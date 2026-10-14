"""Round 2 of the model A/B (pipeline-1min 4.0d, Kfir 2026-10-13, D30): arms, schedule, checks.

* **Arms.** Extraction: ``A+rule`` Haiku 4.5, ``B+rule`` Haiku 5.5 low,
  ``B-medium+rule`` Haiku 5.5 medium, ``C+rule`` Sonnet 5.5 low (thinking
  ``between_tools``) — the completeness rule in every request — ×2 on the three
  cassettes; ``A`` (no rule) once more on jMq8 + uC45 (round 1 has T1dQ ×2,
  jMq8 ×1, uC45 ×1). Plan: ``B-high`` Sonnet 5.5 effort high, thinking off, ×2
  on the cassettes + EqaMrzd9nZU; OPTIONAL ``B-medium`` ×2 on Eqa and once
  more on jMq8 + uC45. Memory: ``A`` Haiku 4.5 ×2 on the cassettes. A run
  numbers its repeats after round 1's (``first_repeat``), so (request, arm,
  repeat) never names two answers across the rounds.
* **Schedule** (sequential, as round 1): the plan arms first, each arm's calls
  back to back after its one 1-hour write; then per cassette each extraction
  arm's repeats back to back (inside the 5-minute window; arm order
  alternating per video), then memory.
* **Checks:** the identity check against round 1's ``prompt_tokens_cl100k``
  (no request hash was kept then) and the completeness variant on every
  extraction request (one place, the same static block per video).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from _model_ab_capture import CASSETTE_VIDEOS
from _model_ab_cost import CallEstimate, PlannedCall, prices, prompt_tokens
from _model_ab_eqa import EQA_VIDEO
from _model_ab_runner import arm_request, request_sha256
from _model_ab_types import HAIKU_45, HAIKU_55, SONNET_55, AbRequest, Arm
from _model_ab_variants import COMPLETENESS, add_completeness_rule, insertion_point

ROUND2_CALL_TYPES: tuple[str, ...] = ("plan", "extraction", "memory")
ROUND2_CEILING_USD = 1.00
_JMQ8, _UC45 = "jMq8lEu-of0", "uC45_4nnEAI"


@dataclass(frozen=True)
class ArmRun:
    """One arm's runs on the requests of ``call_type`` of ``videos``.

    Repeats are numbered ``first_repeat`` … ``first_repeat + repeats - 1``:
    round 1 holds this arm's runs below ``first_repeat``.
    """

    call_type: str
    arm: Arm
    videos: tuple[str, ...]
    repeats: int
    first_repeat: int = 0
    optional: bool = False


ROUND2_RUNS: tuple[ArmRun, ...] = (
    ArmRun("plan", Arm("B-high", SONNET_55, "high"), (*CASSETTE_VIDEOS, EQA_VIDEO), 2),
    ArmRun("plan", Arm("B-medium", SONNET_55, "medium"), (EQA_VIDEO,), 2, optional=True),
    ArmRun("plan", Arm("B-medium", SONNET_55, "medium"), (_JMQ8, _UC45), 1, 1, optional=True),
    ArmRun("extraction", Arm("A+rule", HAIKU_45, variant=COMPLETENESS), CASSETTE_VIDEOS, 2),
    ArmRun("extraction", Arm("B+rule", HAIKU_55, "low", COMPLETENESS), CASSETTE_VIDEOS, 2),
    ArmRun(
        "extraction", Arm("B-medium+rule", HAIKU_55, "medium", COMPLETENESS), CASSETTE_VIDEOS, 2
    ),
    ArmRun("extraction", Arm("C+rule", SONNET_55, "low", COMPLETENESS), CASSETTE_VIDEOS, 2),
    ArmRun("extraction", Arm("A", HAIKU_45), (_JMQ8, _UC45), 1, 1),
    ArmRun("memory", Arm("A", HAIKU_45), CASSETTE_VIDEOS, 2, 1),
)


@dataclass(frozen=True)
class ScheduledCall:
    """A planned call and whether it belongs to the OPTIONAL part of the run."""

    call: PlannedCall
    optional: bool


# ─── Schedule ────────────────────────────────────────────────────────────────


def _calls(run: ArmRun, requests: Sequence[AbRequest], video: str) -> Iterator[ScheduledCall]:
    mine = [r for r in requests if r.call_type == run.call_type and r.video_id == video]
    for repeat in range(run.first_repeat, run.first_repeat + run.repeats):
        for request in mine:
            yield ScheduledCall(PlannedCall(request, run.arm, repeat), run.optional)


def _plan_block(requests: Sequence[AbRequest], runs: Sequence[ArmRun]) -> Iterator[ScheduledCall]:
    for run in (r for r in runs if r.call_type == "plan"):
        for video in run.videos:
            yield from _calls(run, requests, video)


def _video_block(
    requests: Sequence[AbRequest], runs: Sequence[ArmRun], video: str, index: int
) -> Iterator[ScheduledCall]:
    extraction = [r for r in runs if r.call_type == "extraction" and video in r.videos]
    for run in extraction if index % 2 == 0 else extraction[::-1]:
        yield from _calls(run, requests, video)
    for run in (r for r in runs if r.call_type == "memory" and video in r.videos):
        yield from _calls(run, requests, video)


def schedule(
    requests: Sequence[AbRequest], runs: Sequence[ArmRun] = ROUND2_RUNS
) -> list[ScheduledCall]:
    """Every round-2 call in send order (plan arms first, then video by video)."""
    calls = list(_plan_block(requests, runs))
    for index, video in enumerate(CASSETTE_VIDEOS):
        calls += _video_block(requests, runs, video, index)
    return calls


def arms_of(call_type: str, runs: Sequence[ArmRun] = ROUND2_RUNS) -> list[Arm]:
    """The distinct round-2 arms of ``call_type``, in run order."""
    found: dict[str, Arm] = {}
    for run in runs:
        if run.call_type == call_type:
            found.setdefault(run.arm.name, run.arm)
    return list(found.values())


def first_calls(calls: Sequence[ScheduledCall]) -> list[PlannedCall]:
    """Each (call type, arm)'s first scheduled call — the wire check's sample."""
    seen: dict[tuple[str, str], PlannedCall] = {}
    for item in calls:
        key = (item.call.request.call_type, item.call.arm.name)
        seen.setdefault(key, item.call)
    return list(seen.values())


# ─── Checks ──────────────────────────────────────────────────────────────────


def identity_rows(
    requests: Sequence[AbRequest], round1: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """cl100k prompt tokens now vs round 1's stored value, per request round 2 sends."""
    rows: list[dict[str, Any]] = []
    for request in requests:
        stored = (round1.get(request.request_id) or {}).get("prompt_tokens_cl100k")
        now = prompt_tokens(request.kwargs)
        rows.append(
            {
                "request_id": request.request_id,
                "cl100k_now": now,
                "cl100k_round1": stored,
                "equal": stored == now if stored is not None else None,
            }
        )
    return rows


