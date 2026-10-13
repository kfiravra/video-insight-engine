"""Live calls of the model A/B (pipeline-1min 4.0d) — one planned call at a time.

Each call is the captured request re-shaped for its arm by 4.0b's request
rules (``request_for_arm`` → ``request_for_model``) and sent the way the
provider sends it: plain ``acompletion``, or — for a request the pipeline
streams (the split extraction's text1 groups) — the provider's own
``stream_completion``, which also gives the time to first token. Nothing of
the pipeline's retry or fallback runs: a refusal, an error or a timeout is a
row of its own (``CallResult``), never re-sent to another model. Cost is
LiteLLM's ``completion_cost`` (the ledger's price source; Haiku 5.5 through
4.0b's map entry); cache reads and writes as ``pipeline.timing`` reads them.

No callbacks are registered (no Langfuse, no usage ledger, no Mongo writes).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import litellm
from _model_ab_cost import PlannedCall
from _model_ab_types import CallResult, request_for_arm

from llm_common.refusal import refusal_of
from src.services.llm_streaming import stream_completion
from src.services.pipeline.pipeline_timing import _cache_tokens

logger = logging.getLogger(__name__)

# Generous on purpose: the pipeline's own timeout is kept in the request and
# compared with the measured latency in the report ("over_timeout").
AB_TIMEOUT_S = 240.0
_STREAM_KEYS = ("stream", "stream_options")


def arm_request(planned: PlannedCall) -> dict[str, Any]:
    """The kwargs ``planned``'s arm sends: re-shaped, unstreamed, the A/B timeout."""
    captured = {k: v for k, v in planned.request.kwargs.items() if k not in _STREAM_KEYS}
    kwargs = request_for_arm(captured, planned.arm)
    kwargs["timeout"] = max(float(kwargs.get("timeout") or 0.0), AB_TIMEOUT_S)
    kwargs["num_retries"] = 0
    return kwargs


def request_sha256(kwargs: dict[str, Any]) -> str:
    """sha256 of request kwargs as sent (canonical JSON): the bytes a row answered."""
    canonical = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _cost(response: Any) -> float:
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception as exc:  # an unpriced model is reported as $0, never fails the row
        logger.warning("No price for %s: %s", getattr(response, "model", "?"), exc)
        return 0.0


def _result(
    planned: PlannedCall, response: Any, latency_ms: int, ttft_ms: int | None, digest: str
) -> CallResult:
    choice = response.choices[0]
    usage = getattr(response, "usage", None)
    cache = _cache_tokens(usage)
    refusal = refusal_of(response)
    return CallResult(
        request_id=planned.request.request_id,
        call_type=planned.request.call_type,
        arm=planned.arm.name,
        model=planned.arm.model,
        effort=planned.arm.effort,
        repeat=planned.repeat,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_tokens=cache["cacheReadTokens"],
        cache_write_tokens=cache["cacheWriteTokens"],
        cost_usd=_cost(response),
        finish_reason=getattr(choice, "finish_reason", None),
        refusal=refusal is not None,
        refusal_category=refusal.category if refusal else None,
        raw=getattr(choice.message, "content", None) or "",
        request_sha256=digest,
    )


async def send(planned: PlannedCall) -> CallResult:
    """One live call; a provider error is recorded on the row."""
    kwargs = arm_request(planned)
    digest = request_sha256(kwargs)
    streamed = bool(planned.request.kwargs.get("stream"))
    started = time.monotonic()
    try:
        if streamed:
            done = await stream_completion(litellm.acompletion, kwargs, lambda: None)
            response, ttft_ms = done.response, done.ttft_ms
        else:
            response, ttft_ms = await litellm.acompletion(**kwargs), None
    except Exception as exc:  # one failed call is one row, never the run
        return CallResult(
            request_id=planned.request.request_id,
            call_type=planned.request.call_type,
            arm=planned.arm.name,
            model=planned.arm.model,
            effort=planned.arm.effort,
            repeat=planned.repeat,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:300],
            request_sha256=digest,
        )
    return _result(planned, response, int((time.monotonic() - started) * 1000), ttft_ms, digest)


Answered = dict[tuple[str, str, int], str | None]


def answered(path: Path) -> Answered:
    """(request, arm, repeat) → the request hash of every row answered without an error."""
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {
        (r["request_id"], r["arm"], r["repeat"]): r.get("request_sha256")
        for r in rows
        if not r.get("error")
    }


def is_answered(done: Answered, call: PlannedCall) -> bool:
    """Answered already, with today's request bytes (an unhashed older row counts)."""
    key = (call.request.request_id, call.arm.name, call.repeat)
    if key not in done:
        return False
    saved = done[key]
    return saved is None or saved == request_sha256(arm_request(call))


async def run_calls(planned: Iterable[PlannedCall], answers_path: Path) -> list[CallResult]:
    """Send every planned call in order, appending each row to ``answers_path`` as it lands.

    A row already answered there with the same request bytes (a rerun after an
    interruption) is not sent again; an errored or changed one is.
    """
    litellm.success_callback, litellm.failure_callback, litellm.callbacks = [], [], []
    done = answered(answers_path)
    results: list[CallResult] = []
    for call in planned:
        key = (call.request.request_id, call.arm.name, call.repeat)
        if is_answered(done, call):
            continue
        result = await send(call)
        with answers_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        status = result.error or f"{result.latency_ms} ms ${result.cost_usd:.4f}"
        print(f"{key[0]} {key[1]}#{key[2]} {result.model}: {status}", file=sys.stderr)
        results.append(result)
    return results


def load_answers(answers_path: Path) -> list[CallResult]:
    """Every saved row; the last row of a (request, arm, repeat) wins."""
    rows: dict[tuple[str, str, int], CallResult] = {}
    for line in answers_path.read_text(encoding="utf-8").splitlines():
        if line:
            result = CallResult(**json.loads(line))
            rows[(result.request_id, result.arm, result.repeat)] = result
    return list(rows.values())
