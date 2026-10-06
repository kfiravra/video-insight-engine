"""Agent tool schemas + dispatcher for the library-chat agentic loop.

Exposes the user's folder/video operations as LLM function-calling tools. The
LLM picks a tool and supplies arguments; :func:`execute_tool` dispatches to the
matching :class:`~src.services.api_client.ApiClient` method, ALWAYS injecting
``user_id`` server-side (never trusting an id from the model). vie-api enforces
ownership and cost gates at the data layer — this module only wires the call.

Ids (``folder_id``/``video_id``) are resolved by the model calling
``list_folders``/``list_videos`` first; ``video_id`` is the userVideo ``id``
field, NOT the YouTube id.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from llm_common.context import llm_feature_var

from src.logging_config import get_logger
from src.tools.library_organizer import LibraryOrganizerTool

if TYPE_CHECKING:
    from src.services.api_client import ApiClient
    from src.services.llm_provider import LLMProvider

logger = get_logger(__name__)

# ─── Tool schemas (OpenAI function-calling format) ──────────────────────────

_ID_SOURCE_NOTE = (
    "ids come from list_folders/list_videos results; video_id is the userVideo "
    "`id` field (NOT the YouTube id). The user owns these — call the list tools "
    "first to resolve ids."
)

AGENT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_folders",
            "description": (
                "List the user's folders (id, name, parentId). Call this to "
                "resolve a folder_id before renaming/moving/deleting a folder "
                "or moving a video into one."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_videos",
            "description": (
                "List the user's saved videos (id, title, folderId). Call this "
                "to resolve a video_id by its TITLE before moving a video. "
                "video_id is the userVideo `id` field, NOT the YouTube id."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": f"Create a new folder for the user. {_ID_SOURCE_NOTE}",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Folder name."},
                    "parent_id": {
                        "type": "string",
                        "description": "Optional parent folder id (nest under it).",
                    },
                    "color": {"type": "string", "description": "Optional color."},
                    "icon": {"type": "string", "description": "Optional icon."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_folder",
            "description": f"Rename an existing folder. {_ID_SOURCE_NOTE}",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string", "description": "Folder id to rename."},
                    "name": {"type": "string", "description": "New folder name."},
                },
                "required": ["folder_id", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_folder",
            "description": (
                "Move a folder under another folder, or to the top level when "
                f"parent_id is omitted. {_ID_SOURCE_NOTE}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string", "description": "Folder id to move."},
                    "parent_id": {
                        "type": "string",
                        "description": "New parent folder id; omit to move to root.",
                    },
                },
                "required": ["folder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_folder",
            "description": (
                "Delete a folder. When delete_content is true its videos are "
                "deleted too (destructive — confirm with the user first). "
                f"{_ID_SOURCE_NOTE}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string", "description": "Folder id to delete."},
                    "delete_content": {
                        "type": "boolean",
                        "description": "Also delete the folder's videos (destructive).",
                    },
                },
                "required": ["folder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_video",
            "description": f"Move a video into a folder. {_ID_SOURCE_NOTE}",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_id": {
                        "type": "string",
                        "description": "userVideo id to move (NOT the YouTube id).",
                    },
                    "folder_id": {"type": "string", "description": "Destination folder id."},
                },
                "required": ["video_id", "folder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": (
                "Start generating a knowledge app from a YouTube URL. This costs "
                "the user money — confirm with the user before calling. Optionally "
                f"drop it in a folder. {_ID_SOURCE_NOTE}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "YouTube video URL."},
                    "folder_id": {
                        "type": "string",
                        "description": "Optional destination folder id.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize_library",
            "description": (
                "Auto-organize the user's whole library into thematic folders and "
                "move videos into them. Use when the user asks to tidy/organize "
                "everything rather than a single move."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    user_id: str,
    api_client: ApiClient,
    llm: LLMProvider,
) -> dict[str, Any]:
    """Dispatch a tool call to the matching api_client method.

    ``user_id`` is ALWAYS injected server-side and never read from *args*.
    Unknown tool names or bad argument shapes return ``{"error": ...}`` so the
    model can self-correct on the next loop iteration instead of crashing.

    Args:
        name: Tool name from the model's function call.
        args: Parsed arguments dict supplied by the model.
        user_id: Authenticated user id (injected into every call).
        api_client: vie-api client for folder/video mutations.
        llm: LLM provider (needed by organize_library).

    Returns:
        A compact result dict (e.g. ``{"ok": True, "folder": {...}}``) or
        ``{"error": "..."}``.
    """
    # Attribute any LLM calls this tool makes (e.g. organize_library) to the
    # specific tool feature, then restore so the agentic loop's own
    # generations stay labelled with the chat feature. Without this, library
    # agentic tools roll up under "assistant:library_chat".
    token = llm_feature_var.set(f"assistant:tool:{name}")
    try:
        return await _dispatch(name, args, user_id=user_id, api_client=api_client, llm=llm)
    except KeyError as exc:
        return {"error": f"missing required argument: {exc}"}
    except (TypeError, ValueError) as exc:
        logger.warning("agent_tool_bad_args", tool=name, error=str(exc))
        return {"error": f"invalid arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 — surface as model-correctable error
        logger.warning("agent_tool_failed", tool=name, error=str(exc))
        return {"error": f"tool {name} failed: {exc}"}
    finally:
        llm_feature_var.reset(token)


_OBJECT_ID = re.compile(r"[0-9a-f]{24}")


async def _resolve_folder_id(folder_ref: str, user_id: str, api_client: ApiClient) -> str:
    """Folder id for a model-supplied folder reference.

    The model sometimes passes the folder's NAME — e.g. when it creates a folder and
    generates into it in the same round, before the create result can give it the id.
    A name that matches one of the user's folders resolves to that folder's id;
    anything else is passed through for the API to accept or reject as before.
    """
    if not folder_ref or _OBJECT_ID.fullmatch(folder_ref):
        return folder_ref
    wanted = folder_ref.strip().casefold()
    for folder in await api_client.list_folders(user_id) or []:
        if isinstance(folder, dict) and str(folder.get("name", "")).strip().casefold() == wanted:
            return str(folder["id"])
    return folder_ref


async def _dispatch(
    name: str,
    args: dict[str, Any],
    *,
    user_id: str,
    api_client: ApiClient,
    llm: LLMProvider,
) -> dict[str, Any]:
    """Route a single tool name to its api_client method (no error wrapping)."""
    if name == "list_folders":
        return {"ok": True, "folders": await api_client.list_folders(user_id)}
    if name == "list_videos":
        return {"ok": True, "videos": await api_client.list_videos(user_id)}
    if name == "create_folder":
        folder = await api_client.create_folder(
            user_id,
            args["name"],
            parentId=args.get("parent_id"),
            color=args.get("color"),
            icon=args.get("icon"),
        )
        return {"ok": True, "folder": folder}
    if name == "rename_folder":
        folder = await api_client.update_folder(user_id, args["folder_id"], name=args["name"])
        return {"ok": True, "folder": folder}
    if name == "move_folder":
        folder = await api_client.move_folder(user_id, args["folder_id"], args.get("parent_id"))
        return {"ok": True, "folder": folder}
    if name == "delete_folder":
        result = await api_client.delete_folder(
            user_id, args["folder_id"], bool(args.get("delete_content", False))
        )
        return {"ok": True, "result": result}
    if name == "move_video":
        folder_id = await _resolve_folder_id(args["folder_id"], user_id, api_client)
        result = await api_client.move_video(user_id, args["video_id"], folder_id)
        return {"ok": True, "result": result}
    if name == "generate_video":
        folder_ref = args.get("folder_id")
        folder_id = (
            await _resolve_folder_id(folder_ref, user_id, api_client)
            if folder_ref is not None
            else None
        )
        result = await api_client.generate_video(user_id, args["url"], folder_id)
        return {"ok": True, "result": result}
    if name == "organize_library":
        result = await LibraryOrganizerTool(api_client, llm).execute({}, {"user_id": user_id})
        return {"ok": True, "result": result}
    return {"error": f"unknown tool: {name}"}


def summarize_tool_result(name: str, res: dict) -> str:
    """Produce a short, human-readable summary of a tool result for the UI.

    The web renders these as muted "✓ step" lines, so they must read like a
    plain action recap — never expose ids or raw payloads.
    """
    if res.get("error"):
        return f"{name} failed"
    if name == "create_folder":
        folder = res.get("folder") or {}
        folder_name = folder.get("name") if isinstance(folder, dict) else None
        return f'Created folder "{folder_name}"' if folder_name else "Created folder"
    if name == "rename_folder":
        folder = res.get("folder") or {}
        folder_name = folder.get("name") if isinstance(folder, dict) else None
        return f'Renamed folder to "{folder_name}"' if folder_name else "Renamed folder"
    if name == "move_folder":
        return "Moved folder"
    if name == "delete_folder":
        return "Deleted folder"
    if name == "move_video":
        return "Moved video"
    if name == "generate_video":
        return "Started video generation"
    if name == "organize_library":
        result = res.get("result") or {}
        if isinstance(result, dict):
            created = result.get("folders_created", 0)
            moved = result.get("videos_moved", 0)
            return f"Organized library: {created} folders, {moved} videos moved"
        return "Organized library"
    if name == "list_folders":
        folders = res.get("folders") or []
        return f"Found {len(folders)} folders"
    if name == "list_videos":
        videos = res.get("videos") or []
        return f"Found {len(videos)} videos"
    return f"Ran {name}"
