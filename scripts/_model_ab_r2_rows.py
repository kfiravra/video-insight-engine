"""Round-2 rows of the model A/B (pipeline-1min 4.0d, D30): both rounds' answers, scored together.

Round 2 numbered its repeats after round 1's (``_model_ab_round2.ArmRun``), so
(request, arm, repeat) names one answer across both rounds and the two sets
are scored as one: arm A's run 0 is round 1's (Haiku 4.5 extraction / memory,
Sonnet 4.6 plan), the plan's ``B-medium`` is round 1's Sonnet 5.5 at medium.
Every row carries the ``round`` it was answered in.

Beyond ``score_rows``' comparison with arm A: each plan answer against
``B-medium``'s run 0 on the same request (``vsMedium_*``), and every pair of
Haiku 4.5 memory runs on a cassette (round 1's run + round 2's two).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from itertools import combinations
from pathlib import Path
from typing import Any

from _model_ab_report import score_rows
from _model_ab_runner import load_answers
from _model_ab_scores import compare_to_a
from _model_ab_types import CallResult

Row = dict[str, Any]
MEDIUM_ARM = "B-medium"
_RESULT_FIELDS = frozenset(f.name for f in fields(CallResult))


def round1_results(round1_json: Path, request_ids: frozenset[str]) -> list[CallResult]:
    """Round 1's answers to the requests round 2 sends (its other call types are left out)."""
    rows = json.loads(round1_json.read_text(encoding="utf-8"))["rows"]
    return [
        CallResult(**{k: v for k, v in row.items() if k in _RESULT_FIELDS})
        for row in rows
        if row["request_id"] in request_ids
    ]


def combined_rows(
    info: Mapping[str, Mapping[str, Any]], round1: Sequence[CallResult], answers: Path
) -> list[Row]:
    """Both rounds' answers scored as one set, each row tagged with its ``round``."""
    round2 = load_answers(answers) if answers.exists() else []
    keys = {(r.request_id, r.arm, r.repeat): 1 for r in round1}
    clash = [k for k in ((r.request_id, r.arm, r.repeat) for r in round2) if k in keys]
    if clash:
        raise ValueError(f"round 2 answered keys round 1 holds: {clash[:3]}")
    keys |= {(r.request_id, r.arm, r.repeat): 2 for r in round2}
    rows = score_rows(info, [*round1, *round2])
    for row in rows:
        row["round"] = keys[(row["request_id"], row["arm"], row["repeat"])]
    _add_vs_medium(rows)
    return rows


def _add_vs_medium(rows: Sequence[Row]) -> None:
    """Each plan answer's agreement with ``B-medium``'s run 0 on the same request."""
    medium = {
        r["request_id"]: r
        for r in rows
        if r["call_type"] == "plan" and r["arm"] == MEDIUM_ARM and r["repeat"] == 0
    }
    for row in rows:
        base = medium.get(row["request_id"])
        row["vsMedium"] = {}
        if row["call_type"] != "plan" or base is None or base is row:
            continue
        if row["score"] and base["score"]:
            compared = compare_to_a("plan", row["score"], base["score"])
            row["vsMedium"] = {k.replace("vsA_", "vsMedium_", 1): v for k, v in compared.items()}


def memory_pairs(rows: Sequence[Row], arm: str = "A") -> list[Row]:
    """Every pair of ``arm``'s memory runs on one cassette: evidence agreement, outline delta."""
    runs: dict[str, list[Row]] = {}
    for row in rows:
        if row["call_type"] == "memory" and row["arm"] == arm and row["score"]:
            runs.setdefault(row["request_id"], []).append(row)
    pairs: list[Row] = []
    for request_id, found in sorted(runs.items()):
        ordered = sorted(found, key=lambda r: r["repeat"])
        for first, second in combinations(ordered, 2):
            compared = compare_to_a("memory", second["score"], first["score"])
            pairs.append(
                {
                    "request_id": request_id,
                    "runs": [
                        f"{first['repeat']} (r{first['round']})",
                        f"{second['repeat']} (r{second['round']})",
                    ],
                    "evidence_agree": compared.get("vsA_evidence_agree"),
                    "outline_delta": compared.get("vsA_outline_delta"),
                    "outline_sections": [
                        first["score"].get("outline_sections"),
                        second["score"].get("outline_sections"),
                    ],
                }
            )
    return pairs


def labelled(rows: Sequence[Row]) -> list[Row]:
    """Copies whose ``arm`` names round 1's arms ``<arm> r1`` (tables keep the rounds apart)."""
    return [{**row, "arm": f"{row['arm']} r1"} if row["round"] == 1 else dict(row) for row in rows]
