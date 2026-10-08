#!/usr/bin/env python3
"""Compare fast-model vs primary-model vision quality on 5 frames.

Downloads frames from S3 for a given youtubeId, sends them through the
vision LLM twice — once with ``use_fast_model=True`` (current deployed
default → ``LLM_FAST_PROVIDER``/`LLM_FAST_MODEL`) and once with
``use_fast_model=False`` (primary → ``LLM_MODEL`` or provider default).

Writes ``reports/frame-vision-spotcheck-<ts>.json`` with side-by-side
``scene_type`` / ``content`` / ``text_visible`` for each frame and a
short cost split via ``llm_usage`` records.

Designed to run **inside** the ``vie-summarizer`` container so it picks
up the same settings + AWS credentials the pipeline uses::

    docker-compose exec -T vie-summarizer \\
        python /app/scripts/spotcheck_frame_vision.py <youtubeId> [N]

The ``N`` arg overrides the frame count (default 5). The script never
writes back to MongoDB or S3 — only reads frames + records new
``llm_usage`` rows tagged with feature ``spotcheck:frames:fast`` and
``spotcheck:frames:primary``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the summarizer source tree importable when invoked from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_SRC = _REPO_ROOT / "services" / "summarizer"
if str(_SUMMARIZER_SRC) not in sys.path:
    sys.path.insert(0, str(_SUMMARIZER_SRC))

import boto3  # noqa: E402  type: ignore[import-untyped]
import litellm  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from llm_common import MongoDBUsageCallback  # noqa: E402
from llm_common.context import llm_feature_var, llm_video_id_var  # noqa: E402
from src.config import settings  # noqa: E402
from src.services.llm_provider import LLMProvider  # noqa: E402
from src.services.media.frame_analyzer import (  # noqa: E402
    _count_line,
    load_vision_prompt,
    parse_vision_response,
)

logger = logging.getLogger("spotcheck_frame_vision")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REPORTS_DIR = _REPO_ROOT / "reports"


def _download_frames(youtube_id: str, n: int) -> list[dict]:
    """Pull up to ``n`` scene frames from S3 to /tmp and return frame dicts."""
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
        raise RuntimeError(f"No frames in s3://{settings.S3_BUCKET}/{prefix}")

    keys = sorted(o["Key"] for o in contents if o["Key"].endswith((".jpg", ".jpeg", ".png")))
    keys = keys[:n]
    if not keys:
        raise RuntimeError(f"No .jpg/.png frames at {prefix}")

    out: list[dict] = []
    tmpdir = Path("/tmp") / f"vie_spotcheck_{youtube_id}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    for i, key in enumerate(keys):
        local = tmpdir / Path(key).name
        s3.download_file(settings.S3_BUCKET, key, str(local))
        out.append(
            {
                "index": i,
                "path": str(local),
                "s3_key": key,
                "timestamp": _parse_timestamp_from_key(key),
                "total_score": 1.0,  # ranking unused — we want all 5
            }
        )
    logger.info("Downloaded %d frames from s3://%s/%s", len(out), settings.S3_BUCKET, prefix)
    return out


def _parse_timestamp_from_key(_key: str) -> float:
    """scene_NNNN.jpg → numeric index. No actual timestamp survives; return 0."""
    return 0.0


def _encode_frame_base64(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1].lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(
            ext.lstrip("."), "image/jpeg"
        )
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except OSError as exc:
        logger.warning("Encode failed for %s: %s", path, exc)
        return None


def _build_vision_messages(frames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Mirror frame_analyzer._build inner logic; return (messages, metadata)."""
    content: list[dict] = [{"type": "text", "text": load_vision_prompt()}]
    meta: list[dict] = []
    for i, frame in enumerate(frames):
        data_uri = _encode_frame_base64(frame["path"])
        if not data_uri:
            continue
        ts = frame.get("timestamp", 0)
        mins = int(ts) // 60
        secs = int(ts) % 60
        # Labels count the frames actually sent, as production does.
        content.append({"type": "text", "text": f"Frame {len(meta)} (at {mins}:{secs:02d}):"})
        content.append({"type": "image_url", "image_url": {"url": data_uri}})
        meta.append({"index": i, "timestamp_sec": ts, "s3_key": frame["s3_key"]})
    content.append({"type": "text", "text": _count_line(len(meta))})
    return [{"role": "user", "content": content}], meta


async def _run_pass(
    label: str,
    messages: list[dict],
    metadata: list[dict],
    *,
    use_fast_model: bool,
) -> dict:
    provider = LLMProvider()
    started = time.monotonic()
    feature_token = llm_feature_var.set(f"spotcheck:frames:{label}")
    try:
        raw = await provider.complete_with_messages(
            messages, max_tokens=2000, timeout=90.0, use_fast_model=use_fast_model
        )
    finally:
        llm_feature_var.reset(feature_token)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    parsed = parse_vision_response(raw, metadata)
    model = provider._fast_model if use_fast_model else provider._model  # noqa: SLF001
    return {
        "label": label,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "raw_text": raw,
        "parsed": parsed,
    }