def variant_check(requests: Sequence[AbRequest]) -> dict[str, Any]:
    """The completeness rule on every extraction request: one place, one static block per video."""
    per_video: dict[str, set[str]] = {}
    for request in (r for r in requests if r.call_type == "extraction"):
        changed = add_completeness_rule(request.kwargs)
        per_video.setdefault(request.video_id, set()).add(
            changed["messages"][0]["content"][0]["text"]
        )
    first = next(r for r in requests if r.call_type == "extraction")
    return {
        "requests": sum(r.call_type == "extraction" for r in requests),
        "static_blocks_per_video": {video: len(texts) for video, texts in per_video.items()},
        "insertion": insertion_point(first.kwargs),
    }


def request_info(requests: Sequence[AbRequest]) -> dict[str, dict[str, Any]]:
    """What the report keeps of each request (no prompt bytes) + each round-2 arm's hash."""
    info: dict[str, dict[str, Any]] = {}
    for request in requests:
        arms = [
            run.arm
            for run in ROUND2_RUNS
            if run.call_type == request.call_type and request.video_id in run.videos
        ]
        info[request.request_id] = {
            "call_type": request.call_type,
            "source": request.source,
            "video_id": request.video_id,
            "pipeline_timeout_s": float(request.kwargs.get("timeout") or 0.0),
            "prompt_tokens_cl100k": prompt_tokens(request.kwargs),
            "expected_output_tokens": request.expected_output_tokens,
            "context": request.context,
            "arm_sha256": {
                arm.name: request_sha256(arm_request(PlannedCall(request, arm, 0))) for arm in arms
            },
        }
    return info


