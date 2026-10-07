#!/usr/bin/env python3
"""Offline tier-probe A/B (pipeline-1min task 0.8): gpt-4o-mini vs Haiku 4.5.

Runs the draft ``tier_probe`` prompt (``scripts/tier_probe_ab_prompt.txt``,
brief Appendix B.1) once per (live golden video, model) at temperature 0 and
max_tokens 80, then scores agreement with the golden ``domain``/``format`` and
the proposed ``has_visual_demo`` labels below, plus latency, cost and
JSON-parse failures.

Inputs are gathered read-only by ``_tier_probe_inputs.py`` and cached under
``--cache-dir`` (never the repo). No pipeline run, no Langfuse, no Mongo
writes: the LLM calls go straight to LiteLLM with no callbacks registered.

Usage (from the repo root, summarizer venv)::

    services/summarizer/.venv/bin/python scripts/tier_probe_ab.py \\
        --cache-dir <scratch> --out-json dev/active/pipeline-1min/gates/g0-tier-probe-ab.json
    ... --dry-run   # gather inputs + render prompts, no LLM calls; with --out-json,
                    # refresh the saved report's inputs block (results untouched)
    ... --out-json <report> --rescore   # re-score the saved answers; nothing external
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from _tier_probe_inputs import (
    GoldenVideo,
    gather_inputs,
    input_sources,
    load_live_golden,
    refresh_report_inputs,
    render_prompt,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = Path(__file__).resolve().parent / "tier_probe_ab_prompt.txt"
ENV_PATH = _REPO_ROOT / ".env"

MODELS: tuple[str, ...] = ("openai/gpt-4o-mini", "anthropic/claude-haiku-4-5-20251001")
MAX_TOKENS = 80
CALL_TIMEOUT_S = 15.0
# Golden rows whose URL no longer matches the entry (reported, scored both ways).
SUSPECT_GOLDEN: frozenset[str] = frozenset({"review-airpods-pro"})
# Below this many calls a nearest-rank p95 is just the max, so it is not reported.
P95_MIN_CALLS = 20
DEFAULT_FORMAT = "commentary"  # classifier.py's fallback for an invalid format

# Mirrors src/services/pipeline/classifier.py (a unit test guards the drift).
VALID_DOMAINS: frozenset[str] = frozenset(
    "learning tech food travel fitness music review project language science "
    "podcast news gaming sport".split()
)
VALID_FORMATS: frozenset[str] = frozenset(
    "tutorial commentary reaction opinion_rant motivational interview lecture vlog "
    "documentary walkthrough podcast news news_commentary entertainment performance "
    "comparison story unboxing".split()
)

# PROPOSED has_visual_demo ground truth (no golden label exists). Keyed by
# golden id: (label, one-line reason). Reviewed in the gate report.
VISUAL_DEMO_LABELS: dict[str, tuple[bool, str]] = {
    "gaming-op17-unboxing": (True, "booster-box opening; the card reveals are the content"),
    "gaming-op13-box-opening": (True, "booster-box opening; the card reveals are the content"),
    "gaming-op17-set-verdict": (False, "talking-head set/market commentary over price charts"),
    "tech-agentic-engineering": (True, "screen-share of an app being built with coding agents"),
    "tech-react-hooks": (True, "hook code written and run on screen"),
    "tech-docker-basics": (True, "commands and Dockerfiles typed on screen"),
    "food-knife-skills": (True, "knife cuts demonstrated on a board"),
    "learning-photosynthesis": (False, "animated explainer; the narration carries the value"),
    "learning-double-slit": (True, "borderline: the slit experiment is run and shown on camera"),
    "science-crispr-intro": (False, "animated explainer; the narration carries the value"),
    "science-black-holes": (False, "animated explainer; the narration carries the value"),
    "travel-vietnam-10day": (True, "places toured on camera"),
    "fitness-pushup-form": (True, "push-up form demonstrated"),
    "review-airpods-pro": (False, "the URL is a 10-hour white-noise black screen, not a review"),
    "food-travel-montreal-vlog": (True, "food and places shown on camera; eating tour"),
    "food-recipe-story-intro": (True, "recipe cooked on camera after the story intro"),
    "gaming-op17-static-camera": (True, "static-camera pack opening; the reveals are the content"),
    "fitness-7min-workout": (True, "do-along workout; exercises demonstrated"),
}


_Sources = dict[str, dict[str, object]]  # per-video provenance block of the report


# ─── Data ────────────────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    """One (video, model) probe call."""

    golden_id: str
    model: str
    latency_ms: int
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    raw: str = ""
    parsed: dict[str, object] | None = None
    parse_error: str | None = None
    call_error: str | None = None
    field_errors: list[str] = field(default_factory=list)


# ─── Pure helpers ────────────────────────────────────────────────────────────


def parse_probe(raw: str) -> tuple[dict[str, object] | None, str | None, list[str]]:
    """Parse a probe response → (normalised fields, hard error, per-field issues).

    The first JSON object wins, as in the pipeline's ``parse_json_response``
    (fences and trailing prose are tolerated; ``is_bare_json`` and
    ``finish_reason`` count them). Only "no JSON object" is a hard error;
    every field is then judged on its own (``normalize_probe``).
    """
    start = raw.find("{")
    if start < 0:
        return None, "no json object", []
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc.msg}", []
    if not isinstance(data, dict):
        return None, "not an object", []
    parsed, issues = normalize_probe(data)
    return parsed, None, issues


def _enum_value(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def normalize_probe(data: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Normalise like ``classifier.py``: strip/lower enums, invalid format →
    commentary, confidence clamped to [0, 1]. An invalid domain (the classifier
    returns None for it) and a non-bool ``has_visual_demo`` become ``None``.
    """
    issues: list[str] = []
    domain: str | None = _enum_value(data.get("domain"))
    if domain not in VALID_DOMAINS:
        issues.append(f"invalid domain {data.get('domain')!r}")
        domain = None
    fmt = _enum_value(data.get("format"))
    if fmt not in VALID_FORMATS:
        issues.append(f"invalid format {data.get('format')!r} -> {DEFAULT_FORMAT}")
        fmt = DEFAULT_FORMAT
    visual = data.get("has_visual_demo")
    if not isinstance(visual, bool):
        issues.append(f"has_visual_demo not a bool: {visual!r}")
        visual = None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        issues.append(f"confidence not a number: {data.get('confidence')!r}")
        confidence = 0.0
    parsed = {"domain": domain, "format": fmt, "has_visual_demo": visual}
    return parsed | {"confidence": confidence}, issues


