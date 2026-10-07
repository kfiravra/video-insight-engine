"""Scene-based keyframe extraction with smart frame selection (two-pass).

Pass 1 (detection): downloads lowest-quality video via yt-dlp to a temp
file — 144p is plenty for FFmpeg scene detection and local scoring, and
keeps the download small/fast. Selects ~25 best frames with even time
distribution.

Pass 2 (refinement): re-extracts only the SELECTED frames at 720p by
seeking into a direct stream URL (hires_refiner), so the JPEGs uploaded
to S3 and sent to the vision LLM are sharp. Falls back to the low-res
detection frames per-frame on any failure.

Key principle: detect cheap, refine selectively, upload selectively.
"""

import asyncio
import logging
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from src.config import settings
from src.services.media.image_dedup import compute_ahash, is_duplicate
from src.services.media.s3_client import s3_client
from src.services.pipeline.pipeline_timing import mark_step, record_download
from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)

# Per-video extraction locks to prevent duplicate concurrent extractions
_extraction_locks: dict[str, asyncio.Lock] = {}

# Upload all selected frames in a single concurrent batch (~25-30 frames).
# S3 handles thousands of concurrent PUTs; no need to chunk.
UPLOAD_BATCH_SIZE = 50

# Sidecar manifest preserving per-frame timestamps across S3 cache hits.
# Frames reused from S3 without timestamps are useless to the assembler
# (frame->item matching is timestamp-based), so a scenes dir without a
# valid manifest is treated as a cache miss and re-extracted.
_MANIFEST_FILENAME = "manifest.json"
# v2: adds hiresCount. The bump deliberately invalidates every v1 manifest —
# the 2026-08 CDN-403 era uploaded low-res detection frames under v1, and a
# cache hit would serve them forever (bypassCache does not bypass this cache).
_MANIFEST_VERSION = 2
_MANIFEST_MIN_FRAMES = 10

# Imported from the writer so the dedup revert can never drift from the
# filename the refiner actually produces.
from src.services.media.hires_refiner import HIRES_SUFFIX as _HIRES_SUFFIX

# Below this many scene-detect hits, supplement with interval sampling —
# static-camera videos (unboxings on a table) barely trigger scene cuts.
_MIN_DETECTED_FRAMES = 12
# Interval-sampled frames target: roughly one every 30-60s, capped.
_INTERVAL_SAMPLE_COUNT = 30


def _manifest_key(video_id: str) -> str:
    return f"videos/{video_id}/{settings.SCENE_S3_PREFIX}/{_MANIFEST_FILENAME}"


def _validate_manifest(manifest: dict | None) -> list[dict] | None:
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


async def _check_existing_frames(video_id: str) -> dict | None:
    """Reuse S3-cached frames when a valid manifest preserves timestamps.

    Returns the full 3-tier frames dict on a cache hit, or None when
    extraction is needed (no/invalid manifest — deterministic scene_%04d
    keys make re-extraction overwrite safely).
    """
    try:
        manifest = await s3_client.get_json(_manifest_key(video_id))
        if not isinstance(manifest, dict):
            return None
        entries = _validate_manifest(manifest)
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
    key = _manifest_key(video_id)
    try:
        manifest = await s3_client.get_json(key)
        if not isinstance(manifest, dict):
            return
        manifest["visionDescriptions"] = descriptions
        await s3_client.put_json(key, manifest)
    except Exception as e:
        logger.warning("Failed to persist vision descriptions for %s: %s", video_id, e)


