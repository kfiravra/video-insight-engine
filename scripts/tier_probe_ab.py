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
    ... --dry-run   # gather inputs + render prompts, no LLM calls
    ... --rescore   # re-score the raw responses already in --out-json, no LLM calls
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from _tier_probe_inputs import GoldenVideo, ProbeInput, gather_inputs, load_live_golden

_REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = Path(__file__).resolve().parent / "tier_probe_ab_prompt.txt"
ENV_PATH = _REPO_ROOT / ".env"

MODELS: tuple[str, ...] = ("openai/gpt-4o-mini", "anthropic/claude-haiku-4-5-20251001")
MAX_TOKENS = 80
WINDOW_CHARS = 700
DESCRIPTION_CHARS = 500
MAX_TAGS = 15
CALL_TIMEOUT_S = 15.0
# Frame annotations the pipeline splices into the transcript:
# "[VISUAL at 3:42: ...]" and "[ON-SCREEN TEXT at 3:42: ...]" (scene_frames.py).
_ANNOTATION_START = re.compile(r"\[(?:VISUAL|ON-SCREEN TEXT)\b")
# Golden rows whose URL no longer matches the entry (reported, scored both ways).
SUSPECT_GOLDEN: frozenset[str] = frozenset({"review-airpods-pro"})

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


# ─── Pure helpers ────────────────────────────────────────────────────────────


def _annotation_end(text: str, start: int) -> int:
    """Index after the bracket closing the annotation at ``start`` (one line max)."""
    line_end = text.find("\n", start)
    line_end = len(text) if line_end < 0 else line_end
    depth = 0
    for index in range(start, line_end):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index + 1
    last = text.rfind("]", start, line_end)
    return last + 1 if last > start else line_end


def strip_visual_annotations(text: str) -> str:
    """Drop ``[VISUAL ...]`` / ``[ON-SCREEN TEXT ...]`` spans; collapse whitespace."""
    kept: list[str] = []
    position = 0
    for match in _ANNOTATION_START.finditer(text):
        if match.start() < position:
            continue
        kept.append(text[position : match.start()])
        position = _annotation_end(text, match.start())
    kept.append(text[position:])
    return " ".join("".join(kept).split())


def _window(text: str, start: int, size: int) -> str:
    """Slice ``size`` chars from ``start`` and trim partial words at both edges."""
    start = max(0, min(start, len(text) - size))
    chunk = text[start : start + size]
    if start > 0 and " " in chunk:
        chunk = chunk.split(" ", 1)[1]
    if start + size < len(text) and " " in chunk:
        chunk = chunk.rsplit(" ", 1)[0]
    return chunk.strip()


