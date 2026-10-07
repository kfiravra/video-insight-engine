"""Hi-res refinement of selected scene frames.

Second pass of the two-pass frame pipeline: scene detection + scoring run on
a worst-quality download (fast), then this module re-extracts only the ~25
SELECTED frames at 720p. Refined bytes replace each frame's local ``path`` in
place, so S3 upload, vision analysis, and OCR all pick up the hi-res JPEG for
free.

Two sources, picked by whether ``YOUTUBE_PROXY_URL`` is set:

* proxyless — seek into a direct stream URL (no full download), falling back
  to one local 720p download when the CDN 403s or stalls every seek;
* proxied — the stream URL is never looked up (ffmpeg seeks leave from the
  blocked host IP); frames come from the local 720p file that
  ``hires_prefetch`` started alongside scene detection.

Failure is always graceful: any frame that cannot be refined keeps its
original low-res path.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import settings
from src.services.media.download_utils import ytdlp_proxy_url
from src.services.media.frame_extractor import extract_frame
from src.services.media.stream_url import get_video_stream_url

if TYPE_CHECKING:
    from src.services.media.hires_prefetch import LocalHiresSource

logger = logging.getLogger(__name__)

# Single source for the hires filename suffix — scene_extractor's dedup revert
# strips it to recover the original low-res path, so writer and reverter must
# agree exactly or duplicates get silently dropped instead of reverted.
HIRES_SUFFIX = ".hires.jpg"


async def refine_selected_frames(
    video_id: str,
    selected_frames: list[dict],
    local_source: LocalHiresSource | None = None,
) -> int:
    """Re-extract selected frames at 720p and swap their local paths in place.

    Args:
        video_id: YouTube video ID.
        selected_frames: Frame dicts from scene extraction. Each needs a
            ``path`` and a parsed ``timestamp`` (0.0 = unknown, skipped).
        local_source: Prefetched local file for proxied runs (see
            ``hires_prefetch``). The caller owns its lifetime.

    Returns:
        Number of frames successfully upgraded (0 = full low-res fallback).
    """
    if not settings.SCENE_HIRES_ENABLED:
        return 0

    # timestamp 0.0 = showinfo parse gap; without a timestamp there is nothing to seek to
    candidates = [f for f in selected_frames if f.get("timestamp", 0.0) > 0.0 and f.get("path")]
    if not candidates:
        return 0

    if local_source is not None:
        return await _refine_from_prefetched(video_id, candidates, local_source)
    if ytdlp_proxy_url():
        # Proxied caller without a prefetch: still never seek the stream URL.
        return await _refine_from_local_download(video_id, candidates)
    return await _refine_from_stream_url(video_id, candidates)


async def _refine_from_stream_url(video_id: str, candidates: list[dict]) -> int:
    """Proxyless path: seek the direct stream URL, local download as the fallback."""
    stream_url = await get_video_stream_url(video_id)
    if not stream_url:
        logger.warning("No 720p stream URL for %s — trying local-download fallback", video_id)
        return await _refine_from_local_download(video_id, candidates)

    try:
        results = await asyncio.wait_for(
            _refine_batch(stream_url, candidates),
            timeout=settings.SCENE_HIRES_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # Frames refined before the deadline already have their paths swapped.
        refined = _count_refined(candidates)
        logger.warning(
            "Hi-res refinement timed out for %s (%.0fs), %d/%d upgraded",
            video_id,
            settings.SCENE_HIRES_TIMEOUT,
            refined,
            len(candidates),
        )
        if refined == 0:
            # A CDN that stalls every seek is the same outcome as one that
            # 403s them — without the fallback the manifest stamps
            # hiresCount=0, invalidates its own cache, and the next run
            # repeats the full extraction forever.
            return await _refine_from_local_download(video_id, candidates)
        return refined

    refined = _summarize_results(video_id, candidates, results, "Hi-res refinement")
    if refined == 0:
        # 0/N with a "valid" URL = the CDN 403'd every plain-ffmpeg seek
        # (client-bound googlevideo URLs) — yt-dlp itself still downloads
        # fine, so fall back to one local 720p download + local seeks.
        return await _refine_from_local_download(video_id, candidates)
    return refined


async def _refine_batch(source: str, candidates: list[dict]) -> list[bool | BaseException]:
    """Run _refine_one for every candidate against one input source."""
    semaphore = asyncio.Semaphore(settings.SCENE_HIRES_CONCURRENCY)
    return await asyncio.gather(
        *[_refine_one(source, frame, semaphore) for frame in candidates],
        return_exceptions=True,
    )


async def _refine_from_prefetched(
    video_id: str, candidates: list[dict], local_source: LocalHiresSource
) -> int:
    """Proxied path: seek the prefetched local file (pass-1 reuse or 720p download)."""
    video_path = await local_source.path()
    if video_path is None:
        logger.warning("No local hi-res source for %s — keeping low-res frames", video_id)
        return 0
    label = "Hi-res (pass-1 reuse)" if local_source.reuses_pass1 else "Hi-res (local 720p)"
    return await _refine_local_batch(video_id, video_path, candidates, label)


async def _refine_from_local_download(video_id: str, candidates: list[dict]) -> int:
    """Fallback: download a 720p rendition once, extract locally (can't 403)."""
    from src.services.media.local_video import cleanup_local_video, download_video_720p

    downloaded = await download_video_720p(video_id, purpose="refiner")
    if not downloaded:
        logger.warning("Hi-res fallback unavailable for %s — keeping low-res frames", video_id)
        return 0
    video_path, temp_dir = downloaded
    try:
        return await _refine_local_batch(
            video_id, video_path, candidates, "Hi-res fallback (local 720p)"
        )
    finally:
        cleanup_local_video(temp_dir)


async def _refine_local_batch(
    video_id: str, video_path: Path, candidates: list[dict], label: str
) -> int:
    """Seek every candidate in a local file under the local-seek budget."""
    try:
        results = await asyncio.wait_for(
            _refine_batch(str(video_path), candidates),
            timeout=settings.SCENE_HIRES_FALLBACK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        refined = _count_refined(candidates)
        logger.warning(
            "%s timed out for %s (%.0fs), %d/%d upgraded",
            label,
            video_id,
            settings.SCENE_HIRES_FALLBACK_TIMEOUT,
            refined,
            len(candidates),
        )
        return refined
    return _summarize_results(video_id, candidates, results, label)


def _count_refined(candidates: list[dict]) -> int:
    return sum(1 for f in candidates if f["path"].endswith(HIRES_SUFFIX))


def _summarize_results(
    video_id: str, candidates: list[dict], results: list[bool | BaseException], label: str
) -> int:
    """Count upgrades and surface per-frame exceptions swallowed by gather()."""
    refined = sum(1 for r in results if r is True)
    # gather(return_exceptions=True) swallows per-frame exceptions into the
    # result list — surface the population or a systematic failure reads as
    # an unexplained "0/25 upgraded" at INFO.
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        logger.warning(
            "%s errors for %s: %d/%d frames failed (first: %r)",
            label,
            video_id,
            len(errors),
            len(candidates),
            errors[0],
        )
    logger.info("%s for %s: %d/%d frames upgraded", label, video_id, refined, len(candidates))
    return refined


async def _refine_one(stream_url: str, frame: dict, semaphore: asyncio.Semaphore) -> bool:
    """Extract one 720p frame and point ``frame['path']`` at the new file."""
    async with semaphore:
        frame_bytes = await extract_frame(stream_url, int(frame["timestamp"]))

    if not frame_bytes:
        return False

    hires_path = Path(f"{frame['path']}{HIRES_SUFFIX}")
    try:
        await asyncio.to_thread(hires_path.write_bytes, frame_bytes)
    except OSError as e:
        logger.warning("Failed to write hi-res frame %s: %s", hires_path, e)
        return False

    frame["path"] = str(hires_path)
    return True
