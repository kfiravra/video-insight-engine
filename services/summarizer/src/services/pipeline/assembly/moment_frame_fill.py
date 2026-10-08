"""Exact-timestamp frame extraction for frameless moment_track items.

Moments are the video's VALUE index — a moment card without its image is a
broken promise. After the strict injection pass and the relaxed backfill,
items can still be frameless (no scene frame within ±15s, vision refusals,
within-tab exclusivity). This last-resort pass extracts a frame AT the
moment's own timestamp with an ffmpeg seek, so the image is the goal image by
definition — no vision filler-check applies.

The seeks run against the run's one local 720p file (``hires_prefetch``),
which scene extraction already downloaded; a run that skipped scene
extraction (manifest cache hit) starts that download here — capped so the
seeks keep part of the budget — and only when enough moments need it.
Best-effort by design: any failure (no file, extraction timeout, S3 down)
leaves the item on its glyph-plate fallback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.services.media.frame_extractor import extract_frame, frame_s3_key
from src.services.media.s3_client import s3_client

from .core import _item_timestamp

if TYPE_CHECKING:
    from src.services.media.hires_prefetch import LocalHiresSource

logger = logging.getLogger(__name__)

# At most this many extractions per response — beyond it the video's moment
# coverage problem is upstream (extraction quality), not worth minutes of ffmpeg.
_FILL_MAX_FRAMES = 12
# Whole-fill budget: waiting for the 720p file (normally already on disk)
# plus the local seeks. Partial fills stand.
_FILL_TIMEOUT = 150.0
# A download the fill starts itself gets the budget minus this, so a slow
# download can't hold `complete` for the whole budget and leave no time to seek.
_FILL_SEEK_RESERVE = 30.0
# Starting the 720p download just for one or two glyph plates isn't worth it;
# once the file exists (or is on its way), every frameless moment is filled.
_FILL_DOWNLOAD_MIN_TARGETS = 3
_FILL_CONCURRENCY = 3

FillTarget = tuple[dict, int]


def _collect_frameless(tabs: list[dict]) -> list[FillTarget]:
    """(item, int_timestamp) for every frameless moment_track item.

    Within-tab dedup: two frameless moments whose timestamps floor to the
    same second would extract the identical frame — only the first claims it.
    """
    targets: list[FillTarget] = []
    for tab in tabs:
        if tab.get("component") != "moment_track":
            continue
        props = tab.get("props")
        items = props.get("items") if isinstance(props, dict) else None
        if not isinstance(items, list):
            continue
        claimed: set[int] = set()
        for item in items:
            if not isinstance(item, dict) or item.get("thumbnailUrl"):
                continue
            ts = _item_timestamp(item)
            if ts is None or ts < 0:
                continue
            second = int(ts)
            if second in claimed:
                continue
            claimed.add(second)
            targets.append((item, second))
    return targets


async def _fill_one(
    semaphore: asyncio.Semaphore,
    source: str,
    youtube_id: str,
    item: dict,
    second: int,
) -> bool:
    """Extract/reuse the frame at `second`, upload, and stamp the item."""
    key = frame_s3_key(youtube_id, second)
    async with semaphore:
        if not await s3_client.exists(key):
            frame_bytes = await extract_frame(source, second)
            if not frame_bytes:
                return False
            await s3_client.put_bytes(key, frame_bytes, content_type="image/jpeg")
    item["thumbnailUrl"] = s3_client.generate_presigned_url(key)
    item["s3Key"] = key
    return True


async def _extract_batch(source: str, youtube_id: str, batch: list[FillTarget]) -> int:
    """Run _fill_one for every target against one input source; count successes."""
    semaphore = asyncio.Semaphore(_FILL_CONCURRENCY)
    results = await asyncio.gather(
        *[_fill_one(semaphore, source, youtube_id, item, sec) for item, sec in batch],
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        logger.warning(
            "moment_frame_fill: %d/%d extractions errored (first: %r)",
            len(errors),
            len(batch),
            errors[0],
        )
    return sum(1 for r in results if r is True)


async def _fill_from_local(
    youtube_id: str, targets: list[FillTarget], hires_video: LocalHiresSource
) -> int:
    """Seek every target in the run's 720p file (awaiting it if still downloading)."""
    video_path = await hires_video.path()
    if video_path is None:
        logger.warning("moment_frame_fill: no local 720p file for %s", youtube_id)
        return 0
    return await _extract_batch(str(video_path), youtube_id, targets)


def _count_filled(targets: list[FillTarget]) -> int:
    return sum(1 for item, _ in targets if item.get("thumbnailUrl"))


def _cap_targets(targets: list[FillTarget]) -> list[FillTarget]:
    capped = targets[:_FILL_MAX_FRAMES]
    if len(targets) > len(capped):
        logger.warning(
            "moment_frame_fill: %d frameless moments exceed the %d-frame cap — "
            "the overflow keeps the glyph-plate fallback",
            len(targets),
            _FILL_MAX_FRAMES,
        )
    return capped


async def _fill_within_budget(
    youtube_id: str, capped: list[FillTarget], hires_video: LocalHiresSource
) -> int:
    """_fill_from_local under _FILL_TIMEOUT; partial fills stand, never raises."""
    try:
        return await asyncio.wait_for(
            _fill_from_local(youtube_id, capped, hires_video), timeout=_FILL_TIMEOUT
        )
    except asyncio.TimeoutError:
        filled = _count_filled(capped)
        logger.warning(
            "moment_frame_fill: timed out after %.0fs — %d/%d filled",
            _FILL_TIMEOUT,
            filled,
            len(capped),
        )
        return filled
    except Exception as e:  # noqa: BLE001 — strictly best-effort side quest
        logger.warning("moment_frame_fill: failed (%s: %s)", type(e).__name__, e)
        return _count_filled(capped)


async def fill_moment_frames(
    tabs: list[dict], youtube_id: str, hires_video: LocalHiresSource | None
) -> int:
    """Guarantee moment_track image coverage; returns the number filled.

    Mutates items in place (thumbnailUrl + s3Key). Never raises — every
    failure path logs and returns what was filled so far.
    """
    targets = _collect_frameless(tabs)
    if not targets:
        return 0
    if not s3_client.is_available():
        logger.info("moment_frame_fill: S3 unavailable — skipping %d targets", len(targets))
        return 0
    if hires_video is None:
        return 0

    capped = _cap_targets(targets)
    if not hires_video.started:
        if len(capped) < _FILL_DOWNLOAD_MIN_TARGETS:
            logger.info(
                "moment_frame_fill: %d frameless moments — not worth a 720p download", len(capped)
            )
            return 0
        hires_video.start(timeout=_FILL_TIMEOUT - _FILL_SEEK_RESERVE)

    filled = await _fill_within_budget(youtube_id, capped, hires_video)
    if filled:
        logger.info(
            "moment_frame_fill: +%d/%d moment images for %s", filled, len(capped), youtube_id
        )
    return filled
