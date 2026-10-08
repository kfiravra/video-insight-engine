"""Response models for the assistant service."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class RAGSource(BaseModel):
    """A retrieved transcript or output chunk used as context."""

    text: str
    video_id: str  # source video — always populated from Qdrant payload
    title: str | None = None  # video title — set in library mode so the chip can label by title
    text_original: str | None = (
        None  # Original-language transcript text (transcript-source only today)
    )
    timestamp: str | None = None  # formatted "M:SS" (or "H:MM:SS") for display
    # Numeric start/end seconds from the payload-v2 chunk timeline — drive the
    # UI seek/deep-link buttons. None for v1-legacy points (no timeline).
    timestamp_seconds: float | None = None
    end_seconds: float | None = None
    score: float = 0.0
    chunk_index: int = 0
    source: str = "transcript"  # "transcript" | "default_output" | "visual"
    tab_id: str | None = None  # set for default_output results
    tab_component: str | None = None  # e.g. "quiz", "overview"
    prop_path: str | None = None  # e.g. "questions[0]"


class ChatEvent(BaseModel):
    """A single SSE event in a chat stream."""

    type: Literal["text", "source", "tool", "tool_result", "error", "done"]
    content: str = ""
    sources: list[RAGSource] | None = None
    metadata: dict[str, Any] | None = None


class ActionResponse(BaseModel):
    """Response from an action request."""

    success: bool
    action: str
    data: dict[str, Any] | None = None
    error: str | None = None
    trace_id: str
