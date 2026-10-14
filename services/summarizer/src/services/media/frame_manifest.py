"""The frame manifest: the S3 sidecar that makes uploaded scene frames reusable.

``videos/{id}/{SCENE_S3_PREFIX}/manifest.json`` preserves per-frame timestamps
across S3 cache hits. Frames reused from S3 without timestamps are useless to
the assembler (frame->item matching is timestamp-based), so a scenes dir
without a valid manifest is treated as a cache miss and re-extracted. Beside
the frames it carries the gallery indices, the hi-res count (0 = a degraded run
or a deferred hi-res pass that never landed, also a miss, so quality self-heals
on the next run) and the vision descriptions (cached frames have no local path,
so vision cannot re-run on a hit). Scene extraction writes it; the vision pass
and the deferred hi-res pass (``hires_upgrade``) read-modify-write it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.config import settings
from src.services.media.s3_client import s3_client
from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)

_MANIFEST_FILENAME = "manifest.json"
# v2: adds hiresCount. The bump deliberately invalidates every v1 manifest —
# the 2026-08 CDN-403 era uploaded low-res detection frames under v1, and a
# cache hit would serve them forever (bypassCache does not bypass this cache).
_MANIFEST_VERSION = 2
_MANIFEST_MIN_FRAMES = 10


def manifest_key(video_id: str) -> str:
    return f"videos/{video_id}/{settings.SCENE_S3_PREFIX}/{_MANIFEST_FILENAME}"


def validate_manifest(manifest: dict | None) -> list[dict] | None:
    """Return the manifest's frame entries if usable, else None."""
    if not isinstance(manifest, dict) or manifest.get("version") != _MANIFEST_VERSION:
        return None
    # A run where zero frames were hi-res upgraded is a degraded artifact
    # (CDN 403s) — treat as a miss so the next run re-extracts; the local-
    # download fallback makes that retry succeed rather than loop.
    if settings.SCENE_HIRES_ENABLED and manifest.get("hiresCount") == 0:
        return None
    entries = manifest.get("frames")
    if not isinstance(entries, list) or len(entries) < _MANIFEST_MIN_FRAMES:
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        if not isinstance(entry.get("s3Key"), str) or not entry["s3Key"]:
            return None
        if not isinstance(entry.get("timestamp"), (int, float)):
            return None
    # An all-zero manifest is as useless as no manifest (corrupt writer)
    if not any(entry["timestamp"] > 0 for entry in entries):
        return None
    return entries


async def check_existing_frames(video_id: str) -> dict | None:
    """Reuse S3-cached frames when a valid manifest preserves timestamps.

    Returns the full 3-tier frames dict on a cache hit, or None when
    extraction is needed (no/invalid manifest — deterministic scene_%04d
    keys make re-extraction overwrite safely).
    """
    try:
        manifest = await s3_client.get_json(manifest_key(video_id))
        if not isinstance(manifest, dict):
            return None
        entries = validate_manifest(manifest)
        if entries is None:
            return None

        frames: list[dict] = []
        for entry in sorted(entries, key=lambda e: e.get("index", 0)):
            key = entry["s3Key"]
            frames.append(
                {
                    "index": entry.get("index", len(frames)),
                    "filename": entry.get("filename") or key.split("/")[-1],
                    "s3_key": key,
                    "s3_url": s3_client.generate_presigned_url(key),
                    "timestamp": float(entry["timestamp"]),
                }
            )

        gallery_indices = set(manifest.get("galleryIndices") or [])
        gallery = [f for f in frames if f["index"] in gallery_indices] or frames[:12]

        logger.info(
            "Frames already exist in S3 for %s (%d frames, manifest hit), skipping extraction",
            video_id,
            len(frames),
        )
        vision = manifest.get("visionDescriptions")
        return {
            "all_frames": frames,
            "selected_frames": frames,
            "gallery_frames": gallery,
            # Cached frames have no local path, so vision can't re-run on a
            # hit — the descriptions persisted after the fresh run stand in.
            "vision_descriptions": vision if isinstance(vision, list) else [],
        }

    except Exception as e:
        logger.debug("S3 frame manifest check failed (will extract): %s", e)
        return None


async def frames_cached(video_id: str, *, skip_cache: bool = False) -> bool:
    """True when a usable frame manifest exists — the run then downloads no video early.

    ``skip_cache`` (cold-media benchmark run) answers False without reading S3.
    """
    if skip_cache or not YOUTUBE_ID_RE.match(video_id):
        return False
    return await check_existing_frames(video_id) is not None


async def persist_vision_descriptions(video_id: str, descriptions: list[dict]) -> None:
    """Attach the vision pass's descriptions to the frame manifest. Best-effort.

    Cache hits return frames without local paths, so vision cannot run again;
    without this, every reprocess that hits the manifest silently loses the
    visual-context annotations, extraction hints, and assembly captions a
    fresh run had. Read-modify-write keeps the manifest the single artifact
    (no sidecar that could outlive a re-extraction and mismatch its frames).
    """
    if not descriptions:
        return
    await _update_manifest(video_id, {"visionDescriptions": descriptions}, "vision descriptions")


async def mark_manifest_hires(video_id: str, hires_count: int) -> None:
    """Record a deferred hi-res pass's count on the frame manifest (``hires_upgrade``)."""
    await _update_manifest(video_id, {"hiresCount": hires_count}, "the hi-res count")


async def _update_manifest(video_id: str, fields: dict, what: str) -> None:
    """Read-modify-write the frame manifest's ``fields``. Best-effort."""
    key = manifest_key(video_id)
    try:
        manifest = await s3_client.get_json(key)
        if not isinstance(manifest, dict):
            return
        manifest.update(fields)
        await s3_client.put_json(key, manifest)
    except Exception as e:
        logger.warning("Failed to persist %s for %s: %s", what, video_id, e)


async def write_manifest(
    video_id: str,
    uploaded: list[dict],
    gallery_frames: list[dict],
    hires_count: int = 0,
) -> None:
    """Persist per-frame timestamps so future cache hits stay usable. Best-effort."""
    manifest = {
        "version": _MANIFEST_VERSION,
        "videoId": video_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        # 0 = degraded run (every hi-res seek failed) or a deferred hi-res pass
        # not done yet (hires_upgrade sets it when it lands; a cancelled pass
        # leaves 0) — validate_manifest treats that as a cache miss so quality
        # self-heals on the next run.
        "hiresCount": hires_count,
        "frames": [
            {
                "index": f["index"],
                "filename": f.get("filename", ""),
                "s3Key": f["s3_key"],
                "timestamp": f.get("timestamp", 0.0),
                "totalScore": f.get("total_score"),
            }
            for f in uploaded
        ],
        "galleryIndices": [f["index"] for f in gallery_frames if f.get("s3_key")],
    }
    try:
        await s3_client.put_json(manifest_key(video_id), manifest)
    except Exception as e:
        logger.warning("Failed to write frame manifest for %s: %s", video_id, e)
