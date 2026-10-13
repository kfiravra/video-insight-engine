"""Per-video LLM cost of today's v9 vs two candidate model stacks (pipeline-1min 4.0d, D30).

Offline, from the prod benchmark's per-call ledger (``gates/g3-prod-benchmark.json``
``runs[].doc.llm``: span, model, in, out, cr, cw, cw1h, cw5m, usd):

* **today** — every call re-priced with LiteLLM's map (it must match ``usd``);
* **stack 1** — plan + vision Sonnet 5.5, extraction + memory Haiku 5.5 (extraction at
  effort low; ``stack 1 (medium)``: the extraction at medium);
* **stack 2** — plan + vision + extraction Sonnet 5.5, memory Haiku 5.5.

Both stacks carry the committed picks (description + translations gpt-6-luna);
the tier probe, quiz, synthesis, faithfulness and chapter detection keep the
model the ledger shows. A moved call's prompt and cache tokens are scaled by the
A/B's measured provider-counted prompt ratio for its call type (new model vs
old, same requests), its output by the measured output ratio of the arm the
stack uses (plan at medium; extraction the round-2 ``+rule`` arm against
Haiku 4.5 without the rule — the change as the A/B tested it). The cache split
is kept as recorded: on the 5.5 models the 512-token minimum would also cache
the extraction's 1-hour block, which today's Haiku 4.5 (4,096 minimum) folds
into the 5-minute write.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _model_ab_cost import prices
from _model_ab_types import GPT_6_LUNA, HAIKU_55, SONNET_55

BENCHMARK_RUNS: tuple[tuple[str, str], ...] = (
    ("T1dQhQAm8Tc", "r2"),
    ("jMq8lEu-of0", "r2"),
    ("uC45_4nnEAI", "r2"),
    ("EqaMrzd9nZU", "r1"),
)
_SPAN_TYPES: dict[str, str] = {
    "tier_probe": "tier_probe",
    "description_analysis": "description",
    "memory": "memory",
    "plan": "plan",
    "frame_vision": "vision",
    "enrichment": "quiz",
    "faithfulness": "faithfulness",
    "synthesis": "synthesis",
    "chapter_detect": "chapter_detect",
    "translation_full": "translation_output",
    "translation_title": "translation_output",
    "rag_translation": "translation_rag",
}


@dataclass(frozen=True)
class Move:
    """A call type's new model and the A/B arms its ratios are measured on."""

    model: str
    old_arm: str
    new_arm: str


_COMMITTED: dict[str, Move] = {
    "description": Move(GPT_6_LUNA, "A", "B"),
    "translation_output": Move(GPT_6_LUNA, "A", "B"),
    "translation_rag": Move(GPT_6_LUNA, "A", "B"),
}
_SHARED: dict[str, Move] = {
    "plan": Move(SONNET_55, "A", "B-medium"),
    "vision": Move(SONNET_55, "A", "C"),
    "memory": Move(HAIKU_55, "A", "B"),
}
STACKS: dict[str, dict[str, Move]] = {
    "stack 1": {**_COMMITTED, **_SHARED, "extraction": Move(HAIKU_55, "A", "B+rule")},
    # Stack 1 with the extraction at effort medium (B-medium+rule's answer sizes).
    "stack 1 (medium)": {
        **_COMMITTED,
        **_SHARED,
        "extraction": Move(HAIKU_55, "A", "B-medium+rule"),
    },
    "stack 2": {**_COMMITTED, **_SHARED, "extraction": Move(SONNET_55, "A", "C+rule")},
}


def call_type(span: str) -> str:
    return "extraction" if span.startswith("extraction") else _SPAN_TYPES.get(span, span)


# ─── Ratios ──────────────────────────────────────────────────────────────────


def _per_request(rows: Sequence[Mapping[str, Any]], kind: str, arm: str, key: str) -> dict:
    found: dict[str, list[int]] = {}
    for row in rows:
        if row["call_type"] == kind and row["arm"] == arm and not row.get("error"):
            found.setdefault(row["request_id"], []).append(int(row[key]))
    return {request: sum(v) / len(v) for request, v in found.items()}


