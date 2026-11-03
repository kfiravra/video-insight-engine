"""Tool registry and structured-action dispatch for the assistant.

Free-form chat never routes through here anymore — the agentic loop
(``AssistantService._run_agentic_loop``) owns tool selection in both chat
modes. This module keeps the name-keyed tool registry and the deterministic
``/action`` dispatch path.
"""

from __future__ import annotations

from typing import Any

from llm_common.context import llm_feature_var

from src.exceptions import ValidationError
from src.logging_config import get_logger
from src.repositories.video_repository import VideoContext
from src.services.observability import span
from src.tools.base import BaseTool

logger = get_logger(__name__)

# Action -> (tool_name, required param keys). Used by /action dispatcher.
ACTION_TO_TOOL: dict[str, tuple[str, tuple[str, ...]]] = {
    "save_note": ("note_taker", ("text",)),
    "quiz_me": ("quiz_generator", ()),
    "find_moment": ("navigator", ("query",)),
    "explain": ("concept_explain", ("concept",)),
    "generate_video": ("video_generator", ("url",)),
    "organize_library": ("library_organizer", ()),
    "create_folder": ("folder_organizer", ("name",)),
    "rename_folder": ("folder_organizer", ("folder_id", "name")),
    "move_folder": ("folder_organizer", ("folder_id",)),
    "delete_folder": ("folder_organizer", ("folder_id",)),
    "move_video": ("folder_organizer", ("video_id",)),
}

# Actions that target a single video and therefore require a ``video_id``.
_VIDEO_SCOPED_ACTIONS = frozenset(
    {
        "save_note",
        "quiz_me",
        "find_moment",
        "explain",
    }
)

# Actions that operate on the user's whole library, not a single video.
_LIBRARY_ACTIONS = frozenset(
    {
        "generate_video",
        "organize_library",
        "create_folder",
        "rename_folder",
        "move_folder",
        "delete_folder",
        "move_video",
    }
)


class ToolRouter:
    """Name-keyed registry of tools available to the action dispatcher."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool under its name."""
        self._tools[tool.name] = tool
        logger.info("tool_registered", tool_name=tool.name)

    def get_tool(self, name: str) -> BaseTool | None:
        """Return a registered tool by name (or None)."""
        return self._tools.get(name)


# Payload keys passed through verbatim to folder/library/generate tools.
_PASS_THROUGH_KEYS = (
    "name",
    "folder_id",
    "parent_id",
    "video_id",
    "url",
    "color",
    "icon",
    "delete_content",
)


def _build_action_params(
    action: str,
    payload: dict[str, Any],
    video_id: str | None,
) -> dict[str, Any]:
    """Map an action's params into the underlying tool's param shape.

    Raises:
        ValidationError: When a required param is missing or empty.
    """
    if action not in ACTION_TO_TOOL:
        raise ValidationError(f"Unknown action: {action}")

    if action in _VIDEO_SCOPED_ACTIONS and not video_id:
        raise ValidationError(f"Action '{action}' requires a video_id")

    _, required_keys = ACTION_TO_TOOL[action]
    for key in required_keys:
        value = payload.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValidationError(f"Action '{action}' requires param '{key}'")

    if action == "save_note":
        params: dict[str, Any] = {"text": payload["text"]}
        if "timestamp" in payload:
            params["timestamp"] = payload["timestamp"]
        return params
    if action == "quiz_me":
        params = {}
        if "topic" in payload:
            params["topic"] = payload["topic"]
        if "num_questions" in payload:
            params["num_questions"] = payload["num_questions"]
        return params
    if action == "find_moment":
        return {"query": payload["query"]}
    if action == "explain":
        return {"concept": payload["concept"], "video_id": video_id}
    if action in _LIBRARY_ACTIONS:
        return {k: payload[k] for k in _PASS_THROUGH_KEYS if k in payload}
    return dict(payload)


class ActionDispatcher:
    """Dispatch /action requests to registered tools.

    Separate from ``ToolRouter`` so chat-intent and action paths can evolve
    independently. Reuses the same ``BaseTool`` protocol.
    """

    def __init__(self, router: ToolRouter) -> None:
        self._router = router

    async def dispatch(
        self,
        action: str,
        video_id: str | None,
        params: dict[str, Any],
        video_ctx: VideoContext | None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the tool matched to ``action``.

        Args:
            action: One of the keys in :data:`ACTION_TO_TOOL`.
            video_id: Target video, or ``None`` for library-scoped actions.
            params: Action-specific parameters (validated per action).
            video_ctx: Loaded video context, or ``None`` when no video is in
                scope (library actions).
            user_id: Caller's user ID (used to attribute notes and scope
                library mutations).

        Returns:
            The tool's result dict.

        Raises:
            ValidationError: Unknown action or missing required params.
            NotFoundError: Tool isn't registered (should not happen in production).
            AppError: Bubbled up from the tool.
        """
        if action not in ACTION_TO_TOOL:
            raise ValidationError(f"Unknown action: {action}")

        tool_name, _ = ACTION_TO_TOOL[action]
        tool = self._router.get_tool(tool_name)
        if tool is None:
            raise ValidationError(f"Tool '{tool_name}' for action '{action}' is not available")

        tool_params = _build_action_params(action, params, video_id)
        context = {
            "video_ctx": video_ctx,
            "video_id": video_id,
            "user_id": user_id,
            "action": action,
        }

        logger.info(
            "action_dispatch",
            action=action,
            tool=tool_name,
            video_id=video_id,
            user_id=user_id,
        )
        # Attribute this tool's LLM calls to the specific tool feature, then
        # restore the previous value so nothing after dispatch is mislabelled.
        token = llm_feature_var.set(f"assistant:tool:{tool_name}")
        try:
            async with span(f"tool:{tool_name}", as_type="tool"):
                return await tool.execute(tool_params, context)
        finally:
            llm_feature_var.reset(token)
