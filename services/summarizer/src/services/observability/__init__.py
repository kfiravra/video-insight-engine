"""LLM observability for the summarizer: the shared Langfuse SDK v4 client.

The implementation lives in :mod:`llm_common.observability` (shared with the
assistant); this package binds it to the summarizer's settings and re-exports
the names the pipeline uses, so call sites and test patches target
``src.services.observability.<name>``. Every helper no-ops when
``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` are unset.
"""

from __future__ import annotations

from typing import Any

from llm_common import observability as _shared
from llm_common.observability import (
    current_trace_id,
    error_status,
    fetch_prompt_with_obj,
    finish_generation,
    flush_langfuse,
    generation,
    get_active_prompts,
    is_enabled,
    record_active_prompt,
    redact_pii,
    run_trace,
    score,
    shutdown_langfuse,
    span,
    truncate_payload,
    update_root_metadata,
    usage_details,
)

from src.config import settings


def init_langfuse() -> Any | None:
    """Build the shared client from the summarizer's settings; ``None`` when disabled."""
    return _shared.init_langfuse(settings)


__all__ = [
    "current_trace_id",
    "error_status",
    "fetch_prompt_with_obj",
    "finish_generation",
    "flush_langfuse",
    "generation",
    "get_active_prompts",
    "init_langfuse",
    "is_enabled",
    "record_active_prompt",
    "redact_pii",
    "run_trace",
    "score",
    "shutdown_langfuse",
    "span",
    "truncate_payload",
    "update_root_metadata",
    "usage_details",
]