async def _write_manifest(
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
        # 0 = degraded run (every hi-res seek failed) — _validate_manifest
        # treats that as a cache miss so quality self-heals on the next run.
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
        await s3_client.put_json(_manifest_key(video_id), manifest)
    except Exception as e:
        logger.warning("Failed to write frame manifest for %s: %s", video_id, e)


async def _upload_frames_batch(frames: list[dict]) -> list[dict]:
    """Upload frames to S3 in parallel batches.

    Uploads UPLOAD_BATCH_SIZE frames concurrently. Skips individual failures.
    Returns frames with s3_key populated.
    """
    results: list[dict] = []

    for batch_start in range(0, len(frames), UPLOAD_BATCH_SIZE):
        batch = frames[batch_start : batch_start + UPLOAD_BATCH_SIZE]

        async def _upload_one(frame: dict) -> dict | None:
            path = frame.get("path")
            s3_key = frame.get("s3_key")
            if not path or not s3_key:
                return None
            try:
                with open(path, "rb") as f:
                    frame_bytes = f.read()
                await s3_client.put_bytes(s3_key, frame_bytes, content_type="image/jpeg")
                return frame
            except Exception as e:
                logger.warning("Failed to upload frame %s: %s", s3_key, e)
                return None

        batch_results = await asyncio.gather(
            *[_upload_one(f) for f in batch],
            return_exceptions=True,
        )

        for result in batch_results:
            if isinstance(result, dict):
                results.append(result)

    return results


def _sample_by_time(frames: list[dict], count: int) -> list[dict]:
    """Pick up to `count` frames spread evenly across the timestamp span.

    Fallback selector for when scoring produced no usable ranking — divides
    the time range into `count` slots and takes the first frame in each, so
    coverage spans the whole video instead of clustering at the start.
    """
    if len(frames) <= count:
        return list(frames)

    ordered = sorted(frames, key=lambda f: f.get("timestamp", 0))
    span_start = ordered[0].get("timestamp", 0)
    span_end = ordered[-1].get("timestamp", 0)
    if span_end <= span_start:
        step = max(1, len(ordered) // count)
        return ordered[::step][:count]

    slot_width = (span_end - span_start) / count
    sampled: list[dict] = []
    cursor = 0
    for slot_idx in range(count):
        slot_end = span_start + (slot_idx + 1) * slot_width
        while cursor < len(ordered) and ordered[cursor].get("timestamp", 0) <= slot_end:
            if len(sampled) <= slot_idx:
                sampled.append(ordered[cursor])
            cursor += 1
    return sampled


async def _sample_interval_frames(
    temp_video: Path,
    frames_dir: Path,
    duration_seconds: int,
    temp_dir: str,
    index_offset: int,
) -> list[dict]:
    """Extract frames at fixed intervals — the static-camera supplement.

    Returns frame dicts shaped like the scene-detect ones (index continues
    after the detected set so S3 keys stay unique). Best-effort: any failure
    returns [] and the caller proceeds with whatever scene detection found.
    """
    step = max(15, duration_seconds // _INTERVAL_SAMPLE_COUNT)
    pattern = str(frames_dir / "interval_%04d.jpg")
    cmd = [
        "ffmpeg",
        "-i",
        str(temp_video),
        "-vf",
        f"fps=1/{step},scale={settings.SCENE_DETECT_SCALE_WIDTH}:-2",
        "-q:v",
        str(settings.SCENE_JPEG_QUALITY),
        pattern,
        "-loglevel",
        "error",
        "-y",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=120)
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
        logger.warning("Interval sampling failed (non-critical): %s", e)
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        return []

    frames: list[dict] = []
    for i, path in enumerate(sorted(frames_dir.glob("interval_*.jpg"))[:_INTERVAL_SAMPLE_COUNT]):
        frames.append(
            {
                "index": index_offset + i,
                "filename": path.name,
                "path": str(path),
                # fps=1/step (round=near) emits the input frame nearest each
                # sample instant i*step — NOT step/2 into the window. Offset
                # timestamps here would make the hires refiner seek different
                # content than what was scored, and skew vision labels +
                # frame→item matching by up to step/2 (30s at the 60s cap).
                "timestamp": float(i * step),
                "temp_dir": temp_dir,
            }
        )
    return frames


def _dedupe_refined_frames(frames: list[dict]) -> list[dict]:
    """Drop perceptually-duplicate frames after the hires second pass.

    The refiner seeks a remote stream by int(timestamp), so two picks under
    a second apart (or snapped to the same keyframe) can produce identical
    720p images even though their low-res detection frames were distinct.
    On collision, a refined frame is reverted to its original low-res JPEG
    (which passed uniqueness scoring); if still duplicate — or the original
    is gone — the frame is dropped. Unreadable frames are kept (fail-open).
    """
    kept: list[dict] = []
    kept_hashes: list[int] = []
    reverted = 0
    dropped = 0

    for frame in sorted(frames, key=lambda f: f.get("timestamp", 0.0)):
        path = frame.get("path")
        if not path:
            kept.append(frame)
            continue
        try:
            frame_hash = compute_ahash(Path(path).read_bytes())
        except Exception:
            kept.append(frame)
            continue

        if any(is_duplicate(frame_hash, h) for h in kept_hashes):
            original = path[: -len(_HIRES_SUFFIX)] if path.endswith(_HIRES_SUFFIX) else None
            if original:
                try:
                    frame_hash = compute_ahash(Path(original).read_bytes())
                except Exception:
                    dropped += 1
                    continue
                if any(is_duplicate(frame_hash, h) for h in kept_hashes):
                    dropped += 1
                    continue
                frame["path"] = original
                reverted += 1
            else:
                dropped += 1
                continue

        kept.append(frame)
        kept_hashes.append(frame_hash)

    if reverted or dropped:
        logger.info(
            "Post-hires dedup: %d -> %d frames (%d reverted, %d dropped)",
            len(frames),
            len(kept),
            reverted,
            dropped,
        )
    return kept


async def extract_scene_keyframes(
    video_id: str,
    scene_threshold: float | None = None,
    max_frames: int | None = None,
    duration_seconds: int | None = None,
    overselect_count: int | None = None,
    reselect_hook: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
) -> dict:
    """Extract keyframes at scene change boundaries with smart selection.

    Downloads lowest-quality video via yt-dlp, runs FFmpeg scene detection,
    scores all frames locally, selects ~25 best, uploads only those to S3.

    Args:
        video_id: YouTube video ID.
        scene_threshold: FFmpeg scene change threshold (0.0-1.0).
        max_frames: Maximum frames to extract from FFmpeg. Default from settings.
        duration_seconds: Video duration in seconds.

    Returns:
        Dict with 'all_frames', 'selected_frames', 'gallery_frames' keys.
        Empty dict on failure (graceful degradation).
    """
    empty_result: dict = {"all_frames": [], "selected_frames": [], "gallery_frames": []}

    if not settings.SCENE_EXTRACTION_ENABLED:
        return empty_result

    if not YOUTUBE_ID_RE.match(video_id):
        logger.warning("Invalid youtube_id for scene extraction: %s", video_id)
        return empty_result

    # Per-video lock prevents duplicate concurrent extractions (atomic setdefault)
    lock = _extraction_locks.setdefault(video_id, asyncio.Lock())

    async with lock:
        try:
            # Check S3 first — skip extraction if frames already exist
            existing = await _check_existing_frames(video_id)
            if existing:
                return existing

            return await _do_extraction(
                video_id,
                scene_threshold,
                max_frames,
                duration_seconds,
                overselect_count=overselect_count,
                reselect_hook=reselect_hook,
            )
        finally:
            # Prune lock after use to prevent unbounded dict growth
            _extraction_locks.pop(video_id, None)


async def _do_extraction(
    video_id: str,
    scene_threshold: float | None,
    max_frames: int | None,
    duration_seconds: int | None,
    overselect_count: int | None = None,
    reselect_hook: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
) -> dict:
    """Core extraction logic — FFmpeg + scoring + selective upload."""
    empty_result: dict = {"all_frames": [], "selected_frames": [], "gallery_frames": []}

    threshold = scene_threshold or settings.SCENE_THRESHOLD
    if not (0.0 < threshold < 1.0):
        logger.warning("Invalid scene threshold %.4f, using default 0.3", threshold)
        threshold = 0.3

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    temp_dir = tempfile.mkdtemp(prefix=f"vie-scene-{video_id}-")
    temp_video = Path(temp_dir) / f"{video_id}.mp4"
    frames_dir = Path(temp_dir) / "frames"
    frames_dir.mkdir()
    hires_source = None

    try:
        # Step 1: Download lowest quality video via yt-dlp
        dl_proc = None
        dl_started = time.monotonic()
        try:
            from src.services.media.download_utils import (
                ytdlp_client_cli_args,
                ytdlp_subprocess_env,
            )

            dl_proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "-f",
                "worstvideo[ext=mp4]/worst[ext=mp4]/worst",
                "--no-playlist",
                "--no-warnings",
                *ytdlp_client_cli_args(),
                "-o",
                str(temp_video),
                youtube_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=ytdlp_subprocess_env(),
            )
            _, dl_stderr = await asyncio.wait_for(dl_proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            logger.warning("yt-dlp download timed out for %s (120s)", video_id)
            record_download(
                kind="lowres",
                purpose="scene_detect",
                start_monotonic=dl_started,
                path=None,
                ok=False,
            )
            if dl_proc:
                try:
                    dl_proc.kill()
                    await dl_proc.wait()
                except ProcessLookupError:
                    pass
            return empty_result
        except FileNotFoundError:
            logger.warning("yt-dlp not found, scene extraction unavailable")
            return empty_result

        dl_ok = bool(dl_proc and dl_proc.returncode == 0 and temp_video.exists())
        record_download(
            kind="lowres",
            purpose="scene_detect",
            start_monotonic=dl_started,
            path=temp_video,
            ok=dl_ok,
        )
        if not dl_ok:
            stderr_text = dl_stderr.decode("utf-8", errors="replace")[:300] if dl_stderr else ""
            logger.warning(
                "yt-dlp download failed for %s (rc=%d): %s",
                video_id,
                dl_proc.returncode if dl_proc else -1,
                stderr_text,
            )
            return empty_result

        file_size_mb = temp_video.stat().st_size / 1_048_576
        logger.info("Downloaded temp video for %s: %.1fMB", video_id, file_size_mb)

        # Step 1b (proxied runs only): start the 720p download now so it
        # overlaps scene detection + scoring instead of following them.
        from src.services.media.hires_prefetch import start_local_hires

        hires_source = await start_local_hires(video_id, temp_video)

        # Step 2: FFmpeg scene detection on LOCAL file
        output_pattern = str(frames_dir / "scene_%04d.jpg")
        ffmpeg_cmd = [
            "ffmpeg",
            "-i",
            str(temp_video),
            "-vf",
            # -2 (not -1) keeps the auto height even, which mjpeg requires
            f"select='gt(scene,{threshold:.4f})',showinfo,scale={settings.SCENE_DETECT_SCALE_WIDTH}:-2",
            "-vsync",
            "vfr",
            "-q:v",
            str(settings.SCENE_JPEG_QUALITY),
            output_pattern,
            "-loglevel",
            "info",
            "-y",
        ]

        ff_proc = None
        detect_started = time.monotonic()
        try:
            ff_proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            dur = duration_seconds or 300
            ffmpeg_timeout = min(300, max(60, int(dur * 0.3) + 30))
            _, stderr_bytes = await asyncio.wait_for(ff_proc.communicate(), timeout=ffmpeg_timeout)
        except asyncio.TimeoutError:
            ffmpeg_timeout = min(300, max(60, int((duration_seconds or 300) * 0.3) + 30))
            logger.warning(
                "FFmpeg scene detection timed out for %s (%ds)", video_id, ffmpeg_timeout
            )
            if ff_proc:
                try:
                    ff_proc.kill()
                    await ff_proc.wait()
                except ProcessLookupError:
                    pass
            return empty_result
        except FileNotFoundError:
            logger.warning("ffmpeg not found, scene extraction unavailable")
            return empty_result

        mark_step("frames.scene_detect", detect_started)

        # Step 3: Parse pts_time from showinfo filter output
        timestamps: list[float] = []
        if stderr_bytes:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            for match in re.finditer(r"pts_time:\s*([\d.]+)", stderr_text):
                try:
                    timestamps.append(float(match.group(1)))
                except ValueError:
                    continue

        # Step 4: Collect frames (hard cap at 500 for resource safety)
        frame_paths = sorted(frames_dir.glob("scene_*.jpg"))[:500]
        if not frame_paths:
            logger.info("No scene keyframes extracted for %s", video_id)
            return empty_result

        all_frames: list[dict] = []
        for i, path in enumerate(frame_paths):
            all_frames.append(
                {
                    "index": i,
                    "filename": path.name,
                    "path": str(path),
                    "timestamp": timestamps[i] if i < len(timestamps) else 0.0,
                    "temp_dir": temp_dir,
                }
            )

        logger.info(
            "FFmpeg detected %d scene frames for %s (%d timestamps parsed)",
            len(all_frames),
            video_id,
            len(timestamps),
        )

        # Step 4b: static-camera fallback. Scene detection needs CUTS; a
        # fixed-camera video (box openings on a table, lectures) can yield a
        # handful of frames for 20+ minutes, starving every downstream
        # consumer. Supplement with interval-sampled frames so selection has
        # real coverage to choose from.
        if len(all_frames) < _MIN_DETECTED_FRAMES and (duration_seconds or 0) > 120:
            interval_frames = await _sample_interval_frames(
                temp_video, frames_dir, duration_seconds or 300, temp_dir, len(all_frames)
            )
            if interval_frames:
                logger.info(
                    "Static-camera fallback for %s: +%d interval frames (had %d)",
                    video_id,
                    len(interval_frames),
                    len(all_frames),
                )
                all_frames.extend(interval_frames)
                all_frames.sort(key=lambda f: f.get("timestamp", 0.0))

        # Step 5: Score all frames locally (CPU only, ~2-3s)
        from src.services.media.frame_scorer import score_all_frames, select_frames

        score_started = time.monotonic()
        try:
            scored_frames = await asyncio.to_thread(score_all_frames, all_frames)
        except Exception as e:
            logger.warning("Frame scoring failed, uploading first 25 as fallback: %s", e)
            scored_frames = all_frames

        # Step 6: Select ~25 best + classify ~12 for gallery
        selected_frames, gallery_frames = await asyncio.to_thread(
            select_frames,
            scored_frames,
            duration_seconds,
        )

        if not selected_frames:
            # Fallback (all scores zero / scorer degraded): sample by TIME so
            # coverage spans the whole video, and spread the gallery across the
            # selection. Index-stride + [:12] here previously biased both to
            # the first half of long videos.
            selected_frames = _sample_by_time(scored_frames, 25)
            gallery_frames = _sample_by_time(selected_frames, 12)
        mark_step("frames.score_select", score_started)

        # Step 6b (HIGH visual tier): over-select candidates and let the
        # injected hook (vision-informed, lives in phases/frames.py — this
        # module stays LLM-free) drop presenter-dominated frames BEFORE the
        # expensive hires refinement and upload. Hook failure or an
        # over-aggressive cull falls back to the local-score selection.
        if overselect_count and reselect_hook is not None:
            keep_count = len(selected_frames) or 25
            # Same zero-score exclusion the normal selection path applies —
            # black/unreadable frames would otherwise be base64'd into the
            # vision batch and could survive into hires refinement + upload.
            usable = [f for f in scored_frames if f.get("total_score", 0) > 0] or scored_frames
            candidates = _sample_by_time(usable, overselect_count)
            reselect_started = time.monotonic()
            try:
                kept = await reselect_hook(candidates)
            except Exception as e:
                logger.warning("Frame reselect hook failed (using local selection): %s", e)
                kept = None
            mark_step("frames.vision_reselect", reselect_started)
            if kept:
                selected_frames = _sample_by_time(kept, keep_count)
                gallery_frames = _sample_by_time(selected_frames, 12)
                logger.info(
                    "Vision reselect: %d candidates -> %d kept -> %d selected",
                    len(candidates),
                    len(kept),
                    len(selected_frames),
                )

        # Step 7: Re-extract selected frames at 720p (in-place path swap;
        # per-frame fallback to the low-res detection JPEG on failure)
        from src.services.media.hires_refiner import refine_selected_frames

        hires_started = time.monotonic()
        hires_count = await refine_selected_frames(
            video_id, selected_frames, local_source=hires_source
        )
        mark_step("frames.hires", hires_started)

        # Step 7b: Dedup AFTER refinement — CDN seeks by int(timestamp) can
        # collapse distinct low-res picks into near-identical 720p frames.
        selected_frames = await asyncio.to_thread(_dedupe_refined_frames, selected_frames)
        selected_ids = {id(f) for f in selected_frames}
        gallery_frames = [f for f in gallery_frames if id(f) in selected_ids]

        # Step 8: Upload ONLY selected frames to S3 (batch parallel)
        for f in selected_frames:
            f["s3_key"] = f"videos/{video_id}/{settings.SCENE_S3_PREFIX}/scene_{f['index']:04d}.jpg"

        upload_started = time.monotonic()
        uploaded = await _upload_frames_batch(selected_frames)

        # Gallery membership requires a SUCCESSFUL upload — s3_key was stamped
        # on every selected frame before the batch, so filtering on it alone
        # would keep entries whose PUT failed (dead URLs in this run's gallery
        # and dead galleryIndices in the manifest).
        uploaded_ids = {id(f) for f in uploaded}
        gallery_frames = [f for f in gallery_frames if id(f) in uploaded_ids]

        await _write_manifest(video_id, uploaded, gallery_frames, hires_count=hires_count)
        mark_step("frames.upload", upload_started)

        # Log top 5 scored frames for debugging
        top_5 = sorted(scored_frames, key=lambda f: f.get("total_score", 0), reverse=True)[:5]
        for rank, f in enumerate(top_5, 1):
            logger.info(
                "Top frame #%d: index=%d ts=%.1fs total=%.3f "
                "(visual=%.3f face=%.3f text=%.3f unique=%.3f)",
                rank,
                f.get("index", -1),
                f.get("timestamp", 0),
                f.get("total_score", 0),
                f.get("visual_score", 0),
                f.get("face_score", 0),
                f.get("text_score", 0),
                f.get("uniqueness_score", 0),
            )

        logger.info(
            "Scene extraction for %s: %d frames scored, %d uploaded to S3, %d gallery",
            video_id,
            len(scored_frames),
            len(uploaded),
            len(gallery_frames),
        )

        return {
            "all_frames": scored_frames,
            "selected_frames": uploaded,
            "gallery_frames": gallery_frames,
        }

    except Exception as e:
        logger.warning("Scene extraction failed for %s: %s", video_id, e)
        return empty_result
    finally:
        # Cancel/remove the prefetched 720p download on every exit path. The
        # nested finally keeps the temp-video unlink when close() re-raises a
        # cancel aimed at this task.
        try:
            if hires_source is not None:
                await hires_source.close()
        finally:
            # Delete temp VIDEO file immediately (large, ~5-15MB)
            try:
                if temp_video.exists():
                    temp_video.unlink()
            except Exception as cleanup_err:
                logger.debug("Failed to delete temp video: %s", cleanup_err)
            # NOTE: Don't delete frames_dir — OCR needs the frame JPEGs.
            # Cleanup via cleanup_temp_dir() after process_scene_frames().


async def cleanup_temp_dir(temp_dir: str) -> None:
    """Remove temp directory after OCR is done.

    Call AFTER process_scene_frames() has finished running OCR on the
    local frame files. Safe to call multiple times or with invalid paths.
    """
    try:
        await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
    except Exception:
        pass