def transcript_windows(transcript: str, size: int = WINDOW_CHARS) -> tuple[str, str, str]:
    """Clean start/middle/end windows; a short transcript is not duplicated."""
    clean = strip_visual_annotations(transcript)
    if len(clean) <= 3 * size:
        thirds = [clean[i * len(clean) // 3 : (i + 1) * len(clean) // 3] for i in range(3)]
        return thirds[0].strip(), thirds[1].strip(), thirds[2].strip()
    mid_start = len(clean) // 2 - size // 2
    return (
        _window(clean, 0, size),
        _window(clean, mid_start, size),
        _window(clean, len(clean), size),
    )


def _sanitize(text: str, max_len: int) -> str:
    """Same rule as ``pipeline_helpers.sanitize_for_prompt``: no braces, no tags."""
    return text.replace("{", "").replace("}", "").replace("<", "‹").replace(">", "›")[:max_len]


def render_prompt(template: str, item: ProbeInput) -> str:
    """Fill the B.1 template for one video."""
    start, mid, end = transcript_windows(item.transcript)
    duration = str(round(item.duration / 60)) if item.duration > 0 else "unknown"
    values = {
        "{title}": _sanitize(item.title, 200),
        "{channel}": _sanitize(item.channel or "Unknown", 100),
        "{duration_minutes}": duration,
        "{youtube_category}": _sanitize(item.youtube_category or "unknown", 60),
        "{tags}": _sanitize(", ".join(item.tags[:MAX_TAGS]) or "none", 500),
        "{description}": _sanitize(item.description.strip(), DESCRIPTION_CHARS) or "none",
        "{window_start}": _sanitize(start, WINDOW_CHARS) or "(no transcript)",
        "{window_mid}": _sanitize(mid, WINDOW_CHARS) or "(no transcript)",
        "{window_end}": _sanitize(end, WINDOW_CHARS) or "(no transcript)",
    }
    prompt = template
    for key, value in values.items():
        prompt = prompt.replace(key, value)
    return prompt


def parse_probe(raw: str) -> tuple[dict[str, object] | None, str | None]:
    """Parse + validate a probe response → (parsed, error).

    Like the pipeline's ``parse_json_response``, the first JSON object wins:
    code fences and trailing prose are tolerated (they cost tokens, and are
    counted separately by ``is_bare_json`` / ``finish_reason``).
    """
    start = raw.find("{")
    if start < 0:
        return None, "no json object"
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc.msg}"
    if not isinstance(data, dict):
        return None, "not an object"
    if data.get("domain") not in VALID_DOMAINS:
        return None, f"invalid domain {data.get('domain')!r}"
    if data.get("format") not in VALID_FORMATS:
        return None, f"invalid format {data.get('format')!r}"
    if not isinstance(data.get("has_visual_demo"), bool):
        return None, "has_visual_demo not a bool"
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return None, "confidence not in [0, 1]"
    return data, None


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


def summarize(
    results: list[ProbeResult], golden: list[GoldenVideo], exclude: frozenset[str] = frozenset()
) -> dict[str, dict[str, float]]:
    """Per-model agreement, parse failures, latency and cost (``exclude``: golden ids)."""
    by_id = {g.golden_id: g for g in golden}
    summary: dict[str, dict[str, float]] = {}
    for model in sorted({r.model for r in results}):
        rows = [r for r in results if r.model == model and r.golden_id not in exclude]
        ok = [r for r in rows if r.parsed is not None]
        n = len(rows)
        latencies = [r.latency_ms for r in rows if r.call_error is None]
        summary[model] = {
            "n": n,
            "domain_pct": _pct(sum(r.parsed["domain"] == by_id[r.golden_id].domain for r in ok), n),
            "format_pct": _pct(sum(r.parsed["format"] == by_id[r.golden_id].format for r in ok), n),
            "visual_pct": _pct(sum(_visual_match(r) for r in ok), n),
            "parse_failures": sum(r.parse_error is not None for r in rows),
            "bare_json": sum(is_bare_json(r.raw) for r in rows),
            "truncated": sum(r.finish_reason == "length" for r in rows),
            "call_errors": sum(r.call_error is not None for r in rows),
            "p50_ms": percentile(latencies, 50),
            "p95_ms": percentile(latencies, 95),
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


def render_summary_table(summary: dict[str, dict[str, float]]) -> str:
    """Markdown table for the gate report."""
    lines = [
        "| Model | n | domain % | format % | has_visual_demo % | parse fail | bare JSON "
        "| hit max_tokens | p50 ms | p95 ms | max ms | > 3 s | $/call |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, s in summary.items():
        lines.append(
            f"| `{model}` | {s['n']} | {s['domain_pct']} | {s['format_pct']} | {s['visual_pct']} "
            f"| {s['parse_failures']} | {s['bare_json']}/{s['n']} | {s['truncated']} "
            f"| {s['p50_ms']:.0f} | {s['p95_ms']:.0f} | {s['max_ms']:.0f} | {s['over_3s']} "
            f"| {s['usd_per_call']:.5f} |"
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
    parsed, error = parse_probe(raw)
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
    parser.add_argument("--cache-dir", type=Path, required=True, help="scratch dir for inputs")
    parser.add_argument("--out-json", type=Path, help="raw results + summary JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="gather + render only")
    mode.add_argument("--rescore", action="store_true", help="re-score --out-json, no LLM")
    return parser.parse_args(argv)


def load_results(path: Path) -> list[ProbeResult]:
    """Reload saved rows and re-parse each raw response with today's parser."""
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]
    results = [ProbeResult(**row) for row in rows]
    for result in results:
        if result.call_error is None:
            result.parsed, result.parse_error = parse_probe(result.raw)
    return results


def build_summaries(
    results: list[ProbeResult], golden: list[GoldenVideo]
) -> dict[str, dict[str, dict[str, float]]]:
    """All live rows, and the same without the suspect golden rows."""
    return {
        "all": summarize(results, golden),
        "excludingSuspectGolden": summarize(results, golden, SUSPECT_GOLDEN),
    }


def _write_report(
    path: Path,
    inputs: dict[str, ProbeInput],
    results: list[ProbeResult],
    summaries: dict[str, dict[str, dict[str, float]]],
) -> None:
    sources = {
        gid: {
            "metadata": i.metadata_source,
            "transcript": i.transcript_source,
            "transcriptLanguage": i.transcript_language,
            "transcriptChars": len(i.transcript),
            "frameCaptions": i.frame_captions,
        }
        for gid, i in inputs.items()
    }
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
        "summary": summaries,
        "inputs": sources,
        "visualDemoLabels": labels,
        "results": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    golden = load_live_golden()
    inputs = gather_inputs(golden, args.cache_dir)
    template = PROMPT_PATH.read_text(encoding="utf-8")
    prompts = {gid: render_prompt(template, item) for gid, item in inputs.items()}
    if args.dry_run:
        for gid, prompt in prompts.items():
            print(f"{gid}: {len(prompt)} chars, transcript={inputs[gid].transcript_source}")
        return 0
    if args.rescore:
        results = load_results(args.out_json)
    else:
        load_api_keys()
        results = asyncio.run(run_ab(prompts, MODELS))
    summaries = build_summaries(results, golden)
    for name, summary in summaries.items():
        print(f"\n{name}\n{render_summary_table(summary)}")
    if args.out_json:
        _write_report(args.out_json, inputs, results, summaries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
