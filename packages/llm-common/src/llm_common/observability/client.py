"""Langfuse SDK v4 client shared by the summarizer and the assistant.

The single ingress point for tracing: services import from here (through their
``src.services.observability`` re-export) and never touch the SDK directly.

Design contract:

- Helpers NEVER raise: SDK failures degrade to debug logs, so a Langfuse outage
  cannot take a pipeline run or a chat turn down.
- Helpers NEVER block: the SDK buffers in process and exports on its own
  thread; the network ``flush()`` / ``shutdown()`` run in ``asyncio.to_thread``.
- Payloads are redacted on copy before they leave the process.
- Without keys (tests, offline dev) or without the SDK installed, every helper
  is a no-op and the SDK is never imported.

Shape of a trace (v4 is OpenTelemetry underneath, the context is a contextvar):
:func:`run_trace` opens the root span and propagates the trace-level attributes
(name, session, user, tags, metadata) to every observation started inside it;
:func:`span` nests a child observation under whatever is current;
:func:`generation` starts a leaf generation the caller finishes with
:func:`finish_generation`. ``asyncio.create_task``, ``gather`` and
``to_thread`` copy the context, so work spawned inside a span nests under it.
Spans and generations are only recorded inside a :func:`run_trace`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager
from contextvars import ContextVar
from types import TracebackType
from typing import Any

from llm_common.observability import prompts as _prompts
from llm_common.observability.ids import normalize_user_id_mode, resolve_user_id
from llm_common.observability.redaction import normalize_input, truncate_payload

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://cloud.langfuse.com"
# ``propagate_attributes`` drops string values longer than this.
MAX_PROPAGATED_CHARS = 200
# Langfuse's environment rule: lowercase, digits, ``-``/``_``, no ``langfuse`` prefix.
_ENVIRONMENT_RE = re.compile(r"^(?!langfuse)[a-z0-9_-]{1,40}$")

# ─── Module state ───────────────────────────────────────────────────────
_client: Any | None = None
_settings: Any | None = None
_propagate: Callable[..., AbstractContextManager[Any]] | None = None
# Set by :func:`run_trace` for the duration of one trace (and inherited by the
# tasks it spawns): the trace id and the root span handle.
_trace_id: ContextVar[str | None] = ContextVar("langfuse_trace_id", default=None)
_root_span: ContextVar[Any | None] = ContextVar("langfuse_root_span", default=None)


# ─── SDK loading ────────────────────────────────────────────────────────
def _load_sdk() -> tuple[Callable[..., Any], Callable[..., AbstractContextManager[Any]]] | None:
    """``(Langfuse, propagate_attributes)``, or ``None`` when the SDK is not installed.

    Imported here, not at module import, so a process without keys never loads
    the SDK and its OpenTelemetry stack.
    """
    try:
        from langfuse import Langfuse, propagate_attributes
    except ImportError:
        return None
    return Langfuse, propagate_attributes


def langfuse_environment(raw: str | None) -> str | None:
    """``ENVIRONMENT`` as a Langfuse environment name, or ``None`` when it cannot be one."""
    value = (raw or "").strip().lower()
    return value if _ENVIRONMENT_RE.match(value) else None


def _warn_weak_user_id_policy(settings: Any) -> None:
    mode = normalize_user_id_mode(getattr(settings, "LANGFUSE_USER_ID_MODE", None))
    salt = getattr(settings, "LANGFUSE_USER_ID_HASH_SALT", "") or ""
    if mode == "hash" and not salt:
        logger.warning(
            "LANGFUSE_USER_ID_MODE=hash but LANGFUSE_USER_ID_HASH_SALT is empty — "
            "hashed user ids are trivially precomputable from a small id space",
        )


# ─── Init / teardown ────────────────────────────────────────────────────
def init_langfuse(settings: Any, *, span_exporter: Any | None = None) -> Any | None:
    """Build the client from a service's settings; ``None`` when disabled.

    Reads ``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``, ``LANGFUSE_BASE_URL``,
    ``ENVIRONMENT`` (→ the Langfuse environment) and, at trace time,
    ``LANGFUSE_USER_ID_MODE`` / ``LANGFUSE_USER_ID_HASH_SALT``. Idempotent.
    ``span_exporter`` replaces the OTLP exporter (tests export to memory).
    """
    global _client, _settings, _propagate
    if _client is not None:
        return _client
    public = getattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    secret = getattr(settings, "LANGFUSE_SECRET_KEY", None)
    if not public or not secret:
        logger.info("Langfuse keys not set — observability disabled")
        return None
    sdk = _load_sdk()
    if sdk is None:
        logger.info("Langfuse SDK not installed — observability disabled")
        return None
    langfuse_cls, propagate = sdk

    base_url = getattr(settings, "LANGFUSE_BASE_URL", None) or DEFAULT_BASE_URL
    environment = langfuse_environment(getattr(settings, "ENVIRONMENT", None))
    kwargs: dict[str, Any] = {"public_key": public, "secret_key": secret, "base_url": base_url}
    if environment:
        kwargs["environment"] = environment
    if span_exporter is not None:
        kwargs["span_exporter"] = span_exporter
    try:
        _client = langfuse_cls(**kwargs)
    except Exception as exc:  # noqa: BLE001 — SDK errors must not crash boot
        logger.warning("Langfuse init failed (%s) — observability disabled", exc)
        _client = None
        return None
    _settings = settings
    _propagate = propagate
    logger.info("Langfuse initialized (base_url=%s, environment=%s)", base_url, environment)
    _warn_weak_user_id_policy(settings)
    return _client


def is_enabled() -> bool:
    """``True`` once :func:`init_langfuse` has produced a client."""
    return _client is not None


def get_client() -> Any | None:
    """The SDK client, for callers that need its public API (scripts, evals)."""
    return _client


async def flush_langfuse() -> None:
    """Export everything buffered so far. Safe to call when disabled."""
    client = _client
    if client is None:
        return
    try:
        await asyncio.to_thread(client.flush)
    except Exception as exc:  # noqa: BLE001 — flush errors are non-fatal
        logger.debug("Langfuse flush failed: %s", exc)


async def shutdown_langfuse() -> None:
    """Flush and stop the SDK's export threads at process exit. Safe when disabled."""
    global _client
    client, _client = _client, None
    if client is None:
        return
    try:
        await asyncio.to_thread(client.shutdown)
    except Exception as exc:  # noqa: BLE001 — shutdown errors are non-fatal
        logger.debug("Langfuse shutdown failed: %s", exc)


