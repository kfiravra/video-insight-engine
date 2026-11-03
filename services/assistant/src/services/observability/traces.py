"""The Langfuse trace of one assistant chat turn (or ``/action`` dispatch)."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from llm_common.observability import run_trace


def chat_trace(
    *,
    video_id: str | None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AbstractAsyncContextManager[Any | None]:
    """Open the trace of one turn: ``chat:<videoId>``, or ``chat:library`` without a video.

    Turns of one chat share the session ``chat-<sessionId>`` when the caller sent
    ``X-Session-Id``. The user id goes through ``LANGFUSE_USER_ID_MODE`` and is
    never copied into a tag.
    """
    trace_tags = [f"videoId:{video_id}"] if video_id else []
    trace_tags.extend(tags or [])
    return run_trace(
        name=f"chat:{video_id}" if video_id else "chat:library",
        session_id=f"chat-{session_id}" if session_id else None,
        user_id=user_id,
        tags=trace_tags or None,
        metadata=metadata,
        input={"videoId": video_id} if video_id else None,
    )
