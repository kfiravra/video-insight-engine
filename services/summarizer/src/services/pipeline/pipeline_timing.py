"""Per-run pipeline timing — persisted as ``pipeline.timing`` on the cache doc.

One recorder per pipeline run, bound to a ContextVar when the run starts so
deep call sites (LLM provider, retry wrapper, media downloads) record into it
without threading the pipeline context through every signature. Tasks and
``asyncio.to_thread`` workers spawned during the run copy the ContextVar, so
parallel phases and background downloads land in the same recorder. Every
module-level helper is a no-op outside a run (tests, scripts, cache hits).

Offsets (``startMs``/``endMs``) are milliseconds since the run started, so a
timeline can be drawn straight from the document.

Prompt cache per LLM call: ``cacheReadTokens`` = input tokens the provider
served from its cache (Anthropic ``cache_read_input_tokens``, OpenAI
``prompt_tokens_details.cached_tokens``); ``cacheWriteTokens`` = tokens written
(Anthropic only — OpenAI reports no writes), split by lifetime into
``cacheWrite5mTokens`` / ``cacheWrite1hTokens`` when the provider reports it.
``inputTokens`` is the whole prompt, cached tokens included. ``ttftMs`` = time
to the first streamed chunk (the split extraction's warm-up call); null for
every unstreamed call.

Refusals (4.0b): a declined answer is an ``llmFailures`` row ``error: "Refusal"``
(category, tokens, cache tokens, cost — ``costUsd`` counts it); every row of
its one re-send on the previous model is stamped ``refusedModel``. ``counts``:
``refusals`` (calls refused), ``refusalFallbacks`` (answered by the previous
model), ``refusalsUnanswered``; ``fallbacks`` = cross-provider only.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from litellm.cost_calculator import completion_cost
from litellm.exceptions import RateLimitError
from llm_common.context import llm_feature_var, llm_refused_model_var

logger = logging.getLogger(__name__)

# 2: llmCalls carry the 1h/5m cache-write split, and cacheReadTokens counts
# OpenAI cached input too (always 0 for OpenAI calls in version 1).
# 3: llmCalls carry ttftMs (pipeline-1min 3.4).
# 4: refusals — "Refusal" failure rows (tokens, cache tokens, cost), refusedModel
# on the re-send's rows, counts refusals / refusalFallbacks / refusalsUnanswered,
# fallbacks = cross-provider only (pipeline-1min 4.0b).
TIMING_SCHEMA_VERSION = 4
_REFUSAL = "Refusal"
# Stamped on every row of a refused answer's re-send: the model that refused.
_REFUSED = "refusedModel"

# SSE event name → milestone key. First occurrence wins. firstTabReadyMs is
# the first tab_ready of any kind (since 2.1 the provisional Moments tab).
_MILESTONE_EVENTS: dict[str, str] = {
    "metadata": "metadataMs",
    "synthesis_complete": "synthesisCompleteMs",
    "tab_ready": "firstTabReadyMs",
    "complete": "completeMs",
    "done": "doneMs",
}
# The first tab_ready of a tab assembly built (not the provisional preview).
_FIRST_ASSEMBLED_TAB = "firstAssembledTabMs"
_SSE_EVENT_PREFIX = 'data: {"event": "'


def _tab_payload(chunk: str) -> dict[str, Any]:
    """A ``tab_ready`` chunk's payload; empty when it cannot be read."""
    try:
        payload = json.loads(chunk[len("data: ") :])
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _usage_int(usage: object, attr: str) -> int:
    """``usage.attr`` when it is a real int (MagicMock attributes are not)."""
    value = getattr(usage, attr, 0)
    return int(value) if isinstance(value, int) else 0


