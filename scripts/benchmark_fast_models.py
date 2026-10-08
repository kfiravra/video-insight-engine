#!/usr/bin/env python3
"""Multi-model fast-tier benchmark across the summarizer pipeline.

Runs each fast-tier stage (chapter_detect, description analysis, synthesis,
enrichment, translation) with four candidate models against three
real cached videos (tech / food / language), scores candidates against the
Sonnet baseline, and writes a Markdown report with per-stage winners.

Frame vision is benchmarked separately: Sonnet baseline + Haiku 4.5 +
Gemini 2.5 Flash on top-8 frames per video.

The script is **read-mostly**: it does not mutate `videoSummaryCache` or
S3.  It does write new `llm_usage` rows tagged with feature
``bench:<stage>:<model_label>:<video_summary_id>`` for cost attribution.

Usage::

    python scripts/benchmark_fast_models.py \\
        --output reports/fast-model-bench-$(date +%Y%m%d-%H%M%S).md
    python scripts/benchmark_fast_models.py --skip-vision   # cheaper, no vision
    python scripts/benchmark_fast_models.py --dry-run       # show corpus only

Stops before mutating ``config.py``. The proposed diff is printed at the
end of the report for the operator to apply manually.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_SRC = _REPO_ROOT / "services" / "summarizer"
if str(_SUMMARIZER_SRC) not in sys.path:
    sys.path.insert(0, str(_SUMMARIZER_SRC))
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import litellm  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from llm_common import MongoDBUsageCallback  # noqa: E402
from llm_common.context import llm_feature_var, llm_video_id_var  # noqa: E402

from src.config import settings  # noqa: E402
from src.services.llm import LLMService  # noqa: E402
from src.services.llm_provider import LLMProvider  # noqa: E402
from src.services.media.frame_analyzer import (  # noqa: E402
    VISION_ANALYSIS_PROMPT,
    parse_vision_response,
)
from src.services.pipeline.enrichment import enrich_quiz  # noqa: E402
from src.services.pipeline.synthesis import synthesize  # noqa: E402
from src.services.pipeline.translation import _translate_json  # noqa: E402
# Reaching into transcript_chunker's private function is intentional: the
# bench needs the AI chapter-detection step in isolation, but the public API
# (`extract_chapter_chunks`) bundles it with YouTube-chapters detection and
# time-based slicing — those aren't useful for benchmarking the LLM call.
# If `_detect_chapters_with_ai` is renamed or refactored, update this import.
from src.services.transcription.transcript_chunker import (  # noqa: E402
    _detect_chapters_with_ai,
)
from src.services.transcription.transcript_store import TranscriptStoreService  # noqa: E402
from src.services.video.description_analyzer import (  # noqa: E402
    DescriptionAnalysis,
    _analyze_description_async,
)

import _bench_quality_scorers as scorers  # noqa: E402

logger = logging.getLogger("benchmark_fast_models")

REPORTS_DIR = _REPO_ROOT / "reports"

# ─── Candidates ─────────────────────────────────────────────────────────────

CANDIDATE_MODELS: list[tuple[str, str]] = [
    ("gpt-4o-mini", "openai/gpt-4o-mini"),
    ("gpt-5-mini", "openai/gpt-5-mini"),
    ("haiku-4.5", "anthropic/claude-haiku-4-5-20251001"),
    ("gemini-flash-lite", "gemini/gemini-2.5-flash-lite"),
]

VISION_CANDIDATES: list[tuple[str, str]] = [
    ("haiku-4.5", "anthropic/claude-haiku-4-5-20251001"),
    ("gemini-flash", "gemini/gemini-2.5-flash"),
]

BASELINE_MODEL = "anthropic/claude-sonnet-4-6"
BASELINE_LABEL = "sonnet-4.6"

# Per-stage tie-break: when winners are within 5% on quality, what wins?
#   "cheapest" → lowest cost/call
#   "fastest"  → highest output tokens/sec
TIE_BREAK: dict[str, str] = {
    "chapter_detect": "cheapest",
    "description": "cheapest",
    "synthesis": "fastest",
    "enrichment": "fastest",
    "translation": "cheapest",
    "vision": "cheapest",
}

# Domain fallback chain when a primary domain has zero qualifying cases.
DOMAIN_FALLBACKS: dict[str, list[str]] = {
    "tech": ["tech", "code", "project"],
    "food": ["food", "review"],
    "language": ["language", "learning"],
}

QUALITY_TIE_THRESHOLD = 0.05  # within 5% → use tie-break

# Bench feature-tag prefix so cost rows can be aggregated separately.
BENCH_FEATURE_PREFIX = "bench"


# ─── Dataclasses ────────────────────────────────────────────────────────────


@dataclass
class CorpusCase:
    """Hydrated input for benchmark runs."""

    domain_key: str  # tech | food | language
    matched_tag: str  # actual primary_tag from the doc
    video_summary_id: str
    youtube_id: str
    title: str
    channel: str
    duration: int
    description: str
    tags: list[str]
    transcript: str
    transcript_segments: list[dict[str, Any]]
    plan_tabs: list[dict[str, Any]]
    extraction_data: dict[str, Any]
    synthesis_baseline: dict[str, Any]


@dataclass
class StageRun:
    """One LLM call's outcome (telemetry + raw output)."""

    model_label: str
    elapsed_ms: int
    error: str | None = None
    output: Any = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def tokens_per_sec(self) -> float:
        if self.elapsed_ms <= 0 or self.tokens_out <= 0:
            return 0.0
        return round(self.tokens_out / (self.elapsed_ms / 1000.0), 1)