def _build_comparison(fast: dict, primary: dict) -> dict:
    by_idx_fast = {f["frame_index"]: f for f in fast["parsed"]}
    by_idx_primary = {f["frame_index"]: f for f in primary["parsed"]}
    indices = sorted(set(by_idx_fast) | set(by_idx_primary))
    rows: list[dict] = []
    scene_matches = 0
    text_diffs = 0
    for idx in indices:
        f = by_idx_fast.get(idx, {})
        p = by_idx_primary.get(idx, {})
        scene_match = f.get("scene_type") == p.get("scene_type")
        text_diff = (f.get("text_visible") or "").strip() != (p.get("text_visible") or "").strip()
        if scene_match:
            scene_matches += 1
        if text_diff:
            text_diffs += 1
        rows.append(
            {
                "frame_index": idx,
                "fast": {
                    "scene_type": f.get("scene_type"),
                    "content": f.get("content"),
                    "text_visible": f.get("text_visible"),
                    "educational_value": f.get("educational_value"),
                },
                "primary": {
                    "scene_type": p.get("scene_type"),
                    "content": p.get("content"),
                    "text_visible": p.get("text_visible"),
                    "educational_value": p.get("educational_value"),
                },
                "scene_type_match": scene_match,
                "text_visible_differs": text_diff,
            }
        )
    return {
        "total_frames": len(indices),
        "scene_type_matches": scene_matches,
        "text_visible_diffs": text_diffs,
        "rows": rows,
    }


def _query_usage_costs(features: list[str], since: datetime) -> dict:
    client = MongoClient(settings.MONGODB_URI)
    db = client.get_default_database()
    out: dict = {}
    for feat in features:
        cursor = db["llm_usage"].find(
            {"feature": feat, "timestamp": {"$gte": since}},
            {"_id": 0, "model": 1, "cost_usd": 1, "cache_creation_tokens": 1,
             "cache_read_tokens": 1, "cache_savings_usd": 1, "tokens_in": 1,
             "tokens_out": 1, "timestamp": 1},
        ).sort("timestamp", -1).limit(5)
        rows = list(cursor)
        out[feat] = {
            "rows": rows,
            "total_cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 6),
        }
    client.close()
    return out


async def main() -> int:
    description = (__doc__ or "").split("\n", maxsplit=1)[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("youtube_id", help="YouTube video ID with frames already in S3")
    parser.add_argument("-n", "--num-frames", type=int, default=5)
    args = parser.parse_args()

    youtube_id = args.youtube_id
    n = args.num_frames

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    llm_video_id_var.set(youtube_id)

    mongo = MongoClient(settings.MONGODB_URI)
    callback = MongoDBUsageCallback(mongo.get_default_database(), service="summarizer", mode="sync")
    litellm.callbacks = [callback]

    frames = _download_frames(youtube_id, n)
    if not frames:
        logger.error("No frames to analyze")
        return 1

    messages, metadata = _build_vision_messages(frames)
    if not metadata:
        logger.error("Failed to build vision messages (encoding failed for all frames)")
        return 1

    logger.info("Running FAST pass (use_fast_model=True) on %d frames…", len(metadata))
    fast_result = await _run_pass("fast", messages, metadata, use_fast_model=True)
    logger.info("FAST done in %dms (model=%s)", fast_result["elapsed_ms"], fast_result["model"])

    logger.info("Running PRIMARY pass (use_fast_model=False) on %d frames…", len(metadata))
    primary_result = await _run_pass("primary", messages, metadata, use_fast_model=False)
    logger.info("PRIMARY done in %dms (model=%s)", primary_result["elapsed_ms"], primary_result["model"])

    comparison = _build_comparison(fast_result, primary_result)
    cost = _query_usage_costs(["spotcheck:frames:fast", "spotcheck:frames:primary"], started_at)

    ts = started_at.strftime("%Y%m%d-%H%M%S")
    out_path = REPORTS_DIR / f"frame-vision-spotcheck-{ts}.json"
    payload = {
        "youtube_id": youtube_id,
        "started_at": started_at.isoformat(),
        "frame_count": len(metadata),
        "fast": {
            "model": fast_result["model"],
            "elapsed_ms": fast_result["elapsed_ms"],
            "cost": cost.get("spotcheck:frames:fast"),
            "parsed": fast_result["parsed"],
        },
        "primary": {
            "model": primary_result["model"],
            "elapsed_ms": primary_result["elapsed_ms"],
            "cost": cost.get("spotcheck:frames:primary"),
            "parsed": primary_result["parsed"],
        },
        "comparison": comparison,
    }
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)

    print("\n=== SPOT-CHECK SUMMARY ===")
    print(f"  Frames compared:        {comparison['total_frames']}")
    print(f"  Scene-type matches:     {comparison['scene_type_matches']}/{comparison['total_frames']}")
    print(f"  Text-visible diffs:     {comparison['text_visible_diffs']}/{comparison['total_frames']}")
    print(f"  Fast model:    {fast_result['model']:50}  cost=${cost.get('spotcheck:frames:fast', {}).get('total_cost_usd', 0):.6f}")
    print(f"  Primary model: {primary_result['model']:50}  cost=${cost.get('spotcheck:frames:primary', {}).get('total_cost_usd', 0):.6f}")
    print(f"\n  Report:        {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
