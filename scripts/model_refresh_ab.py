"""Offline model A/B per call type (pipeline-1min 4.0d): today's model vs the 4.0 target.

Requests are the pipeline's own, captured where the provider sends them:
the three replay cassettes in split mode (tier probe, description analysis,
plan, memory, every extraction group, quiz, faithfulness judge), the 18 live
golden videos' tier probes (0.8's inputs + labels), EqaMrzd9nZU's output and
RAG translation batches, and the vision batches of uC45 + jMq8 (720p frames).
Arm A = today's model, B = the 4.0 target at 4.0b's defaults (thinking off,
effort low), vision C = Sonnet 5.5 (``--plan-medium`` adds plan at medium).
Each arm re-shapes the same request through 4.0b's request rules; plan and
extraction run twice on one video for the run-to-run noise.

Inputs are read-only (dev Mongo through ``mongosh``, dev S3) and cached under
``--cache-dir``. No pipeline run, no Langfuse, no Mongo writes. The live run
prints the cost estimate first and aborts above what is left of ``--ceiling``
($1.50 for the whole A/B: every row already in ``answers.jsonl`` counts).
Follow-ups (``--only`` / ``--source`` / ``--arms`` / ``--medium`` /
``--repeats``) send only the calls not answered yet with today's request bytes
and append to the same report.

Usage (from the repo root)::

    services/summarizer/.venv/bin/python scripts/model_refresh_ab.py \\
        --cache-dir <scratch>/p40d/cache --dry-run       # render + count + estimate
    ... --cache-dir <scratch>/p40d/cache                  # live (answers.jsonl in the cache dir)
    ... --rescore                                         # re-score the saved report offline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_DIR = _REPO_ROOT / "services" / "summarizer"
for _path in (_SUMMARIZER_DIR, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
# The bundled map + 4.0b's Haiku 5.5 entry: the same prices on every run.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from _model_ab_capture import capture_cassettes  # noqa: E402
from _model_ab_cost import (  # noqa: E402
    SPEND_CEILING_USD,
    CallEstimate,
    PlannedCall,
    estimate_calls,
    longest_chunk_projection,
    over_ceiling,
    prompt_tokens,
    run_order,
    totals_by_arm,
)
from _model_ab_frames import (  # noqa: E402
    VISION_FRAMES_PER_VIDEO,
    VISION_VIDEOS,
    gather_frames,
    vision_requests,  # noqa: E402
)
from _model_ab_inputs import (  # noqa: E402
    gather_golden,
    gather_hebrew,
    golden_probe_requests,
    output_translation_requests,
    rag_translation_requests,
)
from _model_ab_report import max_prompt_tokens, render_markdown, score_rows, summarize  # noqa: E402
from _model_ab_runner import (  # noqa: E402
    answered,
    arm_request,
    is_answered,
    load_answers,
    request_sha256,
    run_calls,
)
from _model_ab_types import CALL_TYPES, AbRequest, CallResult, arms_for, reshaped_keys  # noqa: E402
from _model_ab_wire import wire_check  # noqa: E402

GATES_DIR = _REPO_ROOT / "dev" / "active" / "pipeline-1min" / "gates"
ENV_PATH = _REPO_ROOT / ".env"
NOISE_VIDEO = "T1dQhQAm8Tc"
_REPEATED = frozenset({"plan", "extraction"})
_AWS_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")
_PROVIDER_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
_STREAM_KEYS = ("stream", "stream_options")
_SOURCES = ("cassette", "golden", "hebrew", "frames")


def load_env_keys(names: Sequence[str], env_path: Path = ENV_PATH) -> None:
    """Export only ``names`` from the repo ``.env`` (never printed; the environment wins)."""
    wanted = set(names)
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in wanted and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


# ─── Requests ────────────────────────────────────────────────────────────────


def collect_requests(cache_dir: Path, *, fetch_metadata: bool) -> list[AbRequest]:
    """Every request of the A/B, the noise video's plan + extraction marked for two runs."""
    requests = capture_cassettes()
    requests += golden_probe_requests(gather_golden(cache_dir, fetch_metadata=fetch_metadata))
    hebrew = gather_hebrew(cache_dir)
    requests += output_translation_requests(hebrew) + rag_translation_requests(hebrew)
    frames = gather_frames(cache_dir, VISION_VIDEOS, VISION_FRAMES_PER_VIDEO)
    requests += vision_requests(frames)
    for request in requests:
        if request.video_id == NOISE_VIDEO and request.call_type in _REPEATED:
            request.repeats = 2
    return requests


