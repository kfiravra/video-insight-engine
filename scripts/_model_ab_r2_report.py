"""The round-2 block of ``gates/g4-model-ab.json`` (pipeline-1min 4.0d, D30), built offline.

Inputs, all in the round-2 cache dir unless named: ``answers.jsonl`` + the
request block of ``dry-run.json`` (identity, Eqa trace check, completeness
insertion), ``live-summary.json`` (run time), round 1's report (its answers to
the same requests), ``review/<vid>.json`` + ``.classified.json`` (the blind
review, unblinded), ``review/blind-key.json`` + ``blind/job*.classified.json``
(the review's size), ``review/REVIEWER-NOTES.unblinded.md`` and the prod
benchmark ledger (the stack costs). The block keeps round 2's rows (each
tagged ``"round": 2``) and its rendered markdown, which the report appends
after round 1's picks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from typing import Any

from _model_ab_capture import CASSETTE_VIDEOS
from _model_ab_r2_clusters import (
    OUTLIER,
    Cassette,
    arm_verdicts,
    dump_rounds,
    field_verdicts,
    keep_rule,
    load_clusters,
    named_check,
    run_rows,
)
from _model_ab_r2_downstream import CODE_PATH, downstream, memory_of
from _model_ab_r2_facts import (
    memory_repeats,
    outlier_facts,
    round1_rerender,
    run_facts,
    token_match,
)
from _model_ab_r2_render import render_round2
from _model_ab_r2_rows import Row, combined_rows, memory_pairs, round1_results
from _model_ab_r2_tables import spend_of, summarize_arms
from _model_ab_report import _calls_summary, _metric_means
from _model_ab_round2 import ROUND2_RUNS
from _model_ab_runner import spent_usd
from _model_ab_stack_cost import stack_table

NOTES_FILE = "REVIEWER-NOTES.unblinded.md"
_REVIEW_ARMS = ("A", "A+rule", "B+rule", "B-medium+rule", "C+rule")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def extraction_calls(rows: Sequence[Row]) -> dict[str, dict[str, Any]]:
    """Per reviewed arm (both rounds' runs together): calls, latency, tokens, cost, fill."""
    found: dict[str, dict[str, Any]] = {}
    for arm in _REVIEW_ARMS:
        mine = [r for r in rows if r["call_type"] == "extraction" and r["arm"] == arm]
        entry = _calls_summary(mine)
        entry["input_tokens_mean"] = round(mean(int(r["input_tokens"]) for r in mine), 1)
        entry["metrics"] = _metric_means(mine, "score")
        found[arm] = entry
    return found


def _cassettes(review: Path) -> list[Cassette]:
    found = []
    for video in CASSETTE_VIDEOS:
        rounds = dump_rounds(review / f"{video}.json")
        clusters = load_clusters(review / f"{video}.classified.json")
        found.append(Cassette(video, clusters, sorted(rounds), rounds=rounds))
    return found


def _without_outlier(cassettes: Sequence[Cassette]) -> Cassette:
    cassette = next(c for c in cassettes if c.video_id == OUTLIER["video"])
    return cassette.without(OUTLIER["output"], OUTLIER["fields"])


def extraction_block(rows: Sequence[Row], review: Path) -> dict[str, Any]:
    cassettes = _cassettes(review)
    runs = [row for cassette in cassettes for row in run_rows(cassette)]
    calls = extraction_calls(rows)
    metrics = {arm: entry["metrics"] for arm, entry in calls.items()}
    cleaned = _without_outlier(cassettes)
    return {
        "calls": calls,
        "arms": arm_verdicts(runs, metrics),
        "runs": runs,
        "keep_rule": [keep_rule(c) for c in cassettes],
        "named_check": named_check(next(c for c in cassettes if c.video_id == "jMq8lEu-of0")),
        "outlier": {
            **outlier_facts(rows),
            "verdicts": field_verdicts(cleaned, OUTLIER["output"], OUTLIER["fields"]),
            "runs_without": run_rows(cleaned),
            "keep_rule_without": keep_rule(cleaned),
        },
    }


def plan_downstream(rows: Sequence[Row], info: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every plan answer through parse → plan-done reconcile → groups.

    The plan-done reconcile reads the cassette's Haiku 4.5 memory (round 2's
    run 1); EqaMrzd9nZU has no memory answer in the A/B, so its second opinion
    is skipped (``memory=None``).
    """
    memory_raw = {
        r["request_id"].split("/")[1]: r["raw"]
        for r in rows
        if r["call_type"] == "memory" and r["arm"] == "A" and r["repeat"] == 1
    }
    found = []
    for row in sorted(
        (r for r in rows if r["call_type"] == "plan" and not r["error"]),
        key=lambda r: (r["request_id"], r["arm"], r["repeat"]),
    ):
        video = row["request_id"].split("/")[1]
        context = info[row["request_id"]]["context"]
        memory = memory_of(memory_raw.get(video), int(context.get("duration") or 0))
        found.append(
            {
                "video_id": video,
                "answer": f"{row['arm']}.{row['repeat']}",
                "memory": "Haiku 4.5 run 1" if memory is not None else "none",
            }
            | downstream(row["raw"], context, memory)
        )
    return found


def stack_block(benchmark: Path, round1_json: Path, answers: Path) -> dict[str, Any]:
    """Stack costs on every answer of both rounds (today's re-price checked against ``usd``)."""
    rows = _read(round1_json)["rows"]
    rows += [json.loads(line) for line in answers.read_text(encoding="utf-8").splitlines() if line]
    return stack_table(benchmark, rows)


def review_size(review: Path) -> dict[str, int]:
    return {
        "jobs": len(list((review / "blind").glob("job*.classified.json"))),
        "items": len(_read(review / "blind-key.json")),
    }


def _facts(rows: Sequence[Row], cache_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    live = _read(cache_dir / "live-summary.json")
    return {
        "run_at": live["run_at"],
        "ceiling_usd": live.get("ceiling_usd"),
        "run": run_facts(rows, live),
        "token_match": token_match(rows),
        "round1_rerender": round1_rerender(payload),
    }


def build_round2(
    cache_dir: Path, round1_json: Path, benchmark: Path, ceiling_note: str = ""
) -> dict[str, Any]:
    """The round-2 block (rows + rendered markdown included)."""
    dry = _read(cache_dir / "dry-run.json")
    info, answers = dry["requests"], cache_dir / "answers.jsonl"
    rows = combined_rows(info, round1_results(round1_json, frozenset(info)), answers)
    summary = summarize_arms(rows)
    block: dict[str, Any] = {
        **_facts(rows, cache_dir, _read(round1_json)),
        "ceiling_note": ceiling_note,
        "estimate_usd": round(sum(e["cost_usd"] for e in dry["estimates"] if not e["optional"]), 4),
        "spend": spend_of(rows, spent_usd(answers)),
        "identity": dry["identity"],
        "eqa_trace_check": dry["eqa_trace_check"],
        "variant": dry["variant"],
        "runs": [asdict(run) for run in ROUND2_RUNS],
        "review": review_size(cache_dir / "review"),
        "extraction": extraction_block(rows, cache_dir / "review"),
        "plan": {
            "arms": summary["plan"],
            "downstream": plan_downstream(rows, info),
            "code_path": CODE_PATH,
        },
        "memory": {
            "pairs": memory_pairs(rows),
            "repeats": memory_repeats(rows),
            "temperature": next(
                (w["wire"].get("temperature") for w in dry["wire"] if w["call_type"] == "memory"),
                None,
            ),
            "haiku55_vs_a": summary["memory"]["B r1"]["metrics"].get("vsA_evidence_agree"),
        },
        "stack_cost": stack_block(benchmark, round1_json, answers),
        "requests": info,
    }
    notes = (cache_dir / "review" / NOTES_FILE).read_text(encoding="utf-8")
    block["markdown"] = render_round2(block, notes)
    block["rows"] = [{**row, "round": 2} for row in rows if row["round"] == 2]
    return block