@dataclass
class StageBenchResult:
    """Per-stage results across all candidates for one video."""

    stage: str
    case_id: str
    baseline: StageRun
    candidates: dict[str, StageRun] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    score_extra: dict[str, Any] = field(default_factory=dict)


# ─── Provider construction ──────────────────────────────────────────────────


def make_provider(model: str) -> LLMProvider:
    """Construct an LLMProvider with both slots pinned to ``model``.

    Setting `fast_model=model` ensures `use_fast_model=True` callers also hit
    the candidate. No fallbacks — we want pure per-model measurements.
    """
    return LLMProvider(
        model=model,
        fast_model=model,
        fallback_models=None,
        timeout=120.0,
    )


def make_service(model: str) -> LLMService:
    return LLMService(make_provider(model))


# ─── Corpus selection ───────────────────────────────────────────────────────


def _query_one_per_domain(
    db: Any,
    domain_key: str,
    fallback_tags: list[str],
) -> dict[str, Any] | None:
    """Find one completed video matching the domain (or fallback).

    The cache uses camelCase ``primaryTag`` (Pydantic alias).  Transcripts
    are loaded by youtubeId via the conventional ``videos/{id}/transcript.json``
    key (no ``rawTranscriptRef`` requirement here).
    """
    coll = db["videoSummaryCache"]
    for tag in fallback_tags:
        query: dict[str, Any] = {
            "status": "completed",
            "pipeline.triage.primaryTag": tag,
            "pipeline.extraction": {"$exists": True},
            "tabs": {"$exists": True, "$not": {"$size": 0}},
        }
        doc = coll.find_one(query, sort=[("updatedAt", -1)])
        if doc:
            doc["__matched_tag"] = tag
            logger.info(
                "corpus[%s]: matched %s (id=%s tag=%s)",
                domain_key, doc.get("youtubeId"), doc["_id"], tag,
            )
            return doc
    logger.warning("corpus[%s]: no docs found for any of %s", domain_key, fallback_tags)
    return None