def _reset_for_tests() -> None:
    """Clear module state. ONLY for test fixtures."""
    global _client, _settings, _propagate
    _client = None
    _settings = None
    _propagate = None
    _prompts.reset()


# ─── Trace ──────────────────────────────────────────────────────────────
def current_trace_id() -> str | None:
    """The id of the trace open in this context (or inherited from it), else ``None``."""
    return _trace_id.get()


def _resolve_user(user_id: str | None) -> str | None:
    settings = _settings
    return resolve_user_id(
        getattr(settings, "LANGFUSE_USER_ID_MODE", None),
        getattr(settings, "LANGFUSE_USER_ID_HASH_SALT", "") or "",
        user_id,
    )


def _propagatable(metadata: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Trace metadata as ``propagate_attributes`` takes it: strings of ≤200 chars, no ``None``.

    Longer values belong on the root observation (:func:`update_root_metadata`).
    """
    if not metadata:
        return None
    out: dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        text = str(value).lower() if isinstance(value, bool) else str(value)
        if len(text) > MAX_PROPAGATED_CHARS:
            logger.debug("Langfuse trace metadata %s dropped (%d chars)", key, len(text))
            continue
        out[key] = text
    return out or None


@asynccontextmanager
async def run_trace(
    *,
    name: str,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    input: Any | None = None,
    trace_id: str | None = None,
) -> AsyncIterator[Any | None]:
    """Open one trace: a root span named ``name`` plus its propagated attributes.

    Yields the root span handle (``None`` when disabled or when the SDK failed).
    ``trace_id`` pins the trace id (32 hex chars, see ``trace_id_for_run``);
    otherwise the SDK picks a random one. ``user_id`` goes through the
    ``LANGFUSE_USER_ID_MODE`` policy. An exception from the body marks the root
    span as failed and propagates. On exit the buffer is flushed.
    """
    client, propagate = _client, _propagate
    if client is None or propagate is None:
        yield None
        return

    stack = ExitStack()
    try:
        root_kwargs: dict[str, Any] = {"name": name, "as_type": "span", "input": input}
        if trace_id:
            root_kwargs["trace_context"] = {"trace_id": trace_id}
        root = stack.enter_context(client.start_as_current_observation(**root_kwargs))
        stack.enter_context(
            propagate(
                trace_name=name,
                session_id=session_id,
                user_id=_resolve_user(user_id),
                tags=list(tags) if tags else None,
                metadata=_propagatable(metadata),
            )
        )
        root_trace_id = str(root.trace_id)
    except Exception as exc:  # noqa: BLE001
        stack.close()
        logger.warning("Langfuse trace create failed: %s", exc)
        yield None
        return

    trace_token = _trace_id.set(root_trace_id)
    root_token = _root_span.set(root)
    prompt_tokens = _prompts.open_scope()
    try:
        with stack:
            yield root
    finally:
        # A reset from another context (an async generator closed by the
        # loop's finalizer) raises ValueError; that copy is discarded anyway.
        try:
            _trace_id.reset(trace_token)
            _root_span.reset(root_token)
        except ValueError:
            pass
        _prompts.close_scope(prompt_tokens)
        await flush_langfuse()


def update_root_metadata(extra: Mapping[str, Any]) -> None:
    """Merge ``extra`` into the root span's metadata (keys learned after the trace opened).

    Values may be any JSON-serialisable shape. No-op outside a trace. Never raises.
    """
    root = _root_span.get()
    if root is None or not extra:
        return
    try:
        root.update(metadata=dict(extra))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse root metadata update failed: %s", exc)


# ─── Spans ──────────────────────────────────────────────────────────────
class _ObservationScope:
    """A child observation of the current span; works with ``with`` and ``async with``.

    Yields the observation handle, or ``None`` outside a trace or on an SDK error.
    The body's exception marks the observation as failed and propagates.
    """

    def __init__(self, name: str, as_type: str, metadata: Mapping[str, Any] | None) -> None:
        self._name = name
        self._as_type = as_type
        self._metadata = dict(metadata) if metadata else None
        self._cm: AbstractContextManager[Any] | None = None

    def __enter__(self) -> Any | None:
        client = _client
        if client is None or _trace_id.get() is None:
            return None
        try:
            cm = client.start_as_current_observation(
                name=self._name, as_type=self._as_type, metadata=self._metadata
            )
            handle = cm.__enter__()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Langfuse span start failed (name=%s): %s", self._name, exc)
            return None
        self._cm = cm
        return handle

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        cm, self._cm = self._cm, None
        if cm is None:
            return
        try:
            cm.__exit__(exc_type, exc, tb)
        except Exception as end_exc:  # noqa: BLE001 — never mask the body's outcome
            logger.debug("Langfuse span end failed (name=%s): %s", self._name, end_exc)

    async def __aenter__(self) -> Any | None:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, tb)


def span(
    name: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    as_type: str = "span",
) -> _ObservationScope:
    """A child observation around a block: ``with span("phase:plan"):`` or ``async with``.

    ``as_type`` is a Langfuse observation type (``span``, ``tool``, ``agent``, ...).
    """
    return _ObservationScope(name, as_type, metadata)


# ─── Generations ────────────────────────────────────────────────────────
def usage_details(
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict[str, int]:
    """Token usage as Langfuse ``usage_details``: ``input``/``output``/``total`` plus cache keys.

    ``input`` is the provider's prompt-token count as LiteLLM reports it; the cache
    keys break it down and are left out at 0. ``total`` is set explicitly so
    Langfuse does not add the cache keys to it a second time.
    """
    usage = {
        "input": int(input_tokens),
        "output": int(output_tokens),
        "total": int(input_tokens) + int(output_tokens),
    }
    if cache_read_tokens > 0:
        usage["cache_read_input_tokens"] = int(cache_read_tokens)
    if cache_creation_tokens > 0:
        usage["cache_creation_input_tokens"] = int(cache_creation_tokens)
    return usage


def generation(
    name: str,
    *,
    model: str,
    input: Any | None = None,
    model_parameters: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Any | None:
    """Start a generation under the current span; finish it with :func:`finish_generation`.

    Opened just before the provider call so its start/end times are the call's.
    ``input`` is redacted and truncated. Its metadata gains ``promptVersions``
    and it links to the latest recorded Prompt object. ``None`` outside a trace.
    """
    client = _client
    if client is None or _trace_id.get() is None:
        return None
    meta = dict(metadata or {})
    versions = _prompts.get_active_prompts()
    if versions:
        meta["promptVersions"] = versions
    kwargs: dict[str, Any] = {
        "name": name,
        "as_type": "generation",
        "model": model,
        "input": normalize_input(input),
    }
    if meta:
        kwargs["metadata"] = meta
    if model_parameters:
        kwargs["model_parameters"] = dict(model_parameters)
    prompt = _prompts.latest_prompt()
    if prompt is not None:
        kwargs["prompt"] = prompt
    try:
        return client.start_observation(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse generation start failed (name=%s): %s", name, exc)
        return None


def finish_generation(
    handle: Any | None,
    *,
    output: Any | None = None,
    usage: Mapping[str, int] | None = None,
    cost_usd: float | None = None,
    metadata: Mapping[str, Any] | None = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    """Record a generation's outcome and end it. ``None`` handle → no-op. Never raises.

    ``level`` is ``"ERROR"`` for a failed call, ``"WARNING"`` for a cancelled one.
    """
    if handle is None:
        return
    update: dict[str, Any] = {}
    if output is not None:
        update["output"] = truncate_payload(output) if isinstance(output, str) else output
    if usage:
        update["usage_details"] = dict(usage)
    if cost_usd is not None:
        update["cost_details"] = {"total": float(cost_usd)}
    if metadata:
        update["metadata"] = dict(metadata)
    if level:
        update["level"] = level
    if status_message:
        update["status_message"] = status_message
    try:
        if update:
            handle.update(**update)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse generation update failed: %s", exc)
    try:
        handle.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse generation end failed: %s", exc)


def error_status(exc: BaseException) -> str:
    """``"<ErrorClass>: <message>"`` for a generation's ``status_message``, capped."""
    return truncate_payload(f"{type(exc).__name__}: {exc}", max_bytes=500)


# ─── Scores ─────────────────────────────────────────────────────────────
def score(
    name: str,
    value: float,
    *,
    comment: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Attach a numeric score to ``trace_id`` (default: the current trace). Never raises."""
    client = _client
    target = trace_id or _trace_id.get()
    if client is None or target is None:
        return
    try:
        client.create_score(trace_id=target, name=name, value=float(value), comment=comment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse score failed (name=%s): %s", name, exc)


# ─── Prompt registry ────────────────────────────────────────────────────
def fetch_prompt_with_obj(name: str) -> Any | None:
    """The Langfuse Prompt object (``.prompt``, ``.version``) for ``name``, or ``None``.

    Pass the object to :func:`record_active_prompt` so later generations link to
    that version. A miss is not asked for again within ``PROMPT_MISS_TTL_S``.
    """
    return _prompts.fetch_prompt(_client, name)


record_active_prompt = _prompts.record_active_prompt
get_active_prompts = _prompts.get_active_prompts
