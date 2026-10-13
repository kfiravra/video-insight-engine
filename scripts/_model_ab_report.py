"""Scoring, summaries and the markdown table of the model A/B (pipeline-1min 4.0d).

``score_rows`` reads every saved answer with its call type's scorer and adds
the comparison with arm A's answer to the same request (``vsA_*``) and, for a
second run of the same request on the same arm, the comparison with the first
(``run_*`` — the run-to-run noise the A-vs-B differences are judged against).
``summarize`` aggregates per (call type, arm); ``render_markdown`` writes the
gate table (shape of ``g0-tier-probe-ab.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from statistics import mean
from typing import Any

from _model_ab_scores import REPORT_METRICS, compare_to_a, score_answer
from _model_ab_types import CALL_TYPES, CallResult
from tier_probe_ab import percentile

Row = dict[str, Any]
_RUN_PREFIX = "run_"


def _scored(result: CallResult, info: Mapping[str, Any]) -> Row:
    row: Row = asdict(result)
    answered = not result.error and not result.refusal
    row["score"] = score_answer(result.call_type, result.raw, info["context"]) if answered else {}
    row["over_timeout"] = result.latency_ms > 1000 * float(info["pipeline_timeout_s"])
    row["truncated"] = result.finish_reason == "length"
    current = (info.get("arm_sha256") or {}).get(result.arm)
    # A row sent with other request bytes than today's capture (None: not hashed).
    row["stale"] = bool(result.request_sha256 and current and result.request_sha256 != current)
    return row


def _noise(call_type: str, score: Row, first: Row | None) -> Row:
    compared = compare_to_a(call_type, score, first) if first else {}
    return {key.replace("vsA_", _RUN_PREFIX, 1): value for key, value in compared.items()}


def score_rows(
    requests: Mapping[str, Mapping[str, Any]], results: Sequence[CallResult]
) -> list[Row]:
    """Each result scored, compared with arm A (repeat 0) and with its own first run."""
    rows = [_scored(result, requests[result.request_id]) for result in results]
    by_key = {(r["request_id"], r["arm"], r["repeat"]): r for r in rows}
    for row in rows:
        baseline = by_key.get((row["request_id"], "A", 0))
        row["vsA"] = {}
        if row["arm"] != "A" and baseline and row["score"] and baseline["score"]:
            row["vsA"] = compare_to_a(row["call_type"], row["score"], baseline["score"])
        first = by_key.get((row["request_id"], row["arm"], 0))
        row["noise"] = {}
        if row["repeat"] > 0 and first and row["score"] and first["score"]:
            row["noise"] = _noise(row["call_type"], row["score"], first["score"])
    return rows


# ─── Summaries ───────────────────────────────────────────────────────────────


def _metric_means(rows: Sequence[Row], block: str) -> dict[str, float]:
    """Mean of every bool / number a block holds (a bool's mean is its rate)."""
    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in row[block].items():
            if isinstance(value, bool | int | float) and value is not None:
                values.setdefault(key, []).append(float(value))
    return {key: round(mean(found), 3) for key, found in values.items()}


def _metric_lists(
    rows: Sequence[Row], block: str, keys: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Per list metric in ``keys``: how often each item appears (e.g. evidence keys flipped)."""
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        for key in keys:
            value = row[block].get(key)
            if isinstance(value, list):
                per_item = counts.setdefault(key, {})
                for item in value:
                    per_item[str(item)] = per_item.get(str(item), 0) + 1
    return counts


def _calls_summary(rows: Sequence[Row]) -> dict[str, Any]:
    answered = [r for r in rows if not r["error"]]
    latencies = [float(r["latency_ms"]) for r in answered]
    ttfts = [float(r["ttft_ms"]) for r in answered if r["ttft_ms"] is not None]
    return {
        "model": rows[0]["model"],
        "effort": rows[0]["effort"],
        "calls": len(rows),
        "errors": len(rows) - len(answered),
        "refusals": sum(bool(r["refusal"]) for r in rows),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_max_ms": max(latencies, default=0.0),
        "ttft_p50_ms": percentile(ttfts, 50) if ttfts else None,
        "over_timeout": sum(bool(r["over_timeout"]) for r in answered),
        "truncated": sum(bool(r["truncated"]) for r in answered),
        "input_tokens_max": max((int(r["input_tokens"]) for r in answered), default=0),
        "output_tokens_mean": round(mean([r["output_tokens"] for r in answered]), 1)
        if answered
        else 0.0,
        "latency_p95_ms": percentile(latencies, 95),
        # The arm's first call (in send order) already read a cache written before the run.
        "warm_first_call": int(rows[0]["cache_read_tokens"]) > 0,
        "stale_rows": sum(bool(r.get("stale")) for r in rows),
        "cache_read_tokens": sum(int(r["cache_read_tokens"]) for r in answered),
        "cache_write_tokens": sum(int(r["cache_write_tokens"]) for r in answered),
        "cost_usd": round(sum(float(r["cost_usd"]) for r in rows), 5),
    }


def summarize(rows: Sequence[Row]) -> dict[str, dict[str, dict[str, Any]]]:
    """call type → arm → calls, latency, tokens, cost, quality metrics and noise."""
    grouped: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        grouped.setdefault((row["call_type"], row["arm"]), []).append(row)
    summary: dict[str, dict[str, dict[str, Any]]] = {}
    for (call_type, arm), found in sorted(grouped.items()):
        entry = _calls_summary(found)
        entry["metrics"] = _metric_means(found, "score") | _metric_means(found, "vsA")
        entry["noise"] = _metric_means([r for r in found if r["repeat"] > 0], "noise")
        entry["lists"] = _metric_lists(found, "vsA", REPORT_METRICS[call_type])
        summary.setdefault(call_type, {})[arm] = entry
    return summary


def max_prompt_tokens(rows: Sequence[Row]) -> dict[str, dict[str, int]]:
    """call type → model → the largest prompt (provider-counted tokens)."""
    found: dict[str, dict[str, int]] = {}
    for row in rows:
        if not row["error"]:
            per_model = found.setdefault(row["call_type"], {})
            per_model[row["model"]] = max(per_model.get(row["model"], 0), int(row["input_tokens"]))
    return found


# ─── Markdown ────────────────────────────────────────────────────────────────


def _effort_label(model: str, effort: str) -> str:
    """The thinking setting 4.0b's rules give the model (none for today's models)."""
    if model.startswith("anthropic/") and model.endswith("-5-5"):
        return f" effort {effort}"
    return " reasoning none" if model.startswith("openai/gpt-6") else ""


def _cell(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3g}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


def _metric_cell(entry: Mapping[str, Any], metric: str) -> Any:
    if metric in entry["metrics"]:
        return entry["metrics"][metric]
    items = (entry.get("lists") or {}).get(metric)
    return ", ".join(f"{k}×{n}" for k, n in sorted(items.items())) if items else None


def _call_type_table(call_type: str, arms: Mapping[str, Mapping[str, Any]]) -> list[str]:
    metrics = REPORT_METRICS[call_type]
    head = [
        "arm",
        "model",
        "calls",
        "err",
        "refusals",
        "p50 s",
        "TTFT p50 s",
        ">timeout",
        "trunc",
        "out tok",
        "max in tok",
        "cache r / w tok",
        "cost $",
        *metrics,
    ]
    lines = [f"### {call_type}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for arm, entry in arms.items():
        ttft = entry["ttft_p50_ms"]
        cells = [
            f"{arm}†" if entry["warm_first_call"] else arm,
            f"`{entry['model'].split('/')[-1]}`{_effort_label(entry['model'], entry['effort'])}",
            entry["calls"],
            entry["errors"],
            entry["refusals"],
            entry["latency_p50_ms"] / 1000,
            ttft / 1000 if ttft is not None else None,
            entry["over_timeout"],
            entry["truncated"],
            entry["output_tokens_mean"],
            entry["input_tokens_max"],
            f"{entry['cache_read_tokens']:,} / {entry['cache_write_tokens']:,}",
            entry["cost_usd"],
            *(_metric_cell(entry, m) for m in metrics),
        ]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    if any(e["warm_first_call"] for e in arms.values()):
        lines.append("\n† the arm's first call read a cache written before this run (cost is warm)")
    noise = {arm: e["noise"] for arm, e in arms.items() if e["noise"]}
    for arm, values in noise.items():
        shown = ", ".join(f"{k} {_cell(v)}" for k, v in sorted(values.items()))
        lines.append(f"\nRun-to-run ({arm}, second run vs first): {shown}")
    return [*lines, ""]


def _follow_ups(report: Mapping[str, Any]) -> str:
    runs = report.get("runs") or []
    later = []
    for run in runs[1:]:
        filters = " ".join(f"{k}={v}" for k, v in (run.get("filters") or {}).items() if v)
        later.append(f"{run['run_at']} ({filters}, ceiling ${run.get('ceiling_usd', 0):.2f})")
    return f" + follow-ups {'; '.join(later)}" if later else ""


def _picks_lines(picks: Sequence[Mapping[str, str]]) -> list[str]:
    """The decided model per call type, its evidence and the rule step that decided it."""
    lines = [
        "",
        "## Picks",
        "",
        "Rule: quality within noise or better first, then latency, then cost.",
        "",
    ]
    lines += ["| call type | pick | evidence | decided by |", "|---|---|---|---|"]
    for pick in picks:
        lines.append(
            f"| {pick['call_type']} | `{pick['pick']}` | {pick['evidence']} | {pick['rule_step']} |"
        )
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """The gate table: one section per call type, then prompt sizes and spend."""
    summary = report["summary"]
    lines = [
        "# g4 model A/B (4.0d) — per call type",
        "",
        f"Run {report['run_at']}{_follow_ups(report)} · {report['calls']} calls · "
        f"spend ${report['spend_usd']:.3f} "
        f"(estimate ${report['estimate_usd']:.3f}, ceiling ${report['ceiling_usd']:.2f}). "
        "Metrics: bool = rate, number = mean; `vsA_*` = against arm A's answer to the same "
        "request; run-to-run = the same arm twice on one video (the noise floor).",
        "",
    ]
    for call_type in CALL_TYPES:
        if call_type in summary:
            lines += _call_type_table(call_type, summary[call_type])
    lines += [
        "## Max prompt tokens (provider-counted)",
        "",
        "| call type | model | tokens |",
        "|---|---|---|",
    ]
    for call_type, per_model in report["max_prompt_tokens"].items():
        lines += [f"| {call_type} | `{m}` | {t:,} |" for m, t in sorted(per_model.items())]
    projection = report["longest_chunk_projection"]
    lines += [
        "",
        f"Longest chunk (3.7, {projection['chunk_cl100k']:,} cl100k): extraction prompt ≈ "
        f"{projection['total_cl100k']:,} cl100k ≈ {projection['haiku55_tokens']:,} Haiku 5.5 "
        f"tokens vs the {projection['price_tier_tokens']:,}-token price tier.",
    ]
    lines += _picks_lines(report.get("picks") or [])
    return "\n".join(lines) + "\n"
