"""Raw per-arm tables of the model A/B round 2 (pipeline-1min 4.0d, D30) — before any verdict.

Per call type and arm (round 1's arms as ``<arm> r1``): calls, true median /
p95 latency, TTFT (streamed text1 groups), tokens, cache, cost and cost per
call, then the call type's own metrics as means over the arm's answers
(``vsA_*`` against arm A's run 0 — round 1's — on the same request,
``vsMedium_*`` against round 1's Sonnet 5.5 at medium, ``run_*`` against the
arm's own run 0). Then per-answer detail: extraction item counts per group
and output, each plan answer's tabs, and the memory run pairs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from _model_ab_r2_rows import Row, labelled, memory_pairs
from _model_ab_report import _calls_summary, _metric_lists, _metric_means
from _model_ab_round2 import ROUND2_CALL_TYPES

_METRICS: dict[str, tuple[str, ...]] = {
    "plan": (
        "parsed",
        "fallback",
        "tabs",
        "distinct_sources",
        "duplicate_sources",
        "required_present",
        "vsA_tabs_jaccard",
        "vsA_evidence_agree",
        "vsMedium_tabs_jaccard",
        "vsMedium_evidence_agree",
    ),
    "extraction": (
        "parsed",
        "filled_share",
        "defaulted",
        "dropped",
        "items",
        "answer_lost",
        "vsA_items_ratio",
    ),
    "memory": ("parsed", "outline_valid", "outline_sections", "evidence_keys"),
}


def _cell(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3g}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


def summarize_arms(rows: Sequence[Row]) -> dict[str, dict[str, dict[str, Any]]]:
    """call type → arm label → the calls summary + metric means + noise + flip lists."""
    grouped: dict[tuple[str, str], list[Row]] = {}
    for row in labelled(rows):
        grouped.setdefault((row["call_type"], row["arm"]), []).append(row)
    summary: dict[str, dict[str, dict[str, Any]]] = {}
    for (call_type, arm), found in sorted(grouped.items()):
        entry = _calls_summary(found)
        metrics = _metric_means(found, "score") | _metric_means(found, "vsA")
        entry["metrics"] = metrics | _metric_means(found, "vsMedium")
        entry["noise"] = _metric_means([r for r in found if r["repeat"] > 0], "noise")
        entry["lists"] = _metric_lists(found, "vsA", ("vsA_evidence_flips",))
        entry["lists"] |= _metric_lists(found, "vsMedium", ("vsMedium_evidence_flips",))
        entry["round"] = found[0]["round"]
        entry["input_tokens_mean"] = round(mean(int(r["input_tokens"]) for r in found), 1)
        summary.setdefault(call_type, {})[arm] = entry
    return summary


def _arm_line(arm: str, entry: Mapping[str, Any], metrics: Sequence[str]) -> str:
    calls = entry["calls"]
    ttft = entry["ttft_p50_ms"]
    cells = [
        arm,
        f"`{entry['model'].split('/')[-1]}` {entry['effort']}",
        calls,
        entry["errors"],
        entry["truncated"],
        entry["latency_p50_ms"] / 1000,
        entry["latency_p95_ms"] / 1000,
        ttft / 1000 if ttft is not None else None,
        entry["input_tokens_mean"],
        entry["output_tokens_mean"],
        f"{entry['cache_read_tokens']:,} / {entry['cache_write_tokens']:,}",
        entry["cost_usd"],
        round(entry["cost_usd"] / calls, 5) if calls else None,
        *(entry["metrics"].get(m) for m in metrics),
    ]
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _flip_lines(arms: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = []
    for arm, entry in arms.items():
        for metric, counts in sorted((entry.get("lists") or {}).items()):
            shown = ", ".join(f"{k}×{n}" for k, n in sorted(counts.items()))
            lines.append(f"- {arm} {metric}: {shown}")
    return lines


def call_type_table(call_type: str, arms: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """One call type's per-arm table, then its run-to-run lines and evidence flips."""
    metrics = _METRICS[call_type]
    head = ["arm", "model", "calls", "err", "trunc", "median s", "p95 s", "TTFT median s"]
    head += ["in tok", "out tok", "cache r / w", "cost $", "$ / call", *metrics]
    lines = [f"### {call_type}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += [_arm_line(arm, entry, metrics) for arm, entry in arms.items()]
    lines.append("")
    for arm, entry in arms.items():
        if entry["noise"]:
            shown = ", ".join(f"{k} {_cell(v)}" for k, v in sorted(entry["noise"].items()))
            lines.append(f"- run-to-run {arm} (runs > 0 vs its run 0): {shown}")
    return [*lines, *_flip_lines(arms), ""]


def _output_id(row: Row) -> str:
    return f"{row['arm']}.{row['repeat']}"


def extraction_detail(rows: Sequence[Row]) -> list[str]:
    """Items kept per group and output (filled / requested keys, defaulted, dropped)."""
    lines = ["### extraction items per group (items · filled/requested · defaulted)", ""]
    by_request: dict[str, list[Row]] = {}
    for row in rows:
        if row["call_type"] == "extraction":
            by_request.setdefault(row["request_id"], []).append(row)
    for request_id, found in sorted(by_request.items()):
        cells = []
        for row in sorted(found, key=lambda r: (r["arm"], r["repeat"])):
            score = row["score"] or {}
            if not score.get("parsed"):
                cells.append(f"{_output_id(row)} ✗")
                continue
            cells.append(
                f"{_output_id(row)} {score['items']}·{score['filled']}/{score['requested']}"
                f"·d{score['defaulted']}"
            )
        lines.append(f"- `{request_id.split('/', 1)[1]}`: " + "; ".join(cells))
    return [*lines, ""]


def plan_detail(rows: Sequence[Row]) -> list[str]:
    """Each plan answer: tabs by dataSource, duplicates, required, evidence vs A / medium."""
    head = (
        "| request | answer | round | dataSources | dup | required missing | vs A ev "
        "| vs medium ev |"
    )
    lines = ["### plan answers", "", head, "|---|---|---|---|---|---|---|---|"]
    for row in sorted(
        (r for r in rows if r["call_type"] == "plan"),
        key=lambda r: (r["request_id"], r["arm"], r["repeat"]),
    ):
        score = row["score"] or {}
        lines.append(
            f"| `{row['request_id'].split('/')[1]}` | {_output_id(row)} | {row['round']} | "
            f"{', '.join(score.get('data_sources') or [])} | {score.get('duplicate_sources')} | "
            f"{', '.join(score.get('required_missing') or []) or '–'} | "
            f"{_cell(row['vsA'].get('vsA_evidence_agree'))} | "
            f"{_cell(row['vsMedium'].get('vsMedium_evidence_agree'))} |"
        )
    return [*lines, ""]


def memory_detail(rows: Sequence[Row]) -> list[str]:
    """Haiku 4.5's memory run pairs per cassette (round 1's run + round 2's two)."""
    lines = [
        "### memory run-to-run (Haiku 4.5)",
        "",
        "| cassette | runs | evidence agree | outline delta | outline sections |",
    ]
    lines.append("|---|---|---|---|---|")
    for pair in memory_pairs(rows):
        lines.append(
            f"| `{pair['request_id'].split('/')[1]}` | {' vs '.join(pair['runs'])} | "
            f"{_cell(pair['evidence_agree'])} | {pair['outline_delta']} | "
            f"{pair['outline_sections']} |"
        )
    return [*lines, ""]


def render_raw(rows: Sequence[Row], spend: Mapping[str, Any]) -> str:
    """The raw tables (markdown): spend, per-arm tables per call type, per-answer detail."""
    summary = summarize_arms(rows)
    lines = [
        "# A/B round 2 — raw per-arm tables (no verdicts)",
        "",
        f"Round-2 spend ${spend['total']:.4f} over {spend['calls']} calls · per arm: "
        + ", ".join(f"{k} ${v:.4f}" for k, v in spend["per_arm"].items()),
        "",
    ]
    for call_type in ROUND2_CALL_TYPES:
        if call_type in summary:
            lines += call_type_table(call_type, summary[call_type])
    lines += plan_detail(rows) + extraction_detail(rows) + memory_detail(rows)
    return "\n".join(lines) + "\n"


def spend_of(rows: Sequence[Row], total_usd: float) -> dict[str, Any]:
    """Round 2's spend: ``total_usd`` (every saved row), calls, per (call type, arm)."""
    per_arm: dict[str, float] = {}
    for row in (r for r in rows if r["round"] == 2):
        key = f"{row['call_type']} {row['arm']}"
        per_arm[key] = per_arm.get(key, 0.0) + float(row["cost_usd"])
    return {
        "total": round(total_usd, 4),
        "calls": sum(r["round"] == 2 for r in rows),
        "per_arm": {k: round(v, 4) for k, v in sorted(per_arm.items())},
    }
