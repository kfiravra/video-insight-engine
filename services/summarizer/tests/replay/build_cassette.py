"""Build a replay cassette from read-only dumps of one recorded run.

Inputs (all JSON, produced read-only — see the cassette's ``provenance``):

* ``--trace``   Langfuse ``GET /api/public/traces/{id}`` (generations with raw
  outputs and ``metadata.latencyMs``; ``startTime`` is the call END).
* ``--ledger``  ``llm_usage`` rows of the run (the description-analysis call
  predates task 0.2, so it exists only here: duration + end timestamp).
* ``--doc``     the ``videoSummaryCache`` doc (title, duration, ``processingTimeMs``,
  ``transcriptMeta``, ``meta.descriptionAnalysis`` = the description output).
* ``--s3``      ``{key: object}`` with ``videos/<id>/transcript.json`` and the
  ``<SCENE_S3_PREFIX>/manifest.json`` (frames, gallery, vision descriptions).
* ``--chapters`` optional ``[{start, end, title}]`` YouTube chapters.

Phase walls and the media fakes' latencies are derived in
``cassette_timing`` (worker-log sub-steps where the log survived, an
``estimated`` split otherwise).

Usage (from ``services/summarizer``)::

    .venv/bin/python -m tests.replay.build_cassette --video T1dQhQAm8Tc --env prod \\
        --trace lf.json --ledger ledger.json --doc doc.json --s3 s3.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import settings
from tests.replay.cassette import CASSETTE_DIR, CASSETTE_SCHEMA
from tests.replay.cassette_timing import (
    media_timings,
    phase_bounds,
    phases_ms,
    probe_span,
    span_calls,
)

_SKIPPED_SPANS = frozenset({"faithfulness"})
_FEATURE_BY_SPAN = {
    "description_analysis": "summarize:metadata",
    "frame_vision": "summarize:frames",
    "classifier": "summarize:classifier",
    "tier_probe": "summarize:tier_probe",
    "memory": "summarize:memory",
    "plan": "summarize:plan",
    "chapter_detect": "summarize:chapter_detect",
    "synthesis": "summarize:synthesis",
    "enrichment": "summarize:enrichment",
}
_HAIKU = "anthropic/claude-haiku-4-5-20251001"
_SONNET = "anthropic/claude-sonnet-4-6"
_MINI = "openai/gpt-4o-mini"
# The knobs that shaped the recorded runs (prod `.env`, A-DIGEST "Rate limits";
# dev used the same model pins). Stage models left None fall back to fast.
_RUN_SETTINGS: dict[str, Any] = {
    "LLM_MODEL": _SONNET,
    "LLM_FAST_MODEL": _MINI,
    # The tier probe's pin (D21, config default since 1b.1); prod never sets it.
    "LLM_CLASSIFIER_MODEL": _HAIKU,
    "LLM_CHAPTER_DETECT_MODEL": None,
    "LLM_DESCRIPTION_MODEL": None,
    "LLM_SYNTHESIS_MODEL": None,
    "LLM_ENRICHMENT_MODEL": _MINI,
    "LLM_EXTRACTION_MODEL": _HAIKU,
    "LLM_TRANSLATION_MODEL": None,
    "LLM_VISION_MODEL": _SONNET,
    "EXTRACTION_PARALLEL_BATCHES": 2,
    "CHUNKED_EXTRACTION_THRESHOLD": 900,
    "TRANSCRIPT_CLEANING_ENABLED": False,
    "FRAME_VISION_ENABLED": True,
    "FRAME_TIER_ENABLED": True,
    "SCENE_HIRES_ENABLED": True,
    "SSE_HEARTBEAT_SECONDS": 12.0,
    # Recorded runs were proxied (prefetch 720p, no stream-URL seeks). The
    # host is unresolvable and the network guard refuses it anyway.
    "YOUTUBE_PROXY_URL": "http://replay-proxy.invalid:9",
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_REDACTED = "[REDACTED]"


def _scrub(value: Any) -> Any:
    """Replace e-mail addresses anywhere in the cassette (same marker Langfuse uses)."""
    if isinstance(value, str):
        return _EMAIL.sub(_REDACTED, value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _unwrap(value: Any) -> Any:
    return value["$date"] if isinstance(value, dict) and "$date" in value else value


def _output_text(output: Any) -> str:
    return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)


# ─── LLM entries ───


def _trace_calls(trace: dict[str, Any], t0: float) -> list[dict[str, Any]]:
    calls = []
    for obs in trace["observations"]:
        span = obs["name"]
        if obs.get("type") != "GENERATION" or span in _SKIPPED_SPANS:
            continue
        meta = obs.get("metadata") or {}
        end = _ts(obs["startTime"]) - t0
        latency_ms = int(meta["latencyMs"])
        usage = obs.get("usage") or {}
        calls.append(
            {
                "feature": _FEATURE_BY_SPAN.get(span, "summarize:extraction"),
                "span": span,
                "model": obs["model"],
                "latencyMs": latency_ms,
                "output": _output_text(obs["output"]),
                "finishReason": meta.get("finishReason") or "stop",
                "usage": {
                    "input": int(usage.get("input") or 0),
                    "output": int(usage.get("output") or 0),
                    "cacheRead": int(meta.get("cacheReadTokens") or 0),
                    "cacheWrite": int(meta.get("cacheCreationTokens") or 0),
                },
                "_startS": end - latency_ms / 1000,
                "_endS": end,
            }
        )
    return calls


def _ledger_model(row: dict[str, Any]) -> str:
    """Prefixed model id; the ledger stores the bare name (provider often ``unknown``)."""
    for model in (_MINI, _HAIKU, _SONNET):
        if model.rsplit("/", 1)[-1] == row["model"]:
            return model
    return f"{row['provider']}/{row['model']}"


def _description_call(
    ledger: list[dict[str, Any]], doc: dict[str, Any], t0: float
) -> dict[str, Any]:
    """Rebuild the description-analysis completion from the ledger + stored meta."""
    row = next(r for r in ledger if r["feature"] == "summarize:metadata")
    analysis = dict(doc["meta"].get("descriptionAnalysis") or {})
    analysis["timestamps"] = [
        {"time": t["time"], "label": t["label"]} for t in analysis.get("timestamps", [])
    ]
    end = _ts(_unwrap(row["timestamp"])) - t0
    latency_ms = int(row["duration_ms"])
    return {
        "feature": "summarize:metadata",
        "span": "description_analysis",
        "model": _ledger_model(row),
        "latencyMs": latency_ms,
        "output": json.dumps(analysis, ensure_ascii=False),
        "finishReason": "stop",
        "usage": {
            "input": int(row.get("tokens_in") or 0),
            "output": int(row.get("tokens_out") or 0),
        },
        "_startS": end - latency_ms / 1000,
        "_endS": end,
    }


def _with_ordinals(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], int] = {}
    for call in sorted(calls, key=lambda c: c["_startS"]):
        key = (call["feature"], call["span"])
        call["ordinal"] = seen.get(key, 0)
        seen[key] = call["ordinal"] + 1
    return sorted(calls, key=lambda c: c["_startS"])


# ─── Video, transcript, frames ───


def _video_block(prompt: str) -> str:
    start = prompt.find("<video>")
    return prompt[start : prompt.find("</video>", start)] if start >= 0 else ""


def _user_prompt(trace: dict[str, Any], span: str) -> str:
    obs = next(o for o in trace["observations"] if o["name"] == span)
    content = obs["input"][-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def _video_spec(trace: dict[str, Any], doc: dict[str, Any], args: argparse.Namespace) -> dict:
    probe = probe_span({o["name"] for o in trace["observations"]})
    tags_line = next(
        (
            ln
            for ln in _video_block(_user_prompt(trace, probe)).splitlines()
            if ln.startswith("Tags:")
        ),
        "Tags:",
    )
    plan_video = _video_block(_user_prompt(trace, "plan"))
    description = (
        plan_video.split("Description:", 1)[-1].strip() if "Description:" in plan_video else ""
    )
    meta = doc["transcriptMeta"]
    return {
        "title": doc["title"],
        "channel": doc["creator"],
        "duration": int(doc["duration"]),
        "thumbnailUrl": doc.get("thumbnailUrl"),
        "description": description,
        "chapters": json.loads(Path(args.chapters).read_text()) if args.chapters else [],
        "category": args.category,
        "youtubeCategory": None,
        "tags": [t.strip() for t in tags_line[len("Tags:") :].split(",") if t.strip()],
        "language": doc.get("language") or "en",
        "captionTrack": meta.get("captionTrack"),
        "captionLang": meta.get("captionLang"),
    }


def _frame(index: int, timestamp: float, score: float) -> dict[str, Any]:
    return {"index": index, "timestamp": round(timestamp, 3), "totalScore": round(score, 4)}


def _frames_spec(manifest: dict[str, Any] | None, tier: str) -> dict[str, Any]:
    if not manifest:
        return {"mode": "zero", "selected": [], "galleryIndices": []}
    selected = [_frame(f["index"], f["timestamp"], f["totalScore"]) for f in manifest["frames"]]
    spec: dict[str, Any] = {
        "mode": tier,
        "selected": selected,
        "galleryIndices": manifest["galleryIndices"],
    }
    if tier == "high":
        # Scores descending in frame_index order so the vision call's top-N
        # sort reproduces the recorded frame_index → original_index mapping.
        spec["candidates"] = [
            _frame(d["original_index"], d["timestamp_sec"], 1.0 - d["frame_index"] / 1000)
            for d in manifest["visionDescriptions"]
        ]
    # Scene detection emitted at least every recorded index (index = position).
    recorded = [f["index"] for f in [*selected, *spec.get("candidates", [])]]
    spec["detectedCount"] = max(recorded) + 1
    return spec


# ─── Timing ───


def _sleeps(
    args: argparse.Namespace, calls: list[dict], bounds: dict, doc: dict, frames: dict
) -> tuple[dict, dict, bool]:
    """(sleeps, downloads, estimated) — LLM gaps + the media fakes' latencies."""
    hires_frames = sum(1 for f in frames["selected"] if f["timestamp"] > 0)
    media, downloads, estimated = media_timings(args.video, args.tier, calls, bounds, hires_frames)
    description = span_calls(calls, "description_analysis")[0]
    sleeps = {
        "metadata": round(bounds["metadata"] - description["latencyMs"] / 1000, 3),
        "transcript": doc["transcriptMeta"]["fetchWallMs"] / 1000,
        **media,
        "qdrantStore": 0.0,
    }
    return sleeps, downloads, estimated