async def hydrate_case(
    domain_key: str,
    doc: dict[str, Any],
    transcript_store: TranscriptStoreService,
) -> CorpusCase | None:
    """Reconstruct a CorpusCase from a doc. Returns None on missing data."""
    youtube_id = str(doc.get("youtubeId", ""))
    raw = await transcript_store.get(youtube_id)
    if raw is None or not raw.segments:
        logger.warning("hydrate[%s]: no transcript in S3 for %s", domain_key, youtube_id)
        return None

    segments = raw.segments
    transcript = " ".join(str(s.get("text", "")).strip() for s in segments if s.get("text"))
    if not transcript:
        return None

    triage = (doc.get("pipeline") or {}).get("triage") or {}
    extraction = (doc.get("pipeline") or {}).get("extraction") or {}
    synthesis_data = doc.get("synthesis") or {}
    plan_tabs = list(triage.get("tabs") or [])

    return CorpusCase(
        domain_key=domain_key,
        matched_tag=str(doc.get("__matched_tag", "")),
        video_summary_id=str(doc["_id"]),
        youtube_id=youtube_id,
        title=str(doc.get("title", "")),
        channel=str(doc.get("creator") or doc.get("channel", "")),
        duration=int(doc.get("duration", 0) or 0),
        description=str(doc.get("description", "") or ""),
        tags=list(doc.get("tags", []) or []),
        transcript=transcript,
        transcript_segments=segments,
        plan_tabs=plan_tabs,
        extraction_data=extraction,
        synthesis_baseline=synthesis_data,
    )


# ─── Stage runners ──────────────────────────────────────────────────────────


