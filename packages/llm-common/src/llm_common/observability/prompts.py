"""Prompt-registry state for the Langfuse client: active prompt versions and the miss cache.

The client module opens a prompt scope per run trace; everything here is
context-local (``ContextVar``) except the miss cache, which is process-wide.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar, Token
from typing import Any

logger = logging.getLogger(__name__)

# ─── Module state ───────────────────────────────────────────────────────
# Per-trace map of {prompt_name: Prompt obj or version string}. Populated by
# :func:`record_active_prompt`, read when a generation opens so its metadata
# carries every active prompt version. ``None`` outside a trace = don't record.
_active_prompts: ContextVar[dict[str, Any] | None] = ContextVar(
    "langfuse_active_prompts",
    default=None,
)
# Most recently recorded Prompt object, passed as ``prompt=`` on the next
# generation so the Langfuse UI links generation → prompt version. Latest wins
# when a stage loads several prompts; ``promptVersions`` still lists them all.
_latest_prompt_obj: ContextVar[Any | None] = ContextVar(
    "langfuse_latest_prompt_obj",
    default=None,
)
# The SDK caches the prompts it found, never a miss: an unregistered name (or an
# unreachable server) would cost every load a synchronous GET, its retries and a
# backoff sleep on the event loop. A miss is remembered this long per name.
PROMPT_MISS_TTL_S = 60.0
# Prompt name → ``time.monotonic()`` of its last miss.
_prompt_misses: dict[str, float] = {}

ScopeTokens = tuple[Token[dict[str, Any] | None], Token[Any | None]]


# ─── Scope ──────────────────────────────────────────────────────────────
def open_scope() -> ScopeTokens:
    """Start recording prompts for a new trace in the current context."""
    return _active_prompts.set({}), _latest_prompt_obj.set(None)


def close_scope(tokens: ScopeTokens) -> None:
    """End the scope :func:`open_scope` started; quiet when the context changed."""
    prompts_token, latest_token = tokens
    # A reset from another context (an async generator closed by the loop's
    # finalizer) raises ValueError; that context's copy is discarded anyway.
    try:
        _active_prompts.reset(prompts_token)
    except ValueError:
        pass
    try:
        _latest_prompt_obj.reset(latest_token)
    except ValueError:
        pass


def reset() -> None:
    """Forget every miss. ONLY for test fixtures."""
    _prompt_misses.clear()


# ─── Active prompts ─────────────────────────────────────────────────────
def get_active_prompts() -> dict[str, str]:
    """Snapshot of ``{prompt_name: version}`` recorded in the current trace context.

    A Prompt object contributes its ``.version``; a string value is used as-is.
    """
    bucket = _active_prompts.get()
    if not bucket:
        return {}
    out: dict[str, str] = {}
    for prompt_name, value in bucket.items():
        if isinstance(value, str):
            out[prompt_name] = value
            continue
        version = getattr(value, "version", None)
        if version is not None:
            out[prompt_name] = str(version)
    return out


def record_active_prompt(name: str, value: Any) -> None:
    """Record a loaded prompt against the active trace; no-op outside one.

    ``value`` is a Langfuse Prompt object (preferred: the next generation links
    to it) or a version string.
    """
    bucket = _active_prompts.get()
    if bucket is None:
        return
    bucket[name] = value
    if hasattr(value, "version") and hasattr(value, "prompt"):
        _latest_prompt_obj.set(value)


def latest_prompt() -> Any | None:
    """The Prompt object the next generation links to, or ``None``."""
    return _latest_prompt_obj.get()


# ─── Registry fetch ─────────────────────────────────────────────────────
def fetch_prompt(client: Any | None, name: str) -> Any | None:
    """The ``production`` Prompt object for ``name`` from ``client``, or ``None``.

    Never raises. A name that missed (not registered, server unreachable) is not
    asked for again within ``PROMPT_MISS_TTL_S``; callers fall back to disk.
    """
    if client is None:
        return None
    missed = _prompt_misses.get(name)
    if missed is not None and time.monotonic() - missed < PROMPT_MISS_TTL_S:
        return None
    try:
        prompt = client.get_prompt(name)
    except Exception as exc:  # noqa: BLE001 — a registry outage must never break a load
        _prompt_misses[name] = time.monotonic()
        logger.debug("Langfuse prompt fetch failed (name=%s): %s", name, exc)
        return None
    _prompt_misses.pop(name, None)
    return prompt