# ─── Assembly of the cassette ───


def _request_id(trace: dict[str, Any]) -> str:
    tags = trace.get("tags") or []
    return next((t.split(":", 1)[1] for t in tags if t.startswith("requestId:")), "")


def _provenance(args: argparse.Namespace, trace: dict, doc: dict, has_frames: bool) -> dict:
    return {
        "environment": args.env,
        "langfuseTrace": trace["id"],
        "requestId": _request_id(trace),
        "videoSummaryId": doc["_id"],
        "recordedAt": trace["timestamp"],
        "pipelineVersion": doc.get("pipelineVersion", ""),
        "transcript": f"s3 videos/{args.video}/transcript.json",
        "frames": f"s3 {settings.SCENE_S3_PREFIX}/manifest.json"
        if has_frames
        else "none (zero scene frames)",
        "descriptionAnalysis": "llm_usage row + meta.descriptionAnalysis (no generation pre-0.2)",
    }


def _recorded(bounds: dict[str, float], doc: dict, estimated: bool, notes: list[str]) -> dict:
    return {
        "totalMs": doc["processingTimeMs"],
        "phasesMs": phases_ms(bounds),
        "milestonesMs": {
            "firstTabReadyMs": int(bounds["synthesis_enrichment"] * 1000),
            "completeMs": doc["processingTimeMs"],
        },
        "tabIds": list(doc.get("tabIds") or []),
        "estimated": estimated,
        "notes": notes,
    }