async def _run_with_telemetry(
    stage: str,
    case: CorpusCase,
    model_label: str,
    coro_factory,
) -> StageRun:
    """Run a coroutine, time it, capture errors. Output is whatever coro returns."""
    feature_tag = f"{BENCH_FEATURE_PREFIX}:{stage}:{model_label}:{case.video_summary_id}"
    feature_token = llm_feature_var.set(feature_tag)
    video_token = llm_video_id_var.set(case.video_summary_id)
    started = time.monotonic()
    try:
        output = await coro_factory()
        return StageRun(
            model_label=model_label,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            output=output,
        )
    except Exception as exc:  # broad catch — bench must survive any stage failure
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning("[%s/%s/%s] failed: %s", stage, model_label, case.video_summary_id, exc)
        return StageRun(
            model_label=model_label,
            elapsed_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    finally:
        llm_feature_var.reset(feature_token)
        llm_video_id_var.reset(video_token)


# Each stage takes a (case, model_string, model_label) and returns a StageRun.

async def run_chapter_detect(case: CorpusCase, model: str, label: str) -> StageRun:
    svc = make_service(model)
    return await _run_with_telemetry(
        "chapter_detect", case, label,
        lambda: _detect_chapters_with_ai(
            case.title, case.description, case.transcript, float(case.duration),
            case.transcript_segments, svc,
        ),
    )


async def run_description(case: CorpusCase, model: str, label: str) -> StageRun:
    return await _run_with_telemetry(
        "description", case, label,
        lambda: _analyze_description_async(case.description, fast_model=model),
    )


async def run_synthesis(case: CorpusCase, model: str, label: str) -> StageRun:
    svc = make_service(model)
    primary_tag = case.matched_tag or "learning"
    # Extraction summary is what synthesis prompt expects — feed cached extraction
    extraction_summary = json.dumps(case.extraction_data)[:6000]
    return await _run_with_telemetry(
        "synthesis", case, label,
        lambda: synthesize(
            svc, case.title, case.channel, case.duration,
            output_type=primary_tag,
            extraction_summary=extraction_summary,
        ),
    )


async def run_enrichment(case: CorpusCase, model: str, label: str) -> StageRun:
    svc = make_service(model)
    primary_tag = case.matched_tag or "learning"
    return await _run_with_telemetry(
        "enrichment", case, label,
        lambda: enrich_quiz(
            svc, primary_tag=primary_tag, extraction_data=case.extraction_data,
            video_memory="", tabs=[],
        ),
    )


# Translation: translate a fixed sample of English content TO Hebrew so we
# can score: (a) the model returned same-shaped JSON, (b) values contain
# Hebrew characters. Hebrew is chosen because it's a non-Latin script
# (clearer signal than Spanish where the model could echo English back).
_TRANSLATION_SAMPLE = {
    "tldr": "This video explains the basics of machine learning.",
    "first_takeaway": "Start with linear regression before deep models.",
    "second_takeaway": "Always split your data into train and test sets.",
    "next_step": "Practice on the public titanic dataset.",
}


async def run_translation(case: CorpusCase, model: str, label: str) -> StageRun:
    svc = make_service(model)
    return await _run_with_telemetry(
        "translation", case, label,
        lambda: _translate_json(
            svc, _TRANSLATION_SAMPLE,
            source_language="en", target_language="Hebrew",
            stage_name=f"bench_translation_{label}",
        ),
    )


# ─── Frame vision ───────────────────────────────────────────────────────────


async def run_vision(
    case: CorpusCase,
    model: str,
    label: str,
    messages: list[dict],
    metadata: list[dict],
) -> StageRun:
    """Run vision pass with messages already built (avoid re-encoding per model)."""
    provider = make_provider(model)
    started = time.monotonic()
    feature_tag = f"{BENCH_FEATURE_PREFIX}:vision:{label}:{case.video_summary_id}"
    feature_token = llm_feature_var.set(feature_tag)
    video_token = llm_video_id_var.set(case.video_summary_id)
    try:
        # use_fast_model=False routes to provider._model, which we pinned to candidate
        raw = await provider.complete_with_messages(
            messages, max_tokens=2000, timeout=120.0, use_fast_model=False,
        )
        parsed = parse_vision_response(raw, metadata)
        return StageRun(
            model_label=label,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            output=parsed,
        )
    except Exception as exc:
        return StageRun(
            model_label=label,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    finally:
        llm_feature_var.reset(feature_token)
        llm_video_id_var.reset(video_token)


def _list_scene_frames(youtube_id: str, n: int = 8) -> list[dict[str, Any]]:
    """List up to ``n`` scene frame keys from S3 and download to /tmp."""
    import boto3  # type: ignore[import-untyped]
    s3 = boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=settings.AWS_ENDPOINT_URL or None,
    )
    prefix = f"videos/{youtube_id}/scenes/"
    resp = s3.list_objects_v2(Bucket=settings.S3_BUCKET, Prefix=prefix)
    contents = resp.get("Contents", [])
    if not contents:
        logger.warning("No frames in s3://%s/%s", settings.S3_BUCKET, prefix)
        return []
    keys = sorted(o["Key"] for o in contents if o["Key"].endswith((".jpg", ".jpeg", ".png")))[:n]
    tmpdir = Path("/tmp") / f"vie_bench_{youtube_id}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for i, key in enumerate(keys):
        local = tmpdir / Path(key).name
        s3.download_file(settings.S3_BUCKET, key, str(local))
        out.append({"index": i, "path": str(local), "s3_key": key, "timestamp": 0.0, "total_score": 1.0})
    return out


def _build_vision_messages(frames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Mirror frame_analyzer's message builder so multiple model passes share input."""
    import base64
    content: list[dict] = [{"type": "text", "text": VISION_ANALYSIS_PROMPT}]
    meta: list[dict] = []
    for i, frame in enumerate(frames):
        try:
            with open(frame["path"], "rb") as f:
                data = f.read()
            ext = os.path.splitext(frame["path"])[1].lower()
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/jpeg")
            data_uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        except OSError:
            continue
        content.append({"type": "text", "text": f"Frame {i} (at 0:00):"})
        content.append({"type": "image_url", "image_url": {"url": data_uri}})
        meta.append({"index": i, "timestamp_sec": 0, "s3_key": frame["s3_key"]})
    return [{"role": "user", "content": content}], meta


# ─── Cost attribution ───────────────────────────────────────────────────────


def fetch_cost_rows(db: Any, prefix: str = BENCH_FEATURE_PREFIX) -> dict[str, dict[str, float]]:
    """Aggregate llm_usage cost / tokens per `feature` tag."""
    cursor = db["llm_usage"].aggregate([
        {"$match": {"feature": {"$regex": f"^{prefix}:"}}},
        {"$group": {
            "_id": "$feature",
            "cost_usd": {"$sum": "$cost_usd"},
            "tokens_in": {"$sum": "$tokens_in"},
            "tokens_out": {"$sum": "$tokens_out"},
            "calls": {"$sum": 1},
        }},
    ])
    out: dict[str, dict[str, float]] = {}
    for row in cursor:
        out[str(row["_id"])] = {
            "cost_usd": float(row.get("cost_usd", 0) or 0),
            "tokens_in": int(row.get("tokens_in", 0) or 0),
            "tokens_out": int(row.get("tokens_out", 0) or 0),
            "calls": int(row.get("calls", 0) or 0),
        }
    return out


def attach_costs(results: list[StageBenchResult], costs: dict[str, dict[str, float]]) -> None:
    """Walk results, fill cost / tokens fields from the aggregated cost map."""
    for r in results:
        for label, run in [(BASELINE_LABEL, r.baseline)] + list(r.candidates.items()):
            tag = f"{BENCH_FEATURE_PREFIX}:{r.stage}:{label}:{r.case_id}"
            row = costs.get(tag)
            if row:
                run.cost_usd = row["cost_usd"]
                run.tokens_in = int(row["tokens_in"])
                run.tokens_out = int(row["tokens_out"])


# ─── Per-stage benchmark orchestration ──────────────────────────────────────


STAGE_RUNNERS = {
    "chapter_detect": run_chapter_detect,
    "description": run_description,
    "synthesis": run_synthesis,
    "enrichment": run_enrichment,
    "translation": run_translation,
}

STAGE_SCORERS = {
    "chapter_detect": lambda b, c: scorers.score_chapter_detect(b.output or [], c.output or []),
    "description": lambda b, c: scorers.score_description(b.output, c.output),
    "synthesis": lambda b, c: scorers.score_synthesis(b.output, c.output),
    "enrichment": lambda b, c: scorers.score_enrichment(b.output, c.output),
    "translation": lambda b, c: scorers.score_translation(b.output, c.output, expected_lang="he"),
}


async def benchmark_stage(stage: str, case: CorpusCase) -> StageBenchResult:
    runner = STAGE_RUNNERS[stage]
    # Baseline first, then candidates in sequence (cheap rate-limit safety).
    baseline = await runner(case, BASELINE_MODEL, BASELINE_LABEL)
    candidates: dict[str, StageRun] = {}
    for label, model in CANDIDATE_MODELS:
        candidates[label] = await runner(case, model, label)
    result = StageBenchResult(
        stage=stage, case_id=case.video_summary_id, baseline=baseline,
        candidates=candidates,
    )
    scorer = STAGE_SCORERS[stage]
    for label, run in candidates.items():
        if run.error or baseline.error:
            result.scores[label] = 0.0
            continue
        try:
            result.scores[label] = float(scorer(baseline, run))
        except Exception as exc:  # scorers must never crash the bench
            logger.warning("scorer[%s/%s] failed: %s", stage, label, exc)
            result.scores[label] = 0.0
    return result


async def benchmark_vision(case: CorpusCase) -> StageBenchResult | None:
    frames = _list_scene_frames(case.youtube_id, n=8)
    if not frames:
        return None
    messages, metadata = _build_vision_messages(frames)
    if not metadata:
        return None

    baseline = await run_vision(case, BASELINE_MODEL, BASELINE_LABEL, messages, metadata)
    candidates: dict[str, StageRun] = {}
    for label, model in VISION_CANDIDATES:
        candidates[label] = await run_vision(case, model, label, messages, metadata)
    result = StageBenchResult(
        stage="vision", case_id=case.video_summary_id, baseline=baseline,
        candidates=candidates,
    )
    if baseline.error or not isinstance(baseline.output, list):
        for label in candidates:
            result.scores[label] = 0.0
        return result
    for label, run in candidates.items():
        if run.error or not isinstance(run.output, list):
            result.scores[label] = 0.0
            result.score_extra[label] = {"error": run.error}
            continue
        v = scorers.score_vision(baseline.output, run.output)
        result.scores[label] = v["score"]
        result.score_extra[label] = v
    return result


# ─── Winner selection ──────────────────────────────────────────────────────


def pick_winner(
    stage: str, results_for_stage: list[StageBenchResult],
) -> tuple[str, dict[str, dict[str, float]]]:
    """Average quality/cost/speed per candidate across all videos, then pick."""
    labels = (
        [l for l, _ in CANDIDATE_MODELS] if stage != "vision"
        else [l for l, _ in VISION_CANDIDATES]
    )
    agg: dict[str, dict[str, float]] = {l: {"quality": 0.0, "cost": 0.0, "tps": 0.0, "n": 0.0} for l in labels}
    for r in results_for_stage:
        for label in labels:
            run = r.candidates.get(label)
            if run is None:
                continue
            agg[label]["quality"] += r.scores.get(label, 0.0)
            agg[label]["cost"] += run.cost_usd
            agg[label]["tps"] += run.tokens_per_sec
            agg[label]["n"] += 1
    for label in labels:
        n = agg[label]["n"]
        if n > 0:
            agg[label]["quality"] = round(agg[label]["quality"] / n, 4)
            agg[label]["cost"] = round(agg[label]["cost"] / n, 6)
            agg[label]["tps"] = round(agg[label]["tps"] / n, 1)

    # Highest quality first, then tie-break inside [top - threshold, top].
    sorted_labels = sorted(labels, key=lambda l: -agg[l]["quality"])
    top_quality = agg[sorted_labels[0]]["quality"]
    cluster = [l for l in sorted_labels if top_quality - agg[l]["quality"] <= QUALITY_TIE_THRESHOLD]
    rule = TIE_BREAK.get(stage, "cheapest")
    if rule == "cheapest":
        winner = min(cluster, key=lambda l: agg[l]["cost"])
    elif rule == "fastest":
        winner = max(cluster, key=lambda l: agg[l]["tps"])
    else:
        winner = cluster[0]
    return winner, agg


# ─── Report rendering ───────────────────────────────────────────────────────


def _sample_diff(stage: str, baseline_out: Any, cand_out: Any, model_label: str) -> str:
    """Render a short sample showing baseline vs candidate output for the report."""
    def _short(v: Any, limit: int = 300) -> str:
        if v is None:
            return "_(empty)_"
        if isinstance(v, str):
            return v[:limit] + ("…" if len(v) > limit else "")
        try:
            text = json.dumps(v, default=str)[:limit]
        except (TypeError, ValueError):
            text = repr(v)[:limit]
        return f"`{text}`"

    if stage == "synthesis" and baseline_out and cand_out:
        return (
            f"- baseline TLDR: {_short(baseline_out.tldr, 200)}\n"
            f"- {model_label} TLDR: {_short(cand_out.tldr, 200)}"
        )
    if stage == "vision" and isinstance(baseline_out, list) and isinstance(cand_out, list):
        if not baseline_out or not cand_out:
            return "_(no frames)_"
        b0, c0 = baseline_out[0], cand_out[0]
        return (
            f"- baseline frame 0: {b0.get('scene_type')} | {_short(b0.get('content'), 120)}\n"
            f"- {model_label} frame 0: {c0.get('scene_type')} | {_short(c0.get('content'), 120)}"
        )
    return f"- baseline: {_short(baseline_out)}\n- {model_label}: {_short(cand_out)}"


def render_report(
    corpus: list[CorpusCase],
    by_stage: dict[str, list[StageBenchResult]],
    winners: dict[str, tuple[str, dict[str, dict[str, float]]]],
    started_at: datetime,
    finished_at: datetime,
) -> str:
    out: list[str] = []
    out.append("# Fast-Tier Model Benchmark")
    out.append("")
    out.append(f"Started: {started_at.isoformat()}  ")
    out.append(f"Finished: {finished_at.isoformat()}  ")
    out.append(f"Duration: {(finished_at - started_at).total_seconds():.1f}s")
    out.append("")
    out.append("## Corpus")
    out.append("")
    out.append("| Domain | YouTube ID | Title | Duration | Matched tag |")
    out.append("|---|---|---|---|---|")
    for c in corpus:
        title = c.title[:60].replace("|", "\\|")
        out.append(f"| {c.domain_key} | `{c.youtube_id}` | {title} | {c.duration}s | {c.matched_tag} |")
    out.append("")

    for stage in ["chapter_detect", "description", "synthesis", "enrichment", "translation", "vision"]:
        if stage not in by_stage:
            continue
        results = by_stage[stage]
        winner_label, agg = winners[stage]
        labels_for_stage = list(agg.keys())

        out.append(f"## Stage: `{stage}`")
        out.append("")
        out.append(f"Tie-break rule: **{TIE_BREAK[stage]}**  |  Quality cluster threshold: {QUALITY_TIE_THRESHOLD}")
        out.append("")
        out.append("| Model | Quality | $/call | Latency (avg ms) | tok/sec | Errors |")
        out.append("|---|---|---|---|---|---|")
        # Baseline row
        base_avg_ms = sum(r.baseline.elapsed_ms for r in results) / max(len(results), 1)
        base_cost = sum(r.baseline.cost_usd for r in results) / max(len(results), 1)
        base_tps = sum(r.baseline.tokens_per_sec for r in results) / max(len(results), 1)
        base_errs = sum(1 for r in results if r.baseline.error)
        out.append(
            f"| `{BASELINE_LABEL}` (baseline) | 1.0000 (ref) | ${base_cost:.6f} "
            f"| {int(base_avg_ms)} | {base_tps:.1f} | {base_errs} |"
        )
        for label in labels_for_stage:
            stats = agg[label]
            errs = sum(1 for r in results if (r.candidates.get(label) and r.candidates[label].error))
            mark = "🏆 " if label == winner_label else ""
            out.append(
                f"| {mark}`{label}` | {stats['quality']:.4f} | ${stats['cost']:.6f} "
                f"| {int(sum(r.candidates[label].elapsed_ms for r in results if r.candidates.get(label)) / max(len(results), 1))} "
                f"| {stats['tps']:.1f} | {errs} |"
            )
        out.append("")
        out.append(f"**Winner**: `{winner_label}`")
        out.append("")
        # Sample diff: pick the first non-error case for the winner
        for r in results:
            wrun = r.candidates.get(winner_label)
            if wrun is not None and not wrun.error and not r.baseline.error:
                out.append("Sample (first video):")
                out.append("")
                out.append(_sample_diff(stage, r.baseline.output, wrun.output, winner_label))
                out.append("")
                break

        if stage == "vision":
            out.append("Per-frame detail (first video):")
            out.append("")
            r = results[0]
            for label in labels_for_stage:
                extra = r.score_extra.get(label, {})
                if "scene_matches" in extra:
                    out.append(
                        f"- `{label}`: scene_match={extra['scene_matches']}/{extra['total']}, "
                        f"text_match={extra['text_matches']}/{extra['total']}"
                    )
            out.append("")

    # Cost projection (sum of per-call cost across stages × candidates)
    out.append("## Cost projection per video")
    out.append("")
    out.append("| Stage | Today (`gpt-4o-mini`) | Winner | Δ |")
    out.append("|---|---|---|---|")
    today_total = 0.0
    new_total = 0.0
    for stage, (winner_label, agg) in winners.items():
        today = agg.get("gpt-4o-mini", {}).get("cost", 0.0)
        new = agg.get(winner_label, {}).get("cost", 0.0)
        today_total += today
        new_total += new
        delta = new - today
        delta_str = f"-${abs(delta):.6f}" if delta < 0 else f"+${delta:.6f}"
        out.append(f"| {stage} | ${today:.6f} | `{winner_label}` ${new:.6f} | {delta_str} |")
    out.append(f"| **TOTAL** | **${today_total:.6f}** | **${new_total:.6f}** | "
               f"**{'-' if new_total < today_total else '+'}${abs(new_total - today_total):.6f}** |")
    out.append("")

    # Proposed config diff
    out.append("## Proposed config diff (NOT auto-applied)")
    out.append("")
    out.append("```python")
    out.append("# services/summarizer/src/config.py")
    out.append("# MODEL_MAP — current default fast tier is openai/gpt-4o-mini")
    out.append("# Per-stage winners (operator picks providers to install):")
    for stage, (winner_label, _) in winners.items():
        model = _resolve_model_string(winner_label, stage)
        out.append(f"#   {stage}: {winner_label} ({model})")
    out.append("```")
    out.append("")
    out.append("> **HALT**: Review the report and approve before editing `config.py`.")
    return "\n".join(out)


def _resolve_model_string(label: str, stage: str) -> str:
    pool = VISION_CANDIDATES if stage == "vision" else CANDIDATE_MODELS
    for lbl, model in pool:
        if lbl == label:
            return model
    return "?"


# ─── Main ──────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output",
        type=str,
        default=str(REPORTS_DIR / f"fast-model-bench-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"),
    )
    parser.add_argument("--mongo-uri", type=str, default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017"))
    parser.add_argument("--mongo-db", type=str, default=os.environ.get("MONGODB_DB", "video-insight-engine"))
    parser.add_argument("--skip-vision", action="store_true", help="Skip frame vision benchmark")
    parser.add_argument("--dry-run", action="store_true", help="Print corpus selection only")
    parser.add_argument("--save-json", action="store_true", help="Also save raw results as JSON sidecar")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    started_at = datetime.now(timezone.utc)
    mongo = MongoClient(args.mongo_uri)
    db = mongo[args.mongo_db]

    callback = MongoDBUsageCallback(db, service="summarizer", mode="sync")
    litellm.callbacks = [callback]

    # 1. Select corpus
    corpus: list[CorpusCase] = []
    transcript_store = TranscriptStoreService()
    for domain_key, fallback_tags in DOMAIN_FALLBACKS.items():
        doc = _query_one_per_domain(db, domain_key, fallback_tags)
        if not doc:
            continue
        case = await hydrate_case(domain_key, doc, transcript_store)
        if case is None:
            logger.warning("Failed to hydrate corpus case for %s", domain_key)
            continue
        corpus.append(case)

    if not corpus:
        logger.error("No corpus cases — aborting.")
        return 1
    logger.info("Corpus ready: %d videos", len(corpus))
    for c in corpus:
        logger.info("  [%s] %s | %s | %ds", c.domain_key, c.youtube_id, c.title[:60], c.duration)

    if args.dry_run:
        return 0

    # 2. Run stages
    stages = ["chapter_detect", "description", "synthesis", "enrichment", "translation"]
    by_stage: dict[str, list[StageBenchResult]] = {s: [] for s in stages}
    for case in corpus:
        for stage in stages:
            logger.info("[%s] running stage=%s", case.youtube_id, stage)
            result = await benchmark_stage(stage, case)
            by_stage[stage].append(result)

    if not args.skip_vision:
        by_stage["vision"] = []
        for case in corpus:
            logger.info("[%s] running vision", case.youtube_id)
            v = await benchmark_vision(case)
            if v is not None:
                by_stage["vision"].append(v)

    # 3. Attach costs (give callback time to flush)
    await asyncio.sleep(1.5)
    flat_results = [r for results in by_stage.values() for r in results]
    costs = fetch_cost_rows(db)
    attach_costs(flat_results, costs)

    # 4. Pick winners
    winners: dict[str, tuple[str, dict[str, dict[str, float]]]] = {}
    for stage, results in by_stage.items():
        if not results:
            continue
        winners[stage] = pick_winner(stage, results)

    finished_at = datetime.now(timezone.utc)
    report = render_report(corpus, by_stage, winners, started_at, finished_at)

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    logger.info("Wrote Markdown report → %s", out_path)

    if args.save_json:
        json_path = out_path.with_suffix(".json")
        json_payload = {
            "schema": "vie.fast-bench/v1",
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "corpus": [asdict(c) for c in corpus],
            "by_stage": {
                stage: [_result_to_jsonable(r) for r in results]
                for stage, results in by_stage.items()
            },
            "winners": {stage: {"winner": w, "agg": agg} for stage, (w, agg) in winners.items()},
        }
        json_path.write_text(json.dumps(json_payload, indent=2, default=str))
        logger.info("Wrote JSON sidecar → %s", json_path)

    print()
    print("=" * 60)
    print("BENCHMARK COMPLETE — review report before applying config swaps")
    print(f"Report: {out_path}")
    print("=" * 60)
    return 0


def _result_to_jsonable(r: StageBenchResult) -> dict[str, Any]:
    def _run(run: StageRun) -> dict[str, Any]:
        return {
            "model_label": run.model_label,
            "elapsed_ms": run.elapsed_ms,
            "error": run.error,
            "cost_usd": run.cost_usd,
            "tokens_in": run.tokens_in,
            "tokens_out": run.tokens_out,
            "tokens_per_sec": run.tokens_per_sec,
        }
    return {
        "stage": r.stage,
        "case_id": r.case_id,
        "baseline": _run(r.baseline),
        "candidates": {label: _run(run) for label, run in r.candidates.items()},
        "scores": r.scores,
        "score_extra": r.score_extra,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
