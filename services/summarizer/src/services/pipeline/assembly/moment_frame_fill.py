"""Exact-timestamp frame extraction for frameless moment_track items.

Moments are the video's VALUE index — a moment card without its image is a
broken promise. After the strict injection pass and the relaxed backfill,
items can still be frameless (no scene frame within ±15s, vision refusals,
within-tab exclusivity). This last-resort pass extracts a frame AT the
moment's own timestamp via an ffmpeg stream seek, so the image is the goal
image by definition — no vision filler-check applies.

Best-effort by design: any failure (no stream URL, extraction timeout, S3
down) leaves the item on its glyph-plate fallback. Budgeted so a worst-case
video adds tens of seconds, never minutes.

With ``YOUTUBE_PROXY_URL`` set the stream-URL pass is skipped outright: the
seeks would leave from the blocked host IP and 403, so the local 720p
download is the only pass.
"""

from __future__ import annotations

import asyncio
import logging

from src.services.media.download_utils import ytdlp_proxy_url
from src.services.media.frame_extractor import _frame_s3_key as frame_s3_key
from src.services.media.frame_extractor import extract_frame
from src.services.media.s3_client import s3_client
from src.services.media.stream_url import get_video_stream_url

from .core import _item_timestamp

logger = logging.getLogger(__name__)

# At most this many extractions per response — beyond it the video's moment
# coverage problem is upstream (extraction quality), not worth minutes of ffmpeg.
_FILL_MAX_FRAMES = 12
# Fast-path budget, including the stream-URL fetch. Partial fills stand.
_FILL_TOTAL_TIMEOUT = 60.0
# Local-download budget (one yt-dlp 720p download + local seeks) — the
# fallback when the CDN 403s the direct seeks, the only pass with a proxy.
_FILL_FALLBACK_TIMEOUT = 150.0
# The download gets the fallback budget minus this seek reserve, so a slow
# download is abandoned by yt-dlp itself (clean) rather than cancelled at the
# outer deadline with zero seconds left to extract anything from it.
_FILL_FALLBACK_SEEK_RESERVE = 30.0
# A full download isn't worth it for one or two glyph plates.
_FILL_FALLBACK_MIN_TARGETS = 3
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
    stream_url: str,
    youtube_id: str,
    item: dict,
    second: int,
) -> bool:
    """Extract/reuse the frame at `second`, upload, and stamp the item."""
    key = frame_s3_key(youtube_id, second)
    async with semaphore:
        if not await s3_client.exists(key):
            frame_bytes = await extract_frame(stream_url, second)
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


async def _url_pass(youtube_id: str, batch: list[FillTarget]) -> int:
    """Fast path: seek the direct stream URL for every target."""
    stream_url = await get_video_stream_url(youtube_id)
    if not stream_url:
        logger.warning("moment_frame_fill: no stream URL for %s", youtube_id)
        return 0
    return await _extract_batch(stream_url, youtube_id, batch)


async def _fallback_pass(youtube_id: str, batch: list[FillTarget]) -> int:
    """Local-download pass: fallback proxyless, the primary pass with a proxy.

    Client-bound googlevideo URLs 403 plain ffmpeg, but yt-dlp still downloads
    fine — grab a 720p rendition once and extract from the local file.
    """
    from src.services.media.local_video import cleanup_local_video, download_video_720p

    downloaded = await download_video_720p(
        youtube_id,
        timeout=_FILL_FALLBACK_TIMEOUT - _FILL_FALLBACK_SEEK_RESERVE,
        purpose="moment_fill",
    )
    if not downloaded:
        return 0
    video_path, temp_dir = downloaded
    try:
        return await _extract_batch(str(video_path), youtube_id, batch)
    finally:
        cleanup_local_video(temp_dir)


def _count_filled(targets: list[FillTarget]) -> int:
    return sum(1 for item, _ in targets if item.get("thumbnailUrl"))


async def _run_passes(youtube_id: str, capped: list[FillTarget]) -> int:
    """URL pass, then the local-download fallback for whatever is still frameless.

    Proxied runs skip the URL pass (unproxied seeks from the blocked IP) and
    go straight to the local download.
    """
    filled = 0
    if ytdlp_proxy_url():
        logger.info("moment_frame_fill: proxy set — skipping stream-URL pass for %s", youtube_id)
    else:
        try:
            filled = await asyncio.wait_for(
                _url_pass(youtube_id, capped), timeout=_FILL_TOTAL_TIMEOUT
            )
        except asyncio.TimeoutError:
            filled = _count_filled(capped)
            logger.warning(
                "moment_frame_fill: URL pass timed out after %.0fs — %d/%d filled",
                _FILL_TOTAL_TIMEOUT,
                filled,
                len(capped),
            )

    remaining = [(item, sec) for item, sec in capped if not item.get("thumbnailUrl")]
    if len(remaining) < _FILL_FALLBACK_MIN_TARGETS:
        return filled
    try:
        filled += await asyncio.wait_for(
            _fallback_pass(youtube_id, remaining), timeout=_FILL_FALLBACK_TIMEOUT
        )
    except asyncio.TimeoutError:
        filled = _count_filled(capped)
        logger.warning(
            "moment_frame_fill: fallback timed out after %.0fs — %d/%d filled",
            _FILL_FALLBACK_TIMEOUT,
            filled,
            len(capped),
        )
    return filled


async def fill_moment_frames(tabs: list[dict], youtube_id: str) -> int:
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

    capped = targets[:_FILL_MAX_FRAMES]
    if len(targets) > len(capped):
        logger.warning(
            "moment_frame_fill: %d frameless moments exceed the %d-frame cap — "
            "the overflow keeps the glyph-plate fallback",
            len(targets),
            _FILL_MAX_FRAMES,
        )

    try:
        filled = await _run_passes(youtube_id, capped)
    except Exception as e:  # noqa: BLE001 — strictly best-effort side quest
        logger.warning("moment_frame_fill: failed (%s: %s)", type(e).__name__, e)
        return _count_filled(capped)

    if filled:
        logger.info(
            "moment_frame_fill: +%d/%d moment images for %s", filled, len(capped), youtube_id
        )
    return filled