def _cache_tokens(usage: object) -> dict[str, int]:
    """One call's prompt-cache reads and writes, as LiteLLM's ``Usage`` reports them.

    Anthropic's reads are ``cache_read_input_tokens``; OpenAI's only appear as
    ``prompt_tokens_details.cached_tokens``. The write split by lifetime sits
    in ``prompt_tokens_details.cache_creation_token_details`` (Anthropic's
    ``usage.cache_creation``); without it both split fields stay 0.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    by_ttl = getattr(details, "cache_creation_token_details", None)
    return {
        "cacheReadTokens": _usage_int(usage, "cache_read_input_tokens")
        or _usage_int(details, "cached_tokens"),
        "cacheWriteTokens": _usage_int(usage, "cache_creation_input_tokens"),
        "cacheWrite5mTokens": _usage_int(by_ttl, "ephemeral_5m_input_tokens"),
        "cacheWrite1hTokens": _usage_int(by_ttl, "ephemeral_1h_input_tokens"),
    }


def _model_tail(model: str | None) -> str:
    return (model or "").rsplit("/", 1)[-1]


def is_fallback_response(requested_model: str, response_model: str | None) -> bool:
    """True when the provider answered with a different model than requested.

    A safety net for a silent swap below us (our own cross-provider fallback is
    tagged explicitly by ``call_llm_with_retry``). Providers echo the bare name
    (``claude-sonnet-4-6``) or a dated variant, so the comparison is
    prefix-based on the bare name.
    """
    if not response_model or not isinstance(response_model, str):
        return False
    requested = _model_tail(requested_model)
    answered = _model_tail(response_model)
    return not (answered.startswith(requested) or requested.startswith(answered))


@dataclass
class PipelineTimingRecorder:
    """Collects phase walls, LLM calls, downloads and SSE milestones for a run."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=time.monotonic)
    phases: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_failures: list[dict[str, Any]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    milestones: dict[str, int] = field(default_factory=dict)
    # Distinct tab ids streamed as assembled tabs: a tab re-sent with new
    # content (the overview once synthesis lands, 1d.3) is still one tab, and
    # a provisional tab counts only once a real one is sent under its id.
    emitted_tab_ids: set[str] = field(default_factory=set)

    @property
    def tabs_emitted(self) -> int:
        return len(self.emitted_tab_ids)

    def offset_ms(self, monotonic_ts: float | None = None) -> int:
        ts = time.monotonic() if monotonic_ts is None else monotonic_ts
        return max(0, int((ts - self.started_monotonic) * 1000))

    def wall_time(self, offset_ms: int) -> datetime:
        """Absolute timestamp of an offset — Langfuse spans need datetimes."""
        return self.started_at + timedelta(milliseconds=offset_ms)

    def add_phase(self, name: str, start_monotonic: float, end_monotonic: float) -> None:
        start_ms = self.offset_ms(start_monotonic)
        end_ms = self.offset_ms(end_monotonic)
        self.phases.append(
            {"name": name, "startMs": start_ms, "endMs": end_ms, "wallMs": end_ms - start_ms}
        )

    def observe_sse(self, chunk: str) -> None:
        """Stamp milestones from an outgoing SSE chunk (cheap prefix parse)."""
        if not chunk.startswith(_SSE_EVENT_PREFIX):
            return
        start = len(_SSE_EVENT_PREFIX)
        end = chunk.find('"', start)
        if end < 0:
            return
        event = chunk[start:end]
        if event == "tab_ready":
            self._observe_tab(chunk)
        key = _MILESTONE_EVENTS.get(event)
        if key and key not in self.milestones:
            self.milestones[key] = self.offset_ms()

    def _observe_tab(self, chunk: str) -> None:
        """Count an assembled tab and stamp the first; the provisional preview is neither."""
        payload = _tab_payload(chunk)
        if payload.get("provisional"):
            return
        tab_id = payload.get("id")
        self.emitted_tab_ids.add(str(tab_id) if tab_id is not None else chunk)
        self.milestones.setdefault(_FIRST_ASSEMBLED_TAB, self.offset_ms())

    def _counts(self) -> dict[str, int]:
        return {
            "llmCalls": len(self.llm_calls),
            "llmFailures": len(self.llm_failures),
            "retries": sum(1 for c in self.llm_calls if c.get("attempt", 1) > 1),
            "rateLimited": sum(1 for f in self.llm_failures if f.get("rateLimited")),
            "fallbacks": sum(
                1 for c in self.llm_calls if c.get("fallbackUsed") and _REFUSED not in c
            ),
            **self._refusal_counts(),
            "outputTokens": sum(c.get("outputTokens", 0) for c in self.llm_calls),
            "cacheReadTokens": sum(c.get("cacheReadTokens", 0) for c in self.llm_calls),
            "downloadBytes": sum(d.get("bytes") or 0 for d in self.downloads),
        }

    def _refusal_counts(self) -> dict[str, int]:
        refused = sum(
            1 for f in self.llm_failures if f.get("error") == _REFUSAL and _REFUSED not in f
        )
        answered = sum(1 for c in self.llm_calls if _REFUSED in c)
        return {
            "refusals": refused,
            "refusalFallbacks": answered,
            "refusalsUnanswered": refused - answered,
        }

    def to_document(self, *, tabs_planned: int, tabs_assembled: int) -> dict[str, Any]:
        """Serialize for Mongo (``pipeline.timing``) and trace metadata."""
        return {
            "version": TIMING_SCHEMA_VERSION,
            "startedAt": self.started_at,
            "totalMs": self.offset_ms(),
            "phases": list(self.phases),
            "milestones": dict(self.milestones),
            "llmCalls": list(self.llm_calls),
            "llmFailures": list(self.llm_failures),
            "downloads": list(self.downloads),
            "costUsd": round(
                sum(c.get("costUsd", 0.0) for c in (*self.llm_calls, *self.llm_failures)), 6
            ),
            "counts": {
                **self._counts(),
                "tabsPlanned": tabs_planned,
                "tabsAssembled": tabs_assembled,
                "tabsEmitted": self.tabs_emitted,
            },
        }


_recorder_var: ContextVar[PipelineTimingRecorder | None] = ContextVar(
    "pipeline_timing_recorder", default=None
)


def current_timing() -> PipelineTimingRecorder | None:
    return _recorder_var.get()


def start_run_timing() -> PipelineTimingRecorder:
    """Bind a fresh recorder for the run executing in the current context.

    Set without a reset token on purpose: the runner is an async generator,
    and a token reset from a different context (an ``aclose()`` driven by
    the event loop's finalizer) raises. The next run rebinds; a stale
    recorder outside a run only collects a few dicts nobody persists.
    """
    recorder = PipelineTimingRecorder()
    _recorder_var.set(recorder)
    return recorder


@contextmanager
def timed_step(name: str) -> Iterator[None]:
    """Record a named wall-clock step (phase or sub-step) when a run is bound."""
    start = time.monotonic()
    try:
        yield
    finally:
        recorder = _recorder_var.get()
        if recorder is not None:
            recorder.add_phase(name, start, time.monotonic())


def mark_step(name: str, start_monotonic: float) -> None:
    """Record a sub-step that started at ``start_monotonic`` and ends now."""
    recorder = _recorder_var.get()
    if recorder is not None:
        recorder.add_phase(name, start_monotonic, time.monotonic())


def _call_cost(response: Any) -> float:
    try:
        return float(completion_cost(completion_response=response) or 0.0)
    except Exception as exc:  # noqa: BLE001 — cost lookup is non-critical
        logger.debug("Timing cost lookup failed: %s", exc)
        return 0.0


def record_llm_call(
    *,
    span: str | None,
    model: str,
    response: Any,
    start_monotonic: float,
    latency_ms: int,
    attempt: int = 1,
    fallback_used: bool = False,
    ttft_ms: int | None = None,
    refused_model: str | None = None,
    refusal_category: str | None = None,
) -> None:
    """Record one successful completion. Never raises.

    ``fallback_used`` marks an answer from a fallback model — the cross-provider
    one, or the previous model after ``refused_model`` declined (its category
    in ``refusal_category``); ``model`` is the model that actually answered.
    ``ttft_ms``: time to the first chunk of a streamed call (None otherwise).
    """
    recorder = _recorder_var.get()
    if recorder is None:
        return
    try:
        usage = getattr(response, "usage", None)
        choices = getattr(response, "choices", None) or [None]
        response_model = getattr(response, "model", None)
        recorder.llm_calls.append(
            {
                "feature": llm_feature_var.get(),
                "span": span,
                "model": model,
                "responseModel": response_model if isinstance(response_model, str) else None,
                "startMs": recorder.offset_ms(start_monotonic),
                "wallMs": latency_ms,
                "inputTokens": _usage_int(usage, "prompt_tokens"),
                "outputTokens": _usage_int(usage, "completion_tokens"),
                **_cache_tokens(usage),
                "costUsd": _call_cost(response),
                "attempt": attempt,
                "fallbackUsed": fallback_used or is_fallback_response(model, response_model),
                "finishReason": getattr(choices[0], "finish_reason", None),
                "ttftMs": ttft_ms,
                **_refusal_fields(refused_model, refusal_category),
            }
        )
    except Exception as exc:  # noqa: BLE001 — timing must never break a call
        logger.debug("Timing LLM record skipped (span=%s): %s", span, exc)


def _refusal_fields(refused_model: str | None, category: str | None) -> dict[str, str | None]:
    """An answer to a refused call: the model that refused and its category."""
    if refused_model is None:
        return {}
    return {_REFUSED: refused_model, "refusalCategory": category}


def span_output_tokens(span: str) -> int:
    """Output tokens the run's successful calls under ``span`` produced (0 outside a run)."""
    recorder = _recorder_var.get()
    if recorder is None:
        return 0
    return sum(c.get("outputTokens", 0) for c in recorder.llm_calls if c.get("span") == span)


def record_llm_failure(
    *,
    span: str,
    model: str,
    error: BaseException,
    start_monotonic: float,
    attempt: int,
    refused_model: str | None = None,
) -> None:
    """Record one failed attempt (timeout, 429, provider error). Never raises.

    Stamped ``refusedModel`` during a refusal's re-send (the ContextVar, or ``refused_model``).
    """
    recorder = _recorder_var.get()
    if recorder is None:
        return
    recorder.llm_failures.append(
        {
            "feature": llm_feature_var.get(),
            "span": span,
            "model": model,
            "startMs": recorder.offset_ms(start_monotonic),
            "wallMs": recorder.offset_ms() - recorder.offset_ms(start_monotonic),
            "attempt": attempt,
            "error": type(error).__name__,
            "rateLimited": isinstance(error, RateLimitError),
            **_refused_stamp(refused_model),
        }
    )


def _refused_stamp(refused_model: str | None) -> dict[str, str]:
    refused = refused_model or llm_refused_model_var.get()
    return {_REFUSED: refused} if refused else {}


def record_llm_refusal(
    *,
    span: str,
    model: str,
    response: Any,
    start_monotonic: float,
    attempt: int,
    category: str | None,
    refused_model: str | None = None,
) -> None:
    """Record one declined answer as a failed attempt, with what it was billed. Never raises."""
    recorder = _recorder_var.get()
    if recorder is None:
        return
    usage = getattr(response, "usage", None)
    recorder.llm_failures.append(
        {
            "feature": llm_feature_var.get(),
            "span": span,
            "model": model,
            "startMs": recorder.offset_ms(start_monotonic),
            "wallMs": recorder.offset_ms() - recorder.offset_ms(start_monotonic),
            "attempt": attempt,
            "error": _REFUSAL,
            "rateLimited": False,
            "refusalCategory": category,
            "inputTokens": _usage_int(usage, "prompt_tokens"),
            "outputTokens": _usage_int(usage, "completion_tokens"),
            **_cache_tokens(usage),
            "costUsd": _call_cost(response),
            **_refused_stamp(refused_model),
        }
    )


def record_transcription_call(
    *,
    feature: str,
    provider: str,
    model: str,
    wall_ms: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    error: str | None = None,
) -> None:
    """Record one Whisper/Gemini transcription call. Never raises.

    Those SDKs bypass LiteLLM (and so :func:`record_llm_call`); without this
    hook ``llmCalls``/``costUsd`` silently excluded transcription spend. A
    success lands in ``llmCalls`` with the same keys as a LiteLLM call; a
    failure (``error`` = exception class name; any step of the transcription,
    download included) in ``llmFailures``.
    """
    recorder = _recorder_var.get()
    if recorder is None:
        return
    end_ms = recorder.offset_ms()
    entry: dict[str, Any] = {
        "feature": feature,
        "span": f"transcription:{provider}",
        "model": model,
        "startMs": max(0, end_ms - wall_ms),
        "wallMs": wall_ms,
        "attempt": 1,
    }
    if error is not None:
        recorder.llm_failures.append({**entry, "error": error, "rateLimited": False})
        return
    recorder.llm_calls.append(
        {
            **entry,
            "responseModel": None,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
            "cacheWrite5mTokens": 0,
            "cacheWrite1hTokens": 0,
            "costUsd": cost_usd,
            "fallbackUsed": False,
            "finishReason": None,
            "ttftMs": None,
        }
    )
