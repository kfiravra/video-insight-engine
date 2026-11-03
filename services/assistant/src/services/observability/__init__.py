"""LLM observability for the assistant: the shared Langfuse SDK v4 client.

The implementation lives in :mod:`llm_common.observability` (shared with the
summarizer); this package binds it to the assistant's settings, re-exports the
names the service uses and adds :func:`chat_trace`, the trace of one chat turn.
Every helper no-ops when ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` are unset.
"""

from __future__ import annotations

from typing import Any

from llm_common import observability as _shared
from llm_common.observability import (
    current_trace_id,
    error_status,
    finish_generation,
    flush_langfuse,
    generation,
    is_enabled,
    redact_pii,
    run_trace,
    shutdown_langfuse,
    span,
    truncate_payload,
    usage_details,
)

from src.config import settings
from src.services.observability.traces import chat_trace


def init_langfuse() -> Any | None:
    """Build the shared client from the assistant's settings; ``None`` when disabled."""
    return _shared.init_langfuse(settings)


__all__ = [
    "chat_trace",
    "current_trace_id",
    "error_status",
    "finish_generation",
    "flush_langfuse",
    "generation",
    "init_langfuse",
    "is_enabled",
    "redact_pii",
    "run_trace",
    "shutdown_langfuse",
    "span",
    "truncate_payload",
    "usage_details",
]