def is_bare_json(raw: str) -> bool:
    """True when the whole response is one JSON object (no fences, no prose)."""
    try:
        return isinstance(json.loads(raw), dict)
    except json.JSONDecodeError:
        return False


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))
    return ordered[int(rank) - 1]


def dropped_ids(results: list[ProbeResult], golden: list[GoldenVideo]) -> list[str]:
    """Saved rows whose golden id is no longer live (they cannot be scored)."""
    live = {g.golden_id for g in golden}
    return sorted({r.golden_id for r in results} - live)


def summarize(
    results: list[ProbeResult], golden: list[GoldenVideo], exclude: frozenset[str] = frozenset()
) -> dict[str, dict[str, float | None]]:
    """Per-model agreement, parse failures, latency and cost.

    Rows whose golden id is excluded or no longer live are skipped. Each field
    scores on its own. Errored calls keep their elapsed time, so a timeout
    lands in the latency tail.
    """
    by_id = {g.golden_id: g for g in golden if g.golden_id not in exclude}
    summary: dict[str, dict[str, float | None]] = {}
    for model in sorted({r.model for r in results}):
        rows = [r for r in results if r.model == model and r.golden_id in by_id]
        ok = [r for r in rows if r.parsed is not None]
        n = len(rows)
        latencies = [r.latency_ms for r in rows]
        summary[model] = {
            "n": n,
            "domain_pct": _pct(sum(r.parsed["domain"] == by_id[r.golden_id].domain for r in ok), n),
            "format_pct": _pct(sum(r.parsed["format"] == by_id[r.golden_id].format for r in ok), n),
            "visual_pct": _pct(sum(_visual_match(r) for r in ok), n),
            "parse_failures": sum(r.parse_error is not None for r in rows),
            "field_errors": sum(bool(r.field_errors) for r in rows),
            "bare_json": sum(is_bare_json(r.raw) for r in rows),
            "truncated": sum(r.finish_reason == "length" for r in rows),
            "call_errors": sum(r.call_error is not None for r in rows),
            "p50_ms": percentile(latencies, 50),
            "p95_ms": percentile(latencies, 95) if n >= P95_MIN_CALLS else None,
            "max_ms": max(latencies, default=0),
            "over_3s": sum(ms > 3000 for ms in latencies),
            "usd_per_call": round(sum(r.cost_usd for r in rows) / n, 6) if n else 0.0,
            "usd_total": round(sum(r.cost_usd for r in rows), 6),
        }
    return summary