# ─── Estimate split ──────────────────────────────────────────────────────────


def output_cost(estimate: CallEstimate) -> float:
    """The output part of an estimate (what a longer answer would change)."""
    return estimate.output_tokens * prices(estimate.model, estimate.prompt_tokens).output


def split_totals(
    calls: Sequence[ScheduledCall], estimates: Sequence[CallEstimate]
) -> dict[tuple[bool, str, str], dict[str, Any]]:
    """(optional, call type, arm) → model, effort, calls, max prompt, cost, output cost."""
    totals: dict[tuple[bool, str, str], dict[str, Any]] = {}
    for item, estimate in zip(calls, estimates, strict=True):
        arm = item.call.arm
        row = totals.setdefault(
            (item.optional, estimate.call_type, arm.name),
            {
                "model": arm.model,
                "effort": arm.effort,
                "variant": arm.variant,
                "calls": 0,
                "max_prompt_tokens": 0,
                "cost_usd": 0.0,
                "output_usd": 0.0,
            },
        )
        row["calls"] += 1
        row["max_prompt_tokens"] = max(row["max_prompt_tokens"], estimate.prompt_tokens)
        row["cost_usd"] += estimate.cost_usd
        row["output_usd"] += output_cost(estimate)
    return totals


@dataclass(frozen=True)
class Measured:
    """Round 1's provider-counted sizes: prompts per (request, model); answers per
    (request, model, effort) and, as a proxy, per (request, model) at any effort."""

    inputs: dict[tuple[str, str], int]
    outputs: dict[tuple[str, str, str], int]
    outputs_any_effort: dict[tuple[str, str], int]


def measured_sizes(rows: Sequence[Mapping[str, Any]]) -> Measured:
    """The largest prompt and answer round 1 measured per key (answered rows only)."""
    measured = Measured({}, {}, {})
    for row in rows:
        if row.get("error"):
            continue
        request, model = str(row["request_id"]), str(row["model"])
        out = int(row["output_tokens"])
        _keep_max(measured.inputs, (request, model), int(row["input_tokens"]))
        _keep_max(measured.outputs, (request, model, str(row["effort"])), out)
        _keep_max(measured.outputs_any_effort, (request, model), out)
    return measured


def _keep_max(found: dict[Any, int], key: Any, value: int) -> None:
    found[key] = max(found.get(key, 0), value)


def calibrated_cost(
    item: ScheduledCall, estimate: CallEstimate, measured: Measured
) -> tuple[float, str]:
    """``estimate`` re-sized by round 1: the prompt part scaled to the provider-counted
    prompt of the same request on the same model, the answer at the size round 1
    measured for the same request + model + effort ("exact"), else at another
    effort ("proxy"), else as estimated ("none")."""
    request, arm = item.call.request.request_id, item.call.arm
    price = prices(estimate.model, estimate.prompt_tokens)
    out_part = estimate.output_tokens * price.output
    in_part = estimate.cost_usd - out_part
    prompt = measured.inputs.get((request, arm.model))
    if prompt and estimate.prompt_tokens:
        in_part *= prompt / estimate.prompt_tokens
    answer = measured.outputs.get((request, arm.model, arm.effort))
    how = "exact"
    if answer is None:
        answer, how = measured.outputs_any_effort.get((request, arm.model)), "proxy"
    if answer is None:
        return in_part + out_part, "none"
    return in_part + answer * price.output, how
