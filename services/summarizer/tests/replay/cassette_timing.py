"""Phase walls and media timings for ``build_cassette`` (derivation only).

Phase walls come from the LLM call intervals (start = end − latency) plus
the run total. The media fakes then need per-call latencies that make the
REAL frame orchestration take the recorded wall:

* worker-log sub-steps (``_LOGGED_STEPS``) where the log survived, else the
  frames window split by T1dQhQAm8Tc's proportions (flagged ``estimated``);
* the 720p prefetch lasts from the end of the low-res download until hi-res
  seeking starts (it overlaps scene detect, scoring and — HIGH tier — the
  vision reselect), so ``frames.hires`` = remaining wait + the seeks;
* per-call constants that no log records (``S3_OP_S``, ``FRAME_SEEK_S``) are
  estimates; residuals land within the 10 % fidelity gate.
"""

from __future__ import annotations

import math
from typing import Any

# One S3 JSON get/put/exists from the box (estimate — not logged anywhere).
S3_OP_S = 0.1
# One ffmpeg -ss extraction from a local 720p file on the prod box. Fitted
# from T1dQhQAm8Tc, where two independent windows agree: frames.hires 6.7 s
# = 6 rounds of 4 seeks (≈1.1 s/round), and the moment fill's 2.6 s left
# after its logged 11.7 s download = 2 rounds of (exists + seek + put).
FRAME_SEEK_S = 1.1
_HIRES_CONCURRENCY = 4  # settings.SCENE_HIRES_CONCURRENCY default
# Worker-log numbers for the recorded runs (A-DIGEST "Numbers to reuse").
_LOGGED_STEPS: dict[str, dict[str, float]] = {
    "T1dQhQAm8Tc": {
        "lowresDownload": 11.1,
        "sceneDetect": 21.2,
        "scoreSelect": 8.3,
        "hires": 6.7,
        "upload": 1.9,
        "momentDownload": 11.7,
    },
    "uC45_4nnEAI": {"lowresDownload": 22.0, "sceneDetect": 24.1, "momentDownload": 21.7},
}
_LOGGED_BYTES: dict[str, dict[str, int]] = {
    "uC45_4nnEAI": {"lowres": int(71.5 * 1_048_576), "720p": int(166.3 * 1_048_576)},
}
_REFERENCE = _LOGGED_STEPS["T1dQhQAm8Tc"]
_PRE_VISION_KEYS = ("lowresDownload", "sceneDetect", "scoreSelect")
_POST_VISION_KEYS = ("hires", "upload")
# T1dQhQAm8Tc: the moment-fill download was 11.7 s of a 14.3 s assembly.
_MOMENT_SHARE_OF_ASSEMBLY = 11.7 / 14.3


def span_calls(calls: list[dict[str, Any]], *spans: str) -> list[dict[str, Any]]:
    """Calls whose span or feature is one of ``spans``."""
    return [c for c in calls if c["span"] in spans or c["feature"] in spans]


def probe_span(spans: set[str]) -> str:
    """The tier-probe span of a recorded run: ``tier_probe`` since 1b.1, else the
    ``classifier`` it replaced (cassettes recorded before 1b.1)."""
    return "tier_probe" if "tier_probe" in spans else "classifier"


def phase_bounds(calls: list[dict[str, Any]], total_s: float) -> dict[str, float]:
    """Phase END offsets (s) derived from the LLM intervals."""
    synth_enrich = span_calls(calls, "synthesis", "enrichment")
    probe = probe_span({c["span"] for c in calls})
    return {
        "metadata": span_calls(calls, "description_analysis")[0]["_endS"],
        "transcript_frames": span_calls(calls, probe)[0]["_startS"],
        "plan": span_calls(calls, "plan")[-1]["_endS"],
        "extraction": max(c["_endS"] for c in span_calls(calls, "summarize:extraction")),
        "synthesis_enrichment": max(c["_endS"] for c in synth_enrich),
        "assembly": total_s,
    }


def phases_ms(bounds: dict[str, float]) -> dict[str, int]:
    """Phase walls (ms); visual injection is folded into transcript_frames."""
    phases, previous = {}, 0.0
    for name, end in bounds.items():
        phases[name] = int(round((end - previous) * 1000))
        previous = end
    phases["visual_inject"] = 0
    return phases


def _split(total: float, keys: tuple[str, ...]) -> dict[str, float]:
    weight_sum = sum(_REFERENCE[k] for k in keys)
    return {k: round(max(0.0, total) * _REFERENCE[k] / weight_sum, 3) for k in keys}


def _frame_windows(calls: list[dict], bounds: dict[str, float]) -> tuple[float, float, float]:
    """Seconds of the frames branch before, during and after the vision call."""
    start, end = bounds["metadata"], bounds["transcript_frames"]
    vision = span_calls(calls, "frame_vision")
    if not vision:
        return end - start, 0.0, 0.0
    return vision[0]["_startS"] - start, vision[0]["latencyMs"] / 1000, end - vision[0]["_endS"]


def _frame_steps(video_id: str, tier: str, calls: list[dict], bounds: dict) -> tuple[dict, bool]:
    """Logged frames sub-steps, or the window split T1dQhQAm8Tc's way (estimated)."""
    logged = _LOGGED_STEPS.get(video_id)
    if logged is not None:
        return dict(logged), False
    pre, _, post = _frame_windows(calls, bounds)
    if tier == "high":  # vision sits between scoring and hi-res
        steps = _split(pre - S3_OP_S, _PRE_VISION_KEYS)
        steps.update(_split(post - 2 * S3_OP_S, _POST_VISION_KEYS))
    else:
        steps = _split(pre - S3_OP_S, _PRE_VISION_KEYS + _POST_VISION_KEYS)
    assembly = bounds["assembly"] - bounds["synthesis_enrichment"]
    steps["momentDownload"] = round(assembly * _MOMENT_SHARE_OF_ASSEMBLY, 3)
    return steps, True


def _download(seconds: float, size: int | None) -> dict[str, Any]:
    return {"seconds": round(max(0.0, seconds), 3), "bytes": size}


def media_timings(
    video_id: str, tier: str, calls: list[dict], bounds: dict, hires_frames: int
) -> tuple[dict[str, float], dict[str, Any], bool]:
    """(sleeps, downloads, estimated) for the media fakes."""
    steps, estimated = _frame_steps(video_id, tier, calls, bounds)
    _, vision_s, _ = _frame_windows(calls, bounds)
    seek_wall = math.ceil(hires_frames / _HIRES_CONCURRENCY) * FRAME_SEEK_S
    overlap = steps["sceneDetect"] + steps.get("scoreSelect", 0.0) + steps.get("hires", 0.0)
    if tier == "high":
        overlap += vision_s
    sizes = _LOGGED_BYTES.get(video_id, {})
    moment = steps["momentDownload"]
    prefetch = overlap - seek_wall if hires_frames else moment
    downloads = {
        "lowres": _download(steps["lowresDownload"], sizes.get("lowres")),
        "hires720p": [_download(prefetch, sizes.get("720p")), _download(moment, sizes.get("720p"))],
    }
    sleeps = {
        "sceneDetect": steps["sceneDetect"],
        "scoreSelect": steps.get("scoreSelect", 0.0),
        "frameSeek": FRAME_SEEK_S,
        "s3Op": S3_OP_S,
        "sceneUpload": round(max(0.0, steps.get("upload", 0.0) - S3_OP_S), 3),
        "ocr": 0.0,
    }
    return sleeps, downloads, estimated