def _arm_hashes(request: AbRequest) -> dict[str, str]:
    """Each arm's request hash today (``score_rows`` flags a row sent with other bytes)."""
    arms = arms_for(request.call_type, medium=frozenset(CALL_TYPES))
    return {a.name: request_sha256(arm_request(PlannedCall(request, a, 0))) for a in arms}


def request_info(requests: Sequence[AbRequest]) -> dict[str, dict[str, Any]]:
    """What the report keeps of each request (no prompt bytes): enough for ``--rescore``."""
    return {
        r.request_id: {
            "call_type": r.call_type,
            "source": r.source,
            "video_id": r.video_id,
            "repeats": r.repeats,
            "pipeline_timeout_s": float(r.kwargs.get("timeout") or 0.0),
            "prompt_tokens_cl100k": prompt_tokens(r.kwargs),
            "expected_output_tokens": r.expected_output_tokens,
            "context": r.context,
            "arm_sha256": _arm_hashes(r),
        }
        for r in requests
    }


# ─── Estimate ────────────────────────────────────────────────────────────────


def _estimate_lines(
    estimates: Sequence[CallEstimate], requests: Sequence[AbRequest], ceiling: float
) -> list[str]:
    counts = Counter(r.call_type for r in requests)
    lines = ["call type          arm       model                         calls  max prompt  cost $"]
    for (call_type, arm), row in sorted(
        totals_by_arm(estimates).items(),
        key=lambda item: (CALL_TYPES.index(item[0][0]), item[0][1]),
    ):
        model = str(row["model"]).split("/")[-1]
        lines.append(
            f"{call_type:<18} {arm:<9} {model:<29} {row['calls']:>5}  "
            f"{row['max_prompt_tokens']:>10,}  {row['cost_usd']:.4f}"
        )
    by_arm: dict[str, float] = {}
    for item in estimates:
        by_arm[item.arm] = by_arm.get(item.arm, 0.0) + item.cost_usd
    lines.append("requests per call type: " + ", ".join(f"{c} {counts[c]}" for c in CALL_TYPES))
    lines.append("per arm: " + ", ".join(f"{a} ${v:.4f}" for a, v in sorted(by_arm.items())))
    lines.append(f"TOTAL ${sum(e.cost_usd for e in estimates):.4f} (ceiling ${ceiling:.2f})")
    return lines


def _medium(args: argparse.Namespace) -> frozenset[str]:
    return frozenset(args.medium or ())


def _first_calls(requests: Sequence[AbRequest], args: argparse.Namespace) -> list[PlannedCall]:
    """Each call type's first request (extraction: a streamed text1) once per selected arm."""
    planned: list[PlannedCall] = []
    for call_type in CALL_TYPES:
        first = next((r for r in requests if r.call_type == call_type), None)
        if first is None:
            continue
        arms = arms_for(call_type, medium=_medium(args))
        planned += [PlannedCall(first, a, 0) for a in arms if not args.arms or a.name in args.arms]
    return planned


