"""The Langfuse trace of one assistant chat turn (or ``/action`` dispatch)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from llm_common.observability import run_trace
from src.models.requests import ChatMessage


def chat_turn(history: Sequence[ChatMessage]) -> int:
    """The 1-based number of the turn being answered: the user messages before it, plus one."""
    return sum(1 for message in history if message.role == "user") + 1


def chat_trace(
    *,
    video_summary_id: str | None,
    youtube_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    turn: int | None = None,
    tags: list[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AbstractAsyncContextManager[Any | None]:
    """Open the trace of one turn: ``chat:<videoSummaryId>``, or ``chat:library`` without a video.

    Turns of one chat share the session ``chat-<sessionId>`` when the caller sent
    ``X-Session-Id`` (D8). Tags carry the ids the pipeline traces use
    (``videoSummaryId:``, ``youtubeId:``, ``requestId:``) plus ``turn:<n>``. The
    user id goes through ``LANGFUSE_USER_ID_MODE`` and is never copied into a tag.
    """
    trace_tags: list[str] = []
    if video_summary_id:
        trace_tags.append(f"videoSummaryId:{video_summary_id}")
    if youtube_id:
        trace_tags.append(f"youtubeId:{youtube_id}")
    if request_id:
        trace_tags.append(f"requestId:{request_id}")
    if turn is not None:
        trace_tags.append(f"turn:{turn}")
    trace_tags.extend(tags or [])
    trace_input = {
        key: value
        for key, value in (("videoSummaryId", video_summary_id), ("youtubeId", youtube_id))
        if value
    }
    return run_trace(
        name=f"chat:{video_summary_id}" if video_summary_id else "chat:library",
        session_id=f"chat-{session_id}" if session_id else None,
        user_id=user_id,
        tags=trace_tags or None,
        metadata=metadata,
        input=trace_input or None,
    )
