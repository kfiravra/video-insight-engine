"""Hi-res refinement of selected scene frames.

Second pass of the two-pass frame pipeline: scene detection + scoring run on
a worst-quality download (fast), then this module re-extracts only the ~25
SELECTED frames at 720p. Refined bytes replace each frame's local ``path`` in
place, so S3 upload, vision analysis, and OCR all pick up the hi-res JPEG for
free.

The source is the run's one local 720p file (``hires_prefetch``), downloaded
alongside scene detection and kept for moment fill. Failure is always
graceful: any frame that cannot be refined keeps its original low-res path.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import settings
from src.services.media.frame_extractor import extract_frame

if TYPE_CHECKING:
    from src.services.media.hires_prefetch import LocalHiresSource

logger = logging.getLogger(__name__)

# Single source for the hires filename suffix — scene_extractor's dedup revert
# strips it to recover the original low-res path, so writer and reverter must
# agree exactly or duplicates get silently dropped instead of reverted.
HIRES_SUFFIX = ".hires.jpg"
_LABEL = "Hi-res (local 720p)"


async def refine_selected_frames(
    video_id: str,
    selected_frames: list[dict],
    hires_video: LocalHiresSource | None,
) -> int:
    """Re-extract selected frames at 720p and swap their local paths in place.

    Args:
        video_id: YouTube video ID.
        selected_frames: Frame dicts from scene extraction. Each needs a
            ``path`` and a parsed ``timestamp`` (0.0 = unknown, skipped).
        hires_video: The run's 720p file. The run owns its lifetime.

    Returns:
        Number of frames successfully upgraded (0 = full low-res fallback).
    """
    if not settings.SCENE_HIRES_ENABLED or hires_video is None:
        return 0

    # timestamp 0.0 = showinfo parse gap; without a timestamp there is nothing to seek to
    candidates = [f for f in selected_frames if f.get("timestamp", 0.0) > 0.0 and f.get("path")]
    if not candidates:
        return 0

    video_path = await hires_video.path()
    if video_path is None:
        logger.warning("No local 720p file for %s — keeping low-res frames", video_id)
        return 0
    return await _refine_local_batch(video_id, video_path, candidates)


async def _refine_batch(source: str, candidates: list[dict]) -> list[bool | BaseException]:
    """Run _refine_one for every candidate against one input source."""
    semaphore = asyncio.Semaphore(settings.SCENE_HIRES_CONCURRENCY)
    return await asyncio.gather(
        *[_refine_one(source, frame, semaphore) for frame in candidates],
        return_exceptions=True,
    )


async def _refine_local_batch(video_id: str, video_path: Path, candidates: list[dict]) -> int:
    """Seek every candidate in the local file under the local-seek budget."""
    try:
        results = await asyncio.wait_for(
            _refine_batch(str(video_path), candidates),
            timeout=settings.SCENE_HIRES_FALLBACK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        refined = _count_refined(candidates)
        logger.warning(
            "%s timed out for %s (%.0fs), %d/%d upgraded",
            _LABEL,
            video_id,
            settings.SCENE_HIRES_FALLBACK_TIMEOUT,
            refined,
            len(candidates),
        )
        return refined
    return _summarize_results(video_id, candidates, results)


def _count_refined(candidates: list[dict]) -> int:
    return sum(1 for f in candidates if f["path"].endswith(HIRES_SUFFIX))


def _summarize_results(
    video_id: str, candidates: list[dict], results: list[bool | BaseException]
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
            _LABEL,
            video_id,
            len(errors),
            len(candidates),
            errors[0],
        )
    logger.info("%s for %s: %d/%d frames upgraded", _LABEL, video_id, refined, len(candidates))
    return refined


async def _refine_one(source: str, frame: dict, semaphore: asyncio.Semaphore) -> bool:
    """Extract one 720p frame and point ``frame['path']`` at the new file."""
    async with semaphore:
        frame_bytes = await extract_frame(source, int(frame["timestamp"]))

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
