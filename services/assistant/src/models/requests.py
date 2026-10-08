"""Request models for the assistant service."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

VideoId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"),
]


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=10000)


class ChatRequest(BaseModel):
    """Request to chat about a video."""

    video_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    message: str = Field(..., min_length=1, max_length=10000)
    conversation_history: list[ChatMessage] = Field(default_factory=list, max_length=50)
    # Single-use token echoed from a `pending_confirmation` SSE event to run
    # the parked destructive/costly action. Only the client controls this —
    # the model can never redeem a token via tool-call arguments.
    confirm_token: str | None = Field(default=None, min_length=16, max_length=128)


ActionName = Literal[
    "save_note",
    "quiz_me",
    "find_moment",
    "explain",
    "generate_video",
    "organize_library",
    "create_folder",
    "rename_folder",
    "move_folder",
    "delete_folder",
    "move_video",
]


class ActionRequest(BaseModel):
    """Request to perform an action on a video.

    ``video_id`` is optional — library-scoped actions (folder management,
    generate_video, organize_library) operate on the user's whole library
    and carry no single video. Video-scoped actions (save_note, quiz_me,
    find_moment, explain) require it; the dispatcher validates per-action.
    """

    video_id: VideoId | None = None
    action: ActionName
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class LibraryVideo(BaseModel):
    """A single video in the user's library inventory (id + title)."""

    video_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(default="", max_length=500)


class LibraryChatRequest(BaseModel):
    """Request to chat across a user's library of saved videos.

    Auth assumption: the caller (Node api gateway) derives ``video_ids``
    server-side from the user's owned videos and trusts the list — same
    pattern as ``/chat`` and ``/library/search``. An EMPTY list is allowed
    (the assistant degrades to a no-context "nothing relevant" reply).
    """

    video_ids: list[VideoId] = Field(default_factory=list, max_length=200)
    message: str = Field(..., min_length=1, max_length=10000)
    conversation_history: list[ChatMessage] = Field(default_factory=list, max_length=50)
    # Owned-video inventory ({video_id, title}) the gateway derives server-side so
    # the assistant can name videos by title and answer "what videos do I have?".
    library: list[LibraryVideo] = Field(default_factory=list, max_length=200)
    # Single-use confirmation token — see ChatRequest.confirm_token.
    confirm_token: str | None = Field(default=None, min_length=16, max_length=128)


class LibrarySearchRequest(BaseModel):
    """Request to semantically search across a library of videos.

    Auth assumption: the caller (Node api gateway) has already verified that
    the requesting user owns or has access to every ``video_id`` in the list.
    The assistant trusts the list — same pattern as ``/chat``.
    """

    video_ids: list[VideoId] = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    sources: list[Literal["transcript", "default_output", "visual"]] | None = None
