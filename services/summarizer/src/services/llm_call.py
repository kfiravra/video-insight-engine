"""What one provider call carries (pipeline-1min 4.0b): the request as its caller
asked for it, each model's answer, the attempt metadata telemetry reads, and how
a refusal reaches the caller.

``LLMProvider`` keeps a call model-agnostic (``ProviderCall``) and shapes it per
model only when it sends it (``llm_model_rules.request_for_model``) — so the
same call can go, once, to a refused model's previous model with that model's
own parameters. A call that ends on a refusal ends there: its deadline turns a
timeout during that one re-send into the refusal (``call_deadline``), and the
retry wrapper hands it to callers that ask (``raise_for_caller``) instead of an
earlier rate-limit error — nobody re-sends it to the model that refused.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from litellm.exceptions import RateLimitError, ServiceUnavailableError

from src.services.llm_model_rules import Effort, LLMRefusalError
from src.services.pipeline.pipeline_timing import record_llm_failure, record_llm_refusal


def attempt_of(span_metadata: dict[str, Any] | None) -> int:
    """Retry attempt number stamped by ``call_llm_with_retry`` (1 when absent)."""
    attempt = (span_metadata or {}).get("attempt", 1)
    return attempt if isinstance(attempt, int) else 1


def is_fallback_attempt(span_metadata: dict[str, Any] | None) -> bool:
    """True when ``call_llm_with_retry`` routed this attempt to the fallback model."""
    return bool((span_metadata or {}).get("fallbackFrom"))


def model_parameters(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Sampling and reasoning parameters actually sent, for Langfuse ``modelParameters``."""
    sent = {key: kwargs[key] for key in ("temperature", "reasoning_effort") if key in kwargs}
    if "thinking" in kwargs:
        sent["thinking"] = kwargs["thinking"]["type"]
    if "output_config" in kwargs:
        sent["effort"] = kwargs["output_config"]["effort"]
    return sent or None


@dataclass(frozen=True)
class ProviderCall:
    """One completion as its caller asked for it; shaped for a model only when sent.

    ``request`` holds the model-agnostic kwargs (model, messages, max_tokens,
    timeout, num_retries, and response_format / metadata when set).
    """

    request: dict[str, Any]
    temperature: float | None
    effort: Effort
    span_name: str | None
    span_metadata: dict[str, Any] | None
    context: str = ""
    on_first_chunk: Callable[[], None] | None = None

    @property
    def attempt(self) -> int:
        return attempt_of(self.span_metadata)

    @property
    def span(self) -> str:
        """The span its timing rows are filed under."""
        return self.span_name or self.context or "unknown"


# LiteLLM num_retries is disabled because retries are handled by
# call_llm_with_retry(), which adds structured logging, per-stage timeouts,
# retry-after-aware backoff and the cross-provider fallback.
_LITELLM_NUM_RETRIES = 0


def base_request(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    timeout: float,
    json_mode: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The model-agnostic kwargs of one completion (``ProviderCall.request``)."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "num_retries": _LITELLM_NUM_RETRIES,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if metadata:
        kwargs["metadata"] = metadata
    return kwargs


@dataclass(frozen=True)
class ModelAnswer:
    """One model's response to a ``ProviderCall`` and how long it took."""

    model: str
    sent: dict[str, Any]
    response: Any
    start_monotonic: float
    latency_ms: int
    ttft_ms: int | None


def record_refused(
    call: ProviderCall, answer: ModelAnswer, category: str | None, refused_model: str | None
) -> None:
    """The refusal row of one declined answer; ``refused_model`` when it was the re-send's."""
    record_llm_refusal(
        span=call.span,
        model=answer.model,
        response=answer.response,
        start_monotonic=answer.start_monotonic,
        attempt=call.attempt,
        category=category,
        refused_model=refused_model,
    )


# ─── A refusal ends the call ─────────────────────────────────────────────────


@dataclass(frozen=True)
class _Resend:
    """A refused answer's one re-send, on its previous model, while it is in flight."""

    call: ProviderCall
    refused_model: str
    category: str | None
    previous_model: str
    started: float


@dataclass
class _Deadline:
    # The deadline this one runs inside (a stage's total cap around a retried call).
    parent: _Deadline | None = None
    resend: _Resend | None = None


_deadline_var: ContextVar[_Deadline | None] = ContextVar("llm_call_deadline", default=None)


def note_refused_resend(
    call: ProviderCall, refused_model: str, category: str | None, previous_model: str
) -> None:
    """Tell every deadline bounding the call that the refusal's re-send is going out."""
    resend = _Resend(call, refused_model, category, previous_model, time.monotonic())
    deadline = _deadline_var.get()
    while deadline is not None:
        deadline.resend = resend
        deadline = deadline.parent


@asynccontextmanager
async def call_deadline(seconds: float) -> AsyncIterator[None]:
    """``asyncio.timeout(seconds)`` around one call, refusal-aware.

    The deadline also bounds a refused answer's re-send. Hitting it there ends
    the call as that refusal (``LLMRefusalError``), its timeout recorded under
    the previous model: a plain timeout would be retried on the refused model.
    Nested deadlines (a stage cap around ``call_llm``'s own) all see the re-send;
    the one that fires records it. One deadline bounds one call: after a re-send
    nothing else is sent under it.
    """
    deadline = _Deadline(parent=_deadline_var.get())
    token = _deadline_var.set(deadline)
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError as e:
        resend = deadline.resend
        if resend is None:
            raise
        record_llm_failure(
            span=resend.call.span,
            model=resend.previous_model,
            error=e,
            start_monotonic=resend.started,
            attempt=resend.call.attempt,
            refused_model=resend.refused_model,
        )
        raise LLMRefusalError(resend.refused_model, resend.category) from e
    finally:
        _deadline_var.reset(token)


def raise_for_caller(
    ended_by: BaseException | None, *, propagate_rate_limit: bool, propagate_refusal: bool
) -> None:
    """Re-raise what ended an unanswered call when its caller asked for that kind of error.

    A refusal is never reported as an earlier rate-limit error: a caller that
    re-sends rate-limited work would send it to the model that refused.
    """
    if isinstance(ended_by, LLMRefusalError):
        if propagate_refusal:
            raise ended_by
        return
    if propagate_rate_limit and isinstance(ended_by, (RateLimitError, ServiceUnavailableError)):
        raise ended_by
