"""The deferred hi-res pass: frames-done never waits for the 720p file.

Scene extraction refines the selected frames from the run's 720p file only
when that download has already ended. While it is still running, extraction
uploads the 360p detection JPEGs under the frames' final S3 keys, writes the
manifest with ``hiresCount`` 0 and returns a ``HiresUpgrade``; the frames
phase starts ``upgrade_frames`` in the background once vision is done, and
the run awaits it — bounded — before it closes its downloads (g4 §8: a slow
proxy exit held frames-done 30-100 s for this file).

The upgrade seeks each frame in the 720p file and overwrites the same S3
object, so every frame URL already streamed shows the sharp image on its
next load, then records the hi-res count on the manifest (read-modify-write:
the vision descriptions stay). A 720p image that duplicates an earlier one
keeps its 360p object, and so does one whose seek (int(timestamp)) landed on
the shot before the 360p frame's cut. Best-effort: a failure logs and leaves the 360p
frames, and the manifest's ``hiresCount`` of 0 makes the next run extract
again.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.services.media.hires_refiner import HIRES_SUFFIX, refine_selected_frames
from src.services.media.image_dedup import compute_ahash, is_duplicate
from src.services.media.s3_client import s3_client
from src.services.pipeline.pipeline_timing import mark_step

if TYPE_CHECKING:
    from src.services.media.hires_prefetch import LocalHiresSource

logger = logging.getLogger(__name__)

# The refiner seeks int(timestamp) — up to 1 s before the cut a detection frame
# sits on. A 720p image may replace a 360p one only when it still shows the
# shot vision and OCR described: aHash Hamming distance within this many of 64
# bits (re-encoding moves a few; a cut far more).
_SAME_SHOT_BITS = 12


@dataclass(frozen=True)
class UpgradeFrame:
    """One uploaded frame the deferred pass refines: where to seek, where it lives."""

    index: int
    timestamp: float
    s3_key: str
    ahash: int | None = None  # the uploaded 360p image's; None (unreadable) = never replaced


@dataclass(frozen=True)
class HiresUpgrade:
    """The frames a run uploaded at 360p because its 720p file was still downloading."""

    video_id: str
    frames: tuple[UpgradeFrame, ...]


def _ahash_of(path: str | None) -> int | None:
    try:
        return compute_ahash(Path(path).read_bytes()) if path else None
    except Exception:  # noqa: BLE001 — an unreadable frame is simply never replaced
        return None


def _build_upgrade(video_id: str, uploaded: list[dict]) -> HiresUpgrade:
    frames = tuple(
        UpgradeFrame(
            int(f["index"]),
            float(f.get("timestamp") or 0.0),
            str(f["s3_key"]),
            _ahash_of(f.get("path")),
        )
        for f in uploaded
        if f.get("s3_key")
    )
    return HiresUpgrade(video_id, frames)


async def deferred_upgrade(video_id: str, uploaded: list[dict]) -> HiresUpgrade:
    """The upgrade for the frames extraction just uploaded (``s3_key`` + local 360p ``path``)."""
    return await asyncio.to_thread(_build_upgrade, video_id, uploaded)


def _replaceable(frames: list[dict]) -> list[dict]:
    """Refined frames still on their 360p frame's shot, minus 720p duplicates of earlier ones."""
    kept: list[dict] = []
    hashes: list[int] = []
    for frame in sorted(frames, key=lambda f: f["timestamp"]):
        shot = frame["ahash"]
        hires = _ahash_of(frame["path"]) if frame["path"].endswith(HIRES_SUFFIX) else None
        if hires is None or shot is None or not is_duplicate(hires, shot, _SAME_SHOT_BITS):
            continue
        if any(is_duplicate(hires, h) for h in hashes):
            continue
        hashes.append(hires)
        kept.append(frame)
    return kept


async def _overwrite(frames: list[dict]) -> int:
    """Put each refined JPEG over its frame's S3 object; the number written."""

    async def _put(frame: dict) -> bool:
        data = await asyncio.to_thread(Path(frame["path"]).read_bytes)
        await s3_client.put_bytes(frame["s3_key"], data, content_type="image/jpeg")
        return True

    results = await asyncio.gather(*(_put(f) for f in frames), return_exceptions=True)
    failed = [r for r in results if isinstance(r, BaseException)]
    if failed:
        logger.warning(
            "Deferred hi-res upload: %d/%d failed (%r)", len(failed), len(frames), failed[0]
        )
    return len(results) - len(failed)


async def upgrade_frames(upgrade: HiresUpgrade, hires_video: LocalHiresSource) -> int:
    """Refine the uploaded frames from the 720p file once it lands; the number upgraded."""
    from src.services.media.scene_extractor import mark_manifest_hires

    started = time.monotonic()
    temp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix=f"vie-upgrade-{upgrade.video_id}-")
    try:
        frames = [
            {
                "index": f.index,
                "timestamp": f.timestamp,
                "s3_key": f.s3_key,
                "ahash": f.ahash,
                "path": str(Path(temp_dir) / f"scene_{f.index:04d}.jpg"),
            }
            for f in upgrade.frames
        ]
        if not await refine_selected_frames(upgrade.video_id, frames, hires_video):
            return 0
        written = await _overwrite(await asyncio.to_thread(_replaceable, frames))
        if written:
            await mark_manifest_hires(upgrade.video_id, written)
        logger.info(
            "Deferred hi-res pass for %s: %d/%d frames", upgrade.video_id, written, len(frames)
        )
        return written
    except Exception as e:  # noqa: BLE001 — the 360p frames stand
        logger.warning("Deferred hi-res pass failed for %s: %s", upgrade.video_id, e)
        return 0
    finally:
        mark_step("frames.hires_upgrade", started)
        await asyncio.to_thread(shutil.rmtree, temp_dir, True)
