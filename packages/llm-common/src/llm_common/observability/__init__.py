"""Shared Langfuse SDK v4 observability for the VIE Python services.

Every helper no-ops without ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
(the SDK is not even imported), so services run identically offline and in tests.
Services re-export these names from ``src.services.observability``.
"""

from __future__ import annotations

from llm_common.observability.client import (
    DEFAULT_BASE_URL,
    current_trace_id,
    error_status,
    fetch_prompt_with_obj,
    finish_generation,
    flush_langfuse,
    generation,
    get_active_prompts,
    get_client,
    init_langfuse,
    is_enabled,
    langfuse_environment,
    record_active_prompt,
    run_trace,
    score,
    shutdown_langfuse,
    span,
    update_root_metadata,
    usage_details,
)
from llm_common.observability.ids import resolve_user_id, trace_id_for_run
from llm_common.observability.prompts import PROMPT_MISS_TTL_S
from llm_common.observability.redaction import (
    MAX_PAYLOAD_BYTES,
    normalize_input,
    redact_pii,
    truncate_payload,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "MAX_PAYLOAD_BYTES",
    "PROMPT_MISS_TTL_S",
    "current_trace_id",
    "error_status",
    "fetch_prompt_with_obj",
    "finish_generation",
    "flush_langfuse",
    "generation",
    "get_active_prompts",
    "get_client",
    "init_langfuse",
    "is_enabled",
    "langfuse_environment",
    "normalize_input",
    "record_active_prompt",
    "redact_pii",
    "resolve_user_id",
    "run_trace",
    "score",
    "shutdown_langfuse",
    "span",
    "trace_id_for_run",
    "truncate_payload",
    "update_root_metadata",
    "usage_details",
]
