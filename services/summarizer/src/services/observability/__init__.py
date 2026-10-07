"""LLM observability layer (Langfuse).

Every helper here no-ops when ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
are unset, so the pipeline runs identically offline and in tests.
"""

from src.services.observability.langfuse_client import (
    fetch_prompt_with_obj,
    flush_langfuse,
    get_active_prompts,
    get_current_trace,
    init_langfuse,
    is_enabled,
    log_generation,
    log_score,
    log_span,
    pipeline_trace,
    record_active_prompt,
    redact_pii,
    truncate_payload,
    update_trace_metadata,
)

__all__ = [
    "fetch_prompt_with_obj",
    "flush_langfuse",
    "get_active_prompts",
    "get_current_trace",
    "init_langfuse",
    "is_enabled",
    "log_generation",
    "log_score",
    "log_span",
    "pipeline_trace",
    "record_active_prompt",
    "redact_pii",
    "truncate_payload",
    "update_trace_metadata",
]