def _wire_lines(requests: Sequence[AbRequest], args: argparse.Namespace) -> tuple[list[str], int]:
    """Per call type and arm: the re-shaping (vs the captured request) and the wire body."""
    planned = _first_calls(requests, args)
    rows = wire_check(planned)
    (args.cache_dir / "wire-check.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    lines: list[str] = []
    for call, row in zip(planned, rows, strict=True):
        captured = {k: v for k, v in call.request.kwargs.items() if k not in _STREAM_KEYS}
        diff = reshaped_keys(captured, arm_request(call))
        lines.append(f"{row['call_type']:<18} {row['arm']:<9} {row['model'].split('/')[-1]}")
        lines.append(f"    re-shaped {json.dumps(diff)}")
        lines.append(f"    wire {json.dumps(row['wire'])}")
        if row["problems"] or row["error"]:
            lines.append(f"    PROBLEM {row['problems']} {row['error'] or ''}")
    return lines, sum(bool(r["problems"] or r["error"]) for r in rows)


def _select(requests: list[AbRequest], args: argparse.Namespace) -> list[AbRequest]:
    """The requests ``--only`` / ``--source`` keep; ``--repeats`` raises their run count."""
    chosen = [
        r
        for r in requests
        if (not args.only or r.call_type in args.only)
        and (not args.source or r.source in args.source)
    ]
    for request in chosen:
        request.repeats = max(request.repeats, args.repeats or 1)
    return chosen


def _pending(requests: Sequence[AbRequest], args: argparse.Namespace) -> list[PlannedCall]:
    """The selected arms' planned calls not answered yet (a follow-up sends only these)."""
    done = answered(args.cache_dir / "answers.jsonl")
    return [
        call
        for call in run_order(requests, medium=_medium(args))
        if (not args.arms or call.arm.name in args.arms) and not is_answered(done, call)
    ]


def _spent(answers_path: Path) -> float:
    """What the A/B has spent so far (every saved row, errors included)."""
    return sum(r.cost_usd for r in load_answers(answers_path)) if answers_path.exists() else 0.0


def _estimate(
    planned: Sequence[PlannedCall], requests: Sequence[AbRequest], args: argparse.Namespace
) -> list[CallEstimate]:
    estimates = estimate_calls(planned)
    for line in _estimate_lines(estimates, requests, args.ceiling):
        print(line)
    projection = longest_chunk_projection(requests)
    print(f"longest-chunk projection: {json.dumps(projection)}")
    out = args.cache_dir / "dry-run.json"
    out.write_text(
        json.dumps(
            {
                "estimates": [asdict(e) for e in estimates],
                "projection": projection,
                "requests": request_info(requests),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return estimates


# ─── Report ──────────────────────────────────────────────────────────────────


def _arms_block(medium: frozenset[str]) -> dict[str, list[dict[str, str]]]:
    return {c: [asdict(a) for a in arms_for(c, medium=medium)] for c in CALL_TYPES}


def _finish(args: argparse.Namespace, meta: dict[str, Any], results: list[CallResult]) -> int:
    rows = score_rows(meta["requests"], results)
    report = {
        **meta,
        "calls": len(rows),
        "spend_usd": round(sum(float(r["cost_usd"]) for r in rows), 4),
        "summary": summarize(rows),
        "max_prompt_tokens": max_prompt_tokens(rows),
        "rows": rows,
    }
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    markdown = render_markdown(report)
    args.out_md.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


_META_KEYS = (
    "run_at",
    "estimate_usd",
    "ceiling_usd",
    "runs",
    "medium",
    "arms",
    "requests",
    "longest_chunk_projection",
    "picks",
)


def _rescore(args: argparse.Namespace) -> int:
    """Re-score the saved answers with the saved request block — nothing external."""
    payload = json.loads(args.out_json.read_text(encoding="utf-8"))
    names = {f.name for f in fields(CallResult)}
    results = [
        CallResult(**{k: v for k, v in row.items() if k in names}) for row in payload["rows"]
    ]
    meta = {k: payload[k] for k in _META_KEYS if k in payload}
    if args.picks:
        meta["picks"] = json.loads(args.picks.read_text(encoding="utf-8"))
    return _finish(args, meta, results)


def _runs(args: argparse.Namespace, previous: dict[str, Any], estimate_usd: float) -> list[Any]:
    """The report's runs so far plus this one (a follow-up appends to the same report)."""
    runs = list(previous.get("runs") or [])
    if previous and not runs:
        runs.append({"run_at": previous["run_at"], "estimate_usd": previous["estimate_usd"]})
    filters = {k: getattr(args, k) for k in ("only", "source", "arms", "medium", "repeats")}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run = {"run_at": now, "estimate_usd": round(estimate_usd, 4), "ceiling_usd": args.ceiling}
    return [*runs, {**run, "filters": filters}]


def _live(
    args: argparse.Namespace,
    requests: list[AbRequest],
    planned: list[PlannedCall],
    estimate_usd: float,
) -> int:
    load_env_keys(_PROVIDER_KEYS)
    answers = args.cache_dir / "answers.jsonl"
    asyncio.run(run_calls(planned, answers))
    previous = json.loads(args.out_json.read_text("utf-8")) if args.out_json.exists() else {}
    runs = _runs(args, previous, estimate_usd)
    medium = _medium(args) | frozenset(previous.get("medium") or ())
    meta = {
        "run_at": runs[0]["run_at"],
        "estimate_usd": round(sum(run["estimate_usd"] for run in runs), 4),
        "ceiling_usd": previous.get("ceiling_usd", args.ceiling),
        "runs": runs,
        "medium": sorted(medium),
        "arms": _arms_block(medium),
        "requests": request_info(requests),
        "longest_chunk_projection": longest_chunk_projection(requests),
        "picks": previous.get("picks") or [],
    }
    return _finish(args, meta, load_answers(answers))


# ─── Entry point ─────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--cache-dir", type=Path, help="scratch dir: inputs, answers, dry run")
    parser.add_argument("--out-json", type=Path, default=GATES_DIR / "g4-model-ab.json")
    parser.add_argument("--out-md", type=Path, default=GATES_DIR / "g4-model-ab.md")
    parser.add_argument(
        "--ceiling", type=float, default=SPEND_CEILING_USD, help="total A/B budget (all runs)"
    )
    parser.add_argument("--medium", nargs="*", choices=CALL_TYPES, help="add B at effort medium")
    parser.add_argument("--only", nargs="*", choices=CALL_TYPES, help="limit to these call types")
    parser.add_argument("--source", nargs="*", choices=_SOURCES, help="limit to these sources")
    parser.add_argument("--arms", nargs="*", help="send only these arms (A, B, C, B-medium)")
    parser.add_argument("--repeats", type=int, help="run each selected request this many times")
    parser.add_argument("--fetch-metadata", action="store_true", help="golden yt-dlp metadata")
    parser.add_argument("--picks", type=Path, help="with --rescore: the picks table (JSON list)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="render + count + estimate only")
    mode.add_argument("--rescore", action="store_true", help="re-score --out-json offline")
    args = parser.parse_args(argv)
    if not args.rescore and args.cache_dir is None:
        parser.error("--cache-dir is required unless --rescore")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # The re-shaping is printed once per call type and arm (``_wire_lines``);
    # the rules' per-request "dropping temperature" warnings would repeat it.
    logging.getLogger("src.services.llm_model_rules").setLevel(logging.ERROR)
    if args.rescore:
        return _rescore(args)
    load_env_keys(_AWS_KEYS)
    every = collect_requests(args.cache_dir, fetch_metadata=args.fetch_metadata)
    requests = _select(every, args)
    lines, problems = _wire_lines(requests, args)
    print("\n".join(lines))
    if problems:
        print(f"ABORT: {problems} arm request(s) carry a param their model rejects (above)")
        return 3
    planned = _pending(requests, args)
    estimates = _estimate(planned, requests, args)
    estimate_usd = sum(e.cost_usd for e in estimates)
    remaining = args.ceiling - _spent(args.cache_dir / "answers.jsonl")
    print(f"budget: ${args.ceiling:.2f} total, ${remaining:.4f} left for this run")
    if over_ceiling(estimates, remaining):
        print(f"ABORT: estimate ${estimate_usd:.4f} is above the ${remaining:.4f} left")
        return 2
    if args.dry_run:
        return 0
    return _live(args, every, planned, estimate_usd)


if __name__ == "__main__":
    sys.exit(main())
