"""Langfuse observability client.

This module is the single ingress point for tracing LLM calls. Everything
else in the codebase imports from here (or from :mod:`src.services.observability`).

Design contract:

- Helpers must NEVER raise — failures degrade to debug logs. Observability
  outages must not bring down the pipeline.
- Helpers must NEVER block — Langfuse SDK calls are sync but cheap (in-process
  buffering); we still wrap the network ``flush()`` in :func:`asyncio.to_thread`
  to keep the event loop responsive.
- Helpers must NEVER mutate input — payloads are redacted on copy before
  they leave the process.
- A no-op fallback is used when keys are missing or the SDK is unavailable,
  so tests never need to mock the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, AsyncIterator, Protocol

from src.config import settings

# ``name as name`` = explicit re-export (consumed via ``__init__``); plain
# imports here get stripped as unused by linters.
from src.services.observability._redaction import (
    normalize_input as _normalize_input,
)
from src.services.observability._redaction import (
    redact_pii as redact_pii,
)
from src.services.observability._redaction import (
    truncate_payload as truncate_payload,
)

logger = logging.getLogger(__name__)

# Lazy import — Langfuse is an optional runtime dep. When the SDK isn't
# installed (e.g., trimmed CI image) we still want the rest of the service
# to import cleanly.
try:  # pragma: no cover — exercised by integration runtime, not unit tests
    from langfuse import Langfuse  # type: ignore
except ImportError:
    Langfuse = None  # type: ignore[assignment,misc]


class _LangfuseClient(Protocol):
    """Structural type for the subset of the Langfuse SDK we actually use.

    Captures only the methods called from this module so we can annotate
    ``_client`` with a real type instead of ``Any`` while still tolerating
    the SDK being absent at runtime. The real ``Langfuse`` class satisfies
    this Protocol structurally.
    """

    def trace(self, **kwargs: Any) -> Any: ...
    def get_prompt(self, name: str) -> Any: ...
    def flush(self) -> None: ...
    def create_dataset(self, **kwargs: Any) -> Any: ...
    def create_dataset_item(self, **kwargs: Any) -> Any: ...
    def create_prompt(self, **kwargs: Any) -> Any: ...


# ─── Module state ───────────────────────────────────────────────────────
_client: _LangfuseClient | None = None
_current_trace: ContextVar[Any | None] = ContextVar(
    "langfuse_current_trace",
    default=None,
)
# Per-trace map of {prompt_name: Prompt obj}. Populated by explicit calls
# to :func:`record_active_prompt`; read by :func:`log_generation` so every
# span inside a trace gets the active prompt versions on its metadata
# without callers having to thread them through. Reset on trace entry;
# ``None`` outside a trace means "don't record".
_active_prompts: ContextVar[dict[str, Any] | None] = ContextVar(
    "langfuse_active_prompts",
    default=None,
)
# Most-recently-recorded prompt object. Passed as ``trace.generation(
# prompt=...)`` so the Langfuse UI surfaces the clickable cross-reference
# from generation → prompt version. Latest-wins when a stage loads
# multiple prompts; ``promptVersions`` metadata still records all of them.
_latest_prompt_obj: ContextVar[Any | None] = ContextVar(
    "langfuse_latest_prompt_obj",
    default=None,
)


# ─── Init / teardown ────────────────────────────────────────────────────
def init_langfuse() -> Any | None:
    """Initialize the Langfuse client. Returns ``None`` when disabled.

    Idempotent — safe to call from both lifespan startup and worker boot.
    """
    global _client
    if _client is not None:
        return _client
    if Langfuse is None:
        logger.info("Langfuse SDK not installed — observability disabled")
        return None

    public = getattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    secret = getattr(settings, "LANGFUSE_SECRET_KEY", None)
    if not public or not secret:
        logger.info("Langfuse keys not set — observability disabled")
        return None

    host = getattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    try:
        _client = Langfuse(
            public_key=public,
            secret_key=secret,
            host=host,
        )
        logger.info("Langfuse initialized (host=%s)", host)
    except Exception as exc:  # noqa: BLE001 — defensive; SDK errors must not crash boot
        logger.warning("Langfuse init failed (%s) — observability disabled", exc)
        _client = None
    # Warn when an obviously-weak privacy posture is configured. We don't
    # refuse to start — operators may have legitimate reasons — but the
    # log line surfaces the risk in routine boot logs.
    mode = (getattr(settings, "LANGFUSE_USER_ID_MODE", "identity") or "identity").lower()
    salt = getattr(settings, "LANGFUSE_USER_ID_HASH_SALT", "") or ""
    if mode == "hash" and not salt:
        logger.warning(
            "LANGFUSE_USER_ID_MODE=hash but LANGFUSE_USER_ID_HASH_SALT is empty — "
            "hashed user ids are trivially precomputable from a small id space",
        )
    return _client


def is_enabled() -> bool:
    """``True`` once :func:`init_langfuse` has produced a client."""
    return _client is not None


async def flush_langfuse() -> None:
    """Flush the SDK buffer. Safe to call when disabled."""
    client = _client
    if client is None:
        return
    try:
        await asyncio.to_thread(client.flush)
    except Exception as exc:  # noqa: BLE001 — flush errors are non-fatal
        logger.debug("Langfuse flush failed: %s", exc)


def _reset_for_tests() -> None:
    """Clear module state. ONLY for test fixtures."""
    global _client
    _client = None


# ─── Trace lifecycle ────────────────────────────────────────────────────
def get_current_trace() -> Any | None:
    """Return the trace bound to the current async context, or ``None``."""
    return _current_trace.get()


def get_active_prompts() -> dict[str, str]:
    """Snapshot of {prompt_name: version_str} active in the current trace context.

    Values are stringified versions — when the recorded entry is a Langfuse
    Prompt object the version is read from ``obj.version``; when it's
    already a string it's returned as-is.
    """
    bucket = _active_prompts.get()
    if not bucket:
        return {}
    out: dict[str, str] = {}
    for prompt_name, value in bucket.items():
        if isinstance(value, str):
            out[prompt_name] = value
        else:
            version = getattr(value, "version", None)
            if version is not None:
                out[prompt_name] = str(version)
    return out


def record_active_prompt(name: str, value: Any) -> None:
    """Record a loaded prompt against the active trace.

    ``value`` may be a Langfuse Prompt object (preferred — enables the
    native ``trace.generation(prompt=...)`` link) or a string version.
    No-op outside a trace. Each call also updates ``_latest_prompt_obj``
    when the value carries a Prompt object so the next generation logged
    gets the clickable cross-reference in the Langfuse UI.
    """
    bucket = _active_prompts.get()
    if bucket is None:
        return
    bucket[name] = value
    if hasattr(value, "version") and hasattr(value, "prompt"):
        # Looks like a Langfuse Prompt — keep it as the latest for native linkage.
        _latest_prompt_obj.set(value)


def _resolve_user_id_for_trace(user_id: str | None) -> str | None:
    """Apply the ``LANGFUSE_USER_ID_MODE`` policy before forwarding to the SDK.

    Modes:
      * ``identity`` (default) — pass through as-is. Lets product/support
        debug a specific user's pipeline run in Langfuse.
      * ``hash`` — SHA-256 of ``<salt>::<user_id>`` (truncated to 16 hex
        chars). Lets you correlate spans for the same user without
        exposing the internal id to the third-party trace store.
      * ``omit`` — never send user_id at all. Use when compliance forbids
        any per-user identifier leaving the host.
    """
    if not user_id:
        return None
    mode = (getattr(settings, "LANGFUSE_USER_ID_MODE", "identity") or "identity").lower()
    if mode == "omit":
        return None
    if mode != "hash":
        return user_id
    salt = getattr(settings, "LANGFUSE_USER_ID_HASH_SALT", "") or ""
    digest = hashlib.sha256(f"{salt}::{user_id}".encode()).hexdigest()
    return digest[:16]


@asynccontextmanager
async def pipeline_trace(
    video_id: str,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> AsyncIterator[Any]:
    """Open a parent trace for a pipeline run.

    Yields the Langfuse trace handle (or ``None`` when disabled). On exit,
    the trace is flushed best-effort. The ContextVar is reset even if
    flush fails so nested runs aren't poisoned.
    """
    client = _client
    if client is None:
        yield None
        return

    try:
        trace = client.trace(
            name=name or f"pipeline:{video_id}",
            user_id=_resolve_user_id_for_trace(user_id),
            tags=tags or [],
            metadata=metadata or {},
            input={"videoId": video_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse trace create failed: %s", exc)
        yield None
        return

    trace_token = _current_trace.set(trace)
    prompts_token = _active_prompts.set({})
    latest_token = _latest_prompt_obj.set(None)
    try:
        yield trace
    finally:
        _current_trace.reset(trace_token)
        _active_prompts.reset(prompts_token)
        _latest_prompt_obj.reset(latest_token)
        await flush_langfuse()


def update_trace_metadata(extra: dict[str, Any]) -> None:
    """Merge ``extra`` into the current trace's metadata, best-effort.

    Used by call sites that need to record state discovered AFTER the trace
    opened (e.g., ``cacheHit=True`` once a Redis lookup resolves). No-op
    when no trace is bound. Never raises.
    """
    trace = _current_trace.get()
    if trace is None or not extra:
        return
    try:
        trace.update(metadata=extra)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse trace metadata update failed: %s", exc)


def log_span(
    *,
    name: str,
    start_time: datetime,
    end_time: datetime,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a timed span (a non-LLM pipeline phase) on the current trace.

    No-op without a bound trace. Never raises.
    """
    trace = _current_trace.get()
    if trace is None:
        return
    try:
        trace.span(name=name, start_time=start_time, end_time=end_time, metadata=metadata or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse span log failed (name=%s): %s", name, exc)


# ─── Generation / score logging ─────────────────────────────────────────
def log_generation(
    *,
    name: str,
    model: str,
    input_payload: Any,
    output_payload: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    metadata: dict[str, Any] | None = None,
    level: str = "DEFAULT",
    status_message: str | None = None,
) -> None:
    """Record a generation span on the current pipeline trace.

    No-op when no trace is bound to the current context (e.g., the call
    site is invoked outside a :func:`pipeline_trace`).
    """
    trace = _current_trace.get()
    if trace is None:
        return
    # Latency is folded into metadata — Langfuse v2 generations measure latency
    # from start_time/end_time, but we don't capture those here. Surfacing
    # ``latencyMs`` keeps the number visible without requiring callers to
    # synthesize an end_time/start_time pair just for telemetry.
    enriched_metadata: dict[str, Any] = {**(metadata or {}), "latencyMs": latency_ms}
    active_prompts = get_active_prompts()
    if active_prompts:
        enriched_metadata["promptVersions"] = active_prompts
    # Native Langfuse linkage: when a Prompt object was recorded inside
    # this trace, pass it so the UI surfaces a clickable cross-reference
    # from generation → prompt version. Latest-wins per phase; the full
    # mapping is still in ``promptVersions`` above.
    generation_kwargs: dict[str, Any] = {
        "name": name,
        "model": model,
        "input": _normalize_input(input_payload),
        "output": truncate_payload(output_payload),
        "usage": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
            "unit": "TOKENS",
            "totalCost": cost_usd,
        },
        "metadata": enriched_metadata,
        "level": level,
        "status_message": status_message,
    }
    prompt_obj = _latest_prompt_obj.get()
    if prompt_obj is not None:
        generation_kwargs["prompt"] = prompt_obj
    try:
        trace.generation(**generation_kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse generation log failed (name=%s): %s", name, exc)


def log_score(
    name: str,
    value: float,
    *,
    comment: str | None = None,
    trace: Any | None = None,
) -> None:
    """Attach a score to a trace. Defaults to the current pipeline trace."""
    target = trace if trace is not None else _current_trace.get()
    if target is None:
        return
    try:
        target.score(name=name, value=float(value), comment=comment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse score log failed (name=%s): %s", name, exc)


# ─── Prompt registry ────────────────────────────────────────────────────
def fetch_prompt_with_obj(name: str) -> Any | None:
    """Fetch the full Langfuse Prompt object for ``name``, or ``None``.

    Returning the object (not just its text) lets callers pass it to
    :func:`record_active_prompt` so the generation span gets the native
    Langfuse link to a specific prompt version. The object has at least
    ``.prompt`` (str) and ``.version`` (int) attributes.
    """
    client = _client
    if client is None:
        return None
    try:
        return client.get_prompt(name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse prompt fetch failed (name=%s): %s", name, exc)
        return None