def _load_run(args: argparse.Namespace) -> tuple[dict, dict, list[dict], dict]:
    trace = json.loads(Path(args.trace).read_text())
    docs = json.loads(Path(args.doc).read_text())
    doc = next(d for d in docs if d["_id"] == args.doc_id)
    rows = json.loads(Path(args.ledger).read_text())
    ledger = [r for r in rows if r["video_summary_id"] == doc["_id"]]
    s3 = json.loads(Path(args.s3).read_text())[args.video]
    return trace, doc, ledger, s3


def build(args: argparse.Namespace) -> dict[str, Any]:
    """Assemble the cassette dict for ``args.video`` from the dump files."""
    trace, doc, ledger, s3 = _load_run(args)
    t0 = _ts(trace["timestamp"])
    calls = _with_ordinals([*_trace_calls(trace, t0), _description_call(ledger, doc, t0)])
    bounds = phase_bounds(calls, doc["processingTimeMs"] / 1000)
    manifest = s3.get(f"videos/{args.video}/{settings.SCENE_S3_PREFIX}/manifest.json") or {}
    has_frames = "frames" in manifest
    frames = _frames_spec(manifest if has_frames else None, args.tier)
    sleeps, downloads, estimated = _sleeps(args, calls, bounds, doc, frames)
    segments = s3[f"videos/{args.video}/transcript.json"]["segments"]
    return {
        "schema": CASSETTE_SCHEMA,
        "videoId": args.video,
        "provenance": _provenance(args, trace, doc, has_frames),
        "settings": _RUN_SETTINGS,
        "tier": args.tier,
        "video": _video_spec(trace, doc, args),
        "transcript": {
            "source": doc["transcriptMeta"]["source"],
            "segments": [[s["startMs"], s["endMs"], s["text"]] for s in segments],
        },
        "frames": frames,
        "downloads": downloads,
        "sleeps": sleeps,
        "llm": [{k: v for k, v in c.items() if not k.startswith("_")} for c in calls],
        "recorded": _recorded(bounds, doc, estimated, args.note or []),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "build_cassette").splitlines()[0])
    for name in ("--video", "--doc-id", "--trace", "--ledger", "--doc", "--s3", "--category"):
        parser.add_argument(name, required=True)
    parser.add_argument("--env", choices=("prod", "dev"), required=True)
    parser.add_argument("--tier", choices=("standard", "high", "low"), required=True)
    parser.add_argument("--chapters")
    parser.add_argument("--note", action="append")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cassette = _scrub(build(args))
    out = CASSETTE_DIR / f"{args.video}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cassette, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(cassette['llm'])} LLM entries)")


if __name__ == "__main__":
    main()