def ratio(rows: Sequence[Mapping[str, Any]], kind: str, move: Move, key: str) -> float:
    """sum(new) / sum(old) of ``key`` over the requests both arms answered (means per request)."""
    old = _per_request(rows, kind, move.old_arm, key)
    new = _per_request(rows, kind, move.new_arm, key)
    shared = sorted(set(old) & set(new))
    if not shared:
        raise ValueError(f"no request answered by both {move.old_arm} and {move.new_arm} ({kind})")
    return sum(new[r] for r in shared) / sum(old[r] for r in shared)


def ratios(
    rows: Sequence[Mapping[str, Any]], kinds: Iterable[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """stack → call type → {"prompt", "output"} ratios (new vs old), for the moved ``kinds``."""
    wanted = set(kinds)
    return {
        stack: {
            kind: {
                "prompt": round(ratio(rows, kind, move, "input_tokens"), 4),
                "output": round(ratio(rows, kind, move, "output_tokens"), 4),
            }
            for kind, move in moves.items()
            if kind in wanted
        }
        for stack, moves in STACKS.items()
    }


# ─── Re-pricing ──────────────────────────────────────────────────────────────


def reprice(call: Mapping[str, Any], model: str, prompt_ratio: float, output_ratio: float) -> float:
    """One ledger call's cost on ``model`` with its tokens scaled (cache split kept)."""
    cached_1h, written = int(call["cw1h"]), int(call["cw"])
    cached_5m = written - cached_1h
    uncached = int(call["in"]) - int(call["cr"]) - written
    price = prices(model, round(int(call["in"]) * prompt_ratio))
    return (
        prompt_ratio
        * (
            uncached * price.input
            + int(call["cr"]) * price.cache_read
            + cached_5m * price.write_5m
            + cached_1h * price.write_1h
        )
        + int(call["out"]) * output_ratio * price.output
    )


def video_costs(
    calls: Sequence[Mapping[str, Any]], measured: Mapping[str, Mapping[str, Mapping[str, float]]]
) -> dict[str, dict[str, float]]:
    """call type → {"recorded", "today", <stack>…} dollars for one video's ledger."""
    table: dict[str, dict[str, float]] = {}
    for call in calls:
        kind = call_type(str(call["span"]))
        row = table.setdefault(kind, {"recorded": 0.0, "today": 0.0} | dict.fromkeys(STACKS, 0.0))
        today = reprice(call, str(call["model"]), 1.0, 1.0)
        row["recorded"] += float(call["usd"])
        row["today"] += today
        for stack, moves in STACKS.items():
            move = moves.get(kind)
            if move is None:
                row[stack] += today
                continue
            factor = measured[stack][kind]
            row[stack] += reprice(call, move.model, factor["prompt"], factor["output"])
    return table


def benchmark_calls(path: Path) -> dict[str, list[Mapping[str, Any]]]:
    """``<ytid> <label>`` → that benchmark run's ledger calls."""
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    wanted = set(BENCHMARK_RUNS)
    return {
        f"{run['ytid']} {run['label']}": run["doc"]["llm"]
        for run in runs
        if (run["ytid"], run["label"]) in wanted
    }


def _reprice_gap(calls: Sequence[Mapping[str, Any]]) -> float:
    """|today re-priced − the ledger's ``usd``| for one video (the price source check)."""
    today = sum(reprice(c, str(c["model"]), 1.0, 1.0) for c in calls)
    return abs(today - sum(float(c["usd"]) for c in calls))


def stack_table(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per video: per call type and total, today vs each stack; the ratios used."""
    ledgers = benchmark_calls(path)
    kinds = {call_type(str(c["span"])) for calls in ledgers.values() for c in calls}
    measured = ratios(rows, kinds)
    videos = {name: video_costs(calls, measured) for name, calls in ledgers.items()}
    totals = {
        name: {
            col: round(sum(r[col] for r in table.values()), 6) for col in next(iter(table.values()))
        }
        for name, table in videos.items()
    }
    gap = max((_reprice_gap(calls) for calls in ledgers.values()), default=0.0)
    return {"ratios": measured, "videos": videos, "totals": totals, "max_reprice_diff": gap}