def _pct(hits: int, total: int) -> float:
    return round(100.0 * hits / total, 1) if total else 0.0


def _visual_match(result: ProbeResult) -> bool:
    label = VISUAL_DEMO_LABELS.get(result.golden_id)
    return (
        label is not None
        and result.parsed is not None
        and (result.parsed["has_visual_demo"] is label[0])
    )


def render_summary_table(summary: dict[str, dict[str, float | None]]) -> str:
    """Markdown table for the gate report (p95 shown only from 20 calls up)."""
    lines = [
        "| Model | n | domain % | format % | has_visual_demo % | parse fail | field errors "
        "| bare JSON | hit max_tokens | p50 ms | p95 ms | max ms | > 3 s | $/call |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, s in summary.items():
        p95 = "n/a (n<20)" if s["p95_ms"] is None else f"{s['p95_ms']:.0f}"
        lines.append(
            f"| `{model}` | {s['n']} | {s['domain_pct']} | {s['format_pct']} | {s['visual_pct']} "
            f"| {s['parse_failures']} | {s['field_errors']} | {s['bare_json']}/{s['n']} "
            f"| {s['truncated']} | {s['p50_ms']:.0f} | {p95} | {s['max_ms']:.0f} "
            f"| {s['over_3s']} | {s['usd_per_call']:.5f} |"
        )
    return "\n".join(lines)


# ─── LLM calls ───────────────────────────────────────────────────────────────


def load_api_keys(env_path: Path = ENV_PATH) -> None:
    """Export only the two provider keys from the repo ``.env`` (never printed)."""
    wanted = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in wanted and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


async def call_probe(golden_id: str, model: str, prompt: str) -> ProbeResult:
    """One temperature-0 probe call, timed end to end."""
    import litellm

    started = time.perf_counter()
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=MAX_TOKENS,
            response_format={"type": "json_object"},
            timeout=CALL_TIMEOUT_S,
            num_retries=0,
        )
    except Exception as exc:  # recorded per row and counted in the summary
        elapsed = int((time.perf_counter() - started) * 1000)
        return ProbeResult(
            golden_id, model, elapsed, 0.0, call_error=f"{type(exc).__name__}: {exc}"[:300]
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    raw = response.choices[0].message.content or ""
    parsed, error, issues = parse_probe(raw)
    return ProbeResult(
        golden_id,
        model,
        elapsed,
        float(litellm.completion_cost(completion_response=response)),
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        finish_reason=response.choices[0].finish_reason,
        raw=raw,
        parsed=parsed,
        parse_error=error,
        field_errors=issues,
    )


async def run_ab(prompts: dict[str, str], models: tuple[str, ...]) -> list[ProbeResult]:
    """Sequential calls; model order alternates per video to spread warm-up bias."""
    import litellm

    litellm.success_callback, litellm.failure_callback, litellm.callbacks = [], [], []
    results: list[ProbeResult] = []
    for index, (golden_id, prompt) in enumerate(prompts.items()):
        order = models if index % 2 == 0 else tuple(reversed(models))
        for model in order:
            results.append(await call_probe(golden_id, model, prompt))
            print(f"{golden_id} {model} {results[-1].latency_ms} ms", file=sys.stderr)
    return results


# ─── Entry point ─────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache-dir", type=Path, help="scratch dir for inputs")
    parser.add_argument("--out-json", type=Path, help="raw results + summary JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="gather + render only")
    mode.add_argument("--rescore", action="store_true", help="re-score --out-json offline")
    args = parser.parse_args(argv)
    if args.rescore and args.out_json is None:
        parser.error("--rescore needs --out-json (the saved report to re-score)")
    if not args.rescore and args.cache_dir is None:
        parser.error("--cache-dir is required unless --rescore")
    return args


def load_results(rows: list[dict[str, object]]) -> list[ProbeResult]:
    """Rebuild saved rows and re-parse each raw answer with today's parser."""
    results = [ProbeResult(**row) for row in rows]
    for result in results:
        if result.call_error is None:
            parsed, error, issues = parse_probe(result.raw)
            result.parsed, result.parse_error, result.field_errors = parsed, error, issues
    return results


def build_summaries(
    results: list[ProbeResult], golden: list[GoldenVideo]
) -> dict[str, dict[str, dict[str, float | None]]]:
    """All live rows, and the same without the suspect golden rows."""
    return {
        "all": summarize(results, golden),
        "excludingSuspectGolden": summarize(results, golden, SUSPECT_GOLDEN),
    }


def _write_report(
    path: Path, sources: _Sources, results: list[ProbeResult], golden: list[GoldenVideo]
) -> None:
    labels = {
        gid: {"has_visual_demo": v, "reason": r, "status": "proposed"}
        for gid, (v, r) in VISUAL_DEMO_LABELS.items()
    }
    payload = {
        "task": "pipeline-1min 0.8",
        "models": list(MODELS),
        "maxTokens": MAX_TOKENS,
        "temperature": 0,
        "suspectGolden": sorted(SUSPECT_GOLDEN),
        "droppedRows": dropped_ids(results, golden),
        "summary": build_summaries(results, golden),
        "inputs": sources,
        "visualDemoLabels": labels,
        "results": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _finish(
    out_json: Path | None, sources: _Sources, results: list[ProbeResult], golden: list[GoldenVideo]
) -> int:
    """Print the tables, name unscorable rows, and write the report."""
    for name, summary in build_summaries(results, golden).items():
        print(f"\n{name}\n{render_summary_table(summary)}")
    dropped = dropped_ids(results, golden)
    if dropped:
        print(f"dropped (no longer live golden): {', '.join(dropped)}", file=sys.stderr)
    if out_json:
        _write_report(out_json, sources, results, golden)
    return 0


def _rescore(out_json: Path, golden: list[GoldenVideo]) -> int:
    """Re-score the saved answers with the saved inputs block — nothing external."""
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    return _finish(out_json, payload["inputs"], load_results(payload["results"]), golden)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    golden = load_live_golden()
    if args.rescore:
        return _rescore(args.out_json, golden)
    inputs = gather_inputs(golden, args.cache_dir)
    template = PROMPT_PATH.read_text(encoding="utf-8")
    prompts = {gid: render_prompt(template, item) for gid, item in inputs.items()}
    if args.dry_run:
        for gid, prompt in prompts.items():
            print(f"{gid}: {len(prompt)} chars, transcript={inputs[gid].transcript_source}")
        if args.out_json and args.out_json.exists():
            refresh_report_inputs(args.out_json, input_sources(inputs))
        return 0
    load_api_keys()
    results = asyncio.run(run_ab(prompts, MODELS))
    return _finish(args.out_json, input_sources(inputs), results, golden)


if __name__ == "__main__":
    sys.exit(main())
