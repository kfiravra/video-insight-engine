"""Facts the round-2 report states, derived from the data (pipeline-1min 4.0d, D30).

Nothing here is written down by hand: the live run's ceiling and call counts,
whether any OPTIONAL call went out, the prompt-token match between the rounds,
what re-scoring changed in round 1, whether round 2's repeated memory runs
came back identical, and the numbers behind the A+rule visual outlier.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields
from statistics import mean
from typing import Any

from _model_ab_r2_clusters import OUTLIER
from _model_ab_r2_rows import Row
from _model_ab_report import score_rows, summarize
from _model_ab_round2 import ROUND2_RUNS
from _model_ab_types import CallResult

_RESULT_FIELDS = frozenset(f.name for f in fields(CallResult))
_REVIEWED = ("A", "A+rule", "B+rule", "B-medium+rule", "C+rule")


def _video(row: Mapping[str, Any]) -> str:
    return str(row["request_id"]).split("/")[1]


def run_facts(rows: Sequence[Row], live: Mapping[str, Any]) -> dict[str, Any]:
    """Round 2's calls, failures, OPTIONAL calls sent, and the ceiling the live run used."""
    sent = [r for r in rows if r["round"] == 2]
    optional = {
        (run.call_type, run.arm.name, video, repeat)
        for run in ROUND2_RUNS
        if run.optional
        for video in run.videos
        for repeat in range(run.first_repeat, run.first_repeat + run.repeats)
    }
    return {
        "calls": len(sent),
        "errors": sum(bool(r["error"]) for r in sent),
        "refusals": sum(bool(r["refusal"]) for r in sent),
        "truncated": sum(r["finish_reason"] == "length" for r in sent),
        "optional_sent": sum(
            (r["call_type"], r["arm"], _video(r), r["repeat"]) in optional for r in sent
        ),
        "ceiling_usd": live.get("ceiling_usd"),
    }


def token_match(rows: Sequence[Row]) -> dict[str, int]:
    """Requests both rounds sent with the same bytes to the same model (the completeness
    variant changes them: a ``+rule`` arm is its own key), and how many got equal
    provider-counted prompt tokens."""
    seen: dict[tuple[str, str, bool], dict[int, set[int]]] = {}
    for row in rows:
        if not row["error"]:
            key = (row["request_id"], row["model"], "+rule" in str(row["arm"]))
            seen.setdefault(key, {}).setdefault(row["round"], set()).add(int(row["input_tokens"]))
    shared = [v for v in seen.values() if len(v) == 2]
    equal = sum(len(v[1] | v[2]) == 1 for v in shared)
    return {"pairs": len(shared), "equal": equal}


def round1_rerender(payload: Mapping[str, Any]) -> list[str]:
    """Summary fields re-scoring round 1 changes (kept from the first re-render after that)."""
    results = [
        CallResult(**{k: v for k, v in row.items() if k in _RESULT_FIELDS})
        for row in payload["rows"]
    ]
    rescored = summarize(score_rows(payload["requests"], results))
    changed = {
        key
        for call_type, arms in payload["summary"].items()
        for arm, entry in arms.items()
        for key, value in entry.items()
        if rescored[call_type][arm].get(key) != value
    }
    previous = (payload.get("round2") or {}).get("round1_rerender") or []
    return sorted(changed) or list(previous)


def memory_repeats(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Per cassette: round 2's two Haiku 4.5 memory runs — identical answers or not."""
    found: dict[str, list[Row]] = {}
    for row in rows:
        if row["call_type"] == "memory" and row["arm"] == "A" and row["round"] == 2:
            found.setdefault(_video(row), []).append(row)
    return [
        {
            "video_id": video,
            "identical": len({r["raw"] for r in runs}) == 1,
            "output_tokens": [r["output_tokens"] for r in sorted(runs, key=lambda r: r["repeat"])],
        }
        for video, runs in sorted(found.items())
    ]


def outlier_facts(rows: Sequence[Row]) -> dict[str, Any]:
    """The A+rule visual outlier: its answer size vs the other reviewed outputs of that request,
    and A+rule's filled_share with it, without it, and with it counted as empty."""
    on_request = [
        r
        for r in rows
        if r["request_id"] == OUTLIER["request_id"] and r["arm"] in _REVIEWED and not r["error"]
    ]
    is_outlier = [r["arm"] == "A+rule" and r["repeat"] == OUTLIER["repeat"] for r in on_request]
    outlier = next(r for r, hit in zip(on_request, is_outlier, strict=True) if hit)
    others = [
        int(r["output_tokens"]) for r, hit in zip(on_request, is_outlier, strict=True) if not hit
    ]
    ruled = [r for r in rows if r["call_type"] == "extraction" and r["arm"] == "A+rule"]
    shares = [float(r["score"]["filled_share"]) for r in ruled]
    own = float(outlier["score"]["filled_share"])
    return {
        **OUTLIER,
        "output_tokens": int(outlier["output_tokens"]),
        "others_output_tokens": [min(others), max(others)],
        "items": int(outlier["score"]["items"]),
        "filled_share": round(mean(shares), 3),
        "filled_share_without": round((sum(shares) - own) / (len(shares) - 1), 3),
        "filled_share_as_empty": round((sum(shares) - own) / len(shares), 3),
    }
