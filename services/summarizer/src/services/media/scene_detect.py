"""Candidate frames for the frame pipeline: scene detection plus a zero-candidate ladder.

Rung 1 is FFmpeg scene detection at the configured threshold (0.3) on the local
pass-1 file. A fixed camera (an unboxing on a table, a lecture) can stay below
it for twenty minutes, so the same decode also logs every frame above the
ladder floor (0.15) to a side file: rung 2 costs no second decode, only local
seeks to those timestamps. When both rungs are empty, or the detection pass
failed, uniform sampling seeks evenly spaced timestamps (rung 3). A sparse
result (< 12 frames) on a video over two minutes gets the same uniform samples
on top — the static-camera supplement.

Seeks replace the old ``fps=1/step`` sampling pass: that one decoded the whole
file again (~20 s for 20 minutes of 360p), a seek into the local file costs
~0.1-0.3 s. Every frame dict matches the scene-detect shape (``index``,
``filename``, ``path``, ``timestamp``, ``temp_dir``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.config import settings
from src.services.pipeline.pipeline_timing import mark_step

logger = logging.getLogger(__name__)

LADDER_FLOOR = 0.15
# Hard cap on rung-1 frames for resource safety.
_MAX_DETECTED_FRAMES = 500
# Below this many candidates, longer videos get uniform samples on top.
_MIN_DETECTED_FRAMES = 12
_SUPPLEMENT_MIN_DURATION = 120
# Uniform sampling: about one frame every 15-60 s, at most this many.
_INTERVAL_SAMPLE_COUNT = 30
_MIN_INTERVAL_SECONDS = 15
_DEFAULT_DURATION = 300
# Rung-2 timestamps can number in the hundreds; seek an even spread of them.
_LADDER_MAX_SEEKS = 60
_SEEK_CONCURRENCY = 4
_SEEK_TIMEOUT = 30.0
_SEEK_BUDGET = 120.0
# ffmpeg >= 7 exits non-zero when the image2 output receives no frame at all;
# for scene detection that is the zero-candidate case, not a failure.
_EMPTY_OUTPUT_MARKER = "Nothing was written into output file"
_PTS_TIME_RE = re.compile(r"pts_time:\s*([\d.]+)")
# The score-file path is spliced into the filtergraph, where , : ; = ' [ ] \ are syntax.
_FILTER_SAFE_PATH_RE = re.compile(r"^[\w./-]+$")
_SCORES_FILENAME = "scene_scores.txt"


@dataclass(frozen=True)
class _ScenePass:
    """One detection decode: rung-1 frames and the rung-2 (floor) timestamps."""

    ok: bool
    frames: list[dict] = field(default_factory=list)
    floor_timestamps: list[float] = field(default_factory=list)


def _detect_timeout(duration_seconds: int | None) -> int:
    return min(300, max(60, int((duration_seconds or _DEFAULT_DURATION) * 0.3) + 30))


def _scale_filter() -> str:
    """Cap the width at SCENE_DETECT_SCALE_WIDTH and never upscale.

    The pass-1 file is ~360p: scaling it to 1024 wide added no pixels, only
    2.5x the vision image tokens (792 vs 314 per frame). Even width for mjpeg
    via trunc(/2)*2; ``-2`` keeps the height even too.
    """
    return f"scale='trunc(min(iw,{settings.SCENE_DETECT_SCALE_WIDTH})/2)*2':-2"


def _scene_filter(threshold: float, scores_path: Path | None) -> str:
    """Filtergraph for rung 1, also logging rung-2 scores when a side file is given.

    ``select`` scores every decoded frame and tags survivors with
    ``lavfi.scene_score``; ``metadata=select`` then keeps only rung-1 frames,
    so the JPEGs written are exactly what ``gt(scene, threshold)`` alone wrote.
    """
    tail = f"showinfo,{_scale_filter()}"
    if scores_path is None or threshold <= LADDER_FLOOR:
        return f"select='gt(scene,{threshold:.4f})',{tail}"
    return (
        f"select='gt(scene,{LADDER_FLOOR:.4f})',"
        f"metadata=print:key=lavfi.scene_score:file={scores_path},"
        f"metadata=select:key=lavfi.scene_score:value={threshold:.4f}:function=greater,"
        f"{tail}"
    )


def _collect_scene_frames(frames_dir: Path, stderr_text: str, temp_dir: str) -> list[dict]:
    """Rung-1 frames in output order, paired with the showinfo ``pts_time`` lines."""
    timestamps = [float(m) for m in _PTS_TIME_RE.findall(stderr_text)]
    paths = sorted(frames_dir.glob("scene_*.jpg"))[:_MAX_DETECTED_FRAMES]
    return [
        {
            "index": i,
            "filename": path.name,
            "path": str(path),
            "timestamp": timestamps[i] if i < len(timestamps) else 0.0,
            "temp_dir": temp_dir,
        }
        for i, path in enumerate(paths)
    ]


def _read_floor_timestamps(scores_path: Path | None) -> list[float]:
    if scores_path is None or not scores_path.exists():
        return []
    text = scores_path.read_text(encoding="utf-8", errors="replace")
    return [float(m) for m in _PTS_TIME_RE.findall(text)]


async def _kill(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass


async def _run_ffmpeg(cmd: list[str], timeout: float) -> tuple[int | None, str]:
    """(returncode, stderr); returncode None on timeout. FileNotFoundError propagates."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill(proc)
        return None, ""
    except asyncio.CancelledError:
        await _kill(proc)
        raise
    return proc.returncode, (stderr or b"").decode("utf-8", errors="replace")


async def _run_scene_pass(
    video: Path, frames_dir: Path, threshold: float, duration_seconds: int | None, temp_dir: str
) -> _ScenePass | None:
    """Rung 1 (+ rung-2 timestamps) in one decode; None when ffmpeg is missing."""
    scores_path = frames_dir / _SCORES_FILENAME
    if not _FILTER_SAFE_PATH_RE.match(str(scores_path)):
        scores_path = None
    cmd = [
        "ffmpeg",
        "-i",
        str(video),
        "-vf",
        _scene_filter(threshold, scores_path),
        "-vsync",
        "vfr",
        "-q:v",
        str(settings.SCENE_JPEG_QUALITY),
        str(frames_dir / "scene_%04d.jpg"),
        "-loglevel",
        "info",
        "-y",
    ]
    timeout = _detect_timeout(duration_seconds)
    try:
        rc, stderr_text = await _run_ffmpeg(cmd, timeout)
    except FileNotFoundError:
        logger.warning("ffmpeg not found, scene extraction unavailable")
        return None
    if rc is None:
        logger.warning("FFmpeg scene detection timed out (%ds) on %s", timeout, video.name)
        return _ScenePass(ok=False)
    frames = _collect_scene_frames(frames_dir, stderr_text, temp_dir)
    if rc != 0 and (frames or _EMPTY_OUTPUT_MARKER not in stderr_text):
        # A failed run can print a showinfo line for a frame its encoder never
        # wrote, which would shift every later timestamp onto the wrong JPEG.
        logger.warning(
            "FFmpeg scene detection failed (rc=%s) on %s: %s", rc, video.name, stderr_text[-300:]
        )
        return _ScenePass(ok=False)
    return _ScenePass(ok=True, frames=frames, floor_timestamps=_read_floor_timestamps(scores_path))


def uniform_timestamps(duration_seconds: int | None) -> list[float]:
    """Evenly spaced whole-second sample points at window midpoints (never t=0)."""
    duration = duration_seconds or _DEFAULT_DURATION
    step = max(_MIN_INTERVAL_SECONDS, duration // _INTERVAL_SAMPLE_COUNT)
    count = max(1, min(_INTERVAL_SAMPLE_COUNT, duration // step))
    # Whole seconds: the hi-res refiner seeks int(timestamp), so a fractional
    # sample would refine a frame up to a second away from the one scored.
    return [float(int(step * (i + 0.5))) for i in range(count)]


def _spread(timestamps: list[float], count: int) -> list[float]:
    """At most ``count`` timestamps, evenly picked across the sorted list."""
    ordered = sorted(timestamps)
    if len(ordered) <= count:
        return ordered
    stride = len(ordered) / count
    return [ordered[int(i * stride)] for i in range(count)]


async def _seek_frame(video: Path, timestamp: float, out_path: Path) -> bool:
    """One local-file seek rendered like a detection frame; False on any failure."""
    cmd = [
        "ffmpeg",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        _scale_filter(),
        "-q:v",
        str(settings.SCENE_JPEG_QUALITY),
        "-loglevel",
        "error",
        "-y",
        str(out_path),
    ]
    try:
        rc, _ = await _run_ffmpeg(cmd, _SEEK_TIMEOUT)
    except OSError as e:
        logger.debug("Frame seek at %.1fs failed to start: %s", timestamp, e)
        return False
    return rc == 0 and out_path.exists() and out_path.stat().st_size > 0


async def seek_frames(
    video: Path,
    frames_dir: Path,
    timestamps: list[float],
    *,
    prefix: str,
    index_offset: int,
    temp_dir: str,
) -> list[dict]:
    """Seek every timestamp in the local file; the frames that landed, in time order.

    Bounded by a total budget: on expiry the frames already written stand.
    """
    semaphore = asyncio.Semaphore(_SEEK_CONCURRENCY)
    landed: list[dict] = []

    async def _one(position: int, timestamp: float) -> None:
        path = frames_dir / f"{prefix}_{position + 1:04d}.jpg"
        async with semaphore:
            if not await _seek_frame(video, timestamp, path):
                return
        landed.append(
            {
                "index": index_offset + position,
                "filename": path.name,
                "path": str(path),
                "timestamp": timestamp,
                "temp_dir": temp_dir,
            }
        )

    try:
        await asyncio.wait_for(
            asyncio.gather(*(_one(i, ts) for i, ts in enumerate(timestamps))),
            timeout=_SEEK_BUDGET,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Frame seeks hit the %.0fs budget: %d/%d landed",
            _SEEK_BUDGET,
            len(landed),
            len(timestamps),
        )
    return sorted(landed, key=lambda f: f["timestamp"])


def _needs_uniform(frames: list[dict], duration_seconds: int | None) -> bool:
    if not frames:
        return True
    sparse = len(frames) < _MIN_DETECTED_FRAMES
    return sparse and (duration_seconds or 0) > _SUPPLEMENT_MIN_DURATION


async def _ladder(
    video: Path,
    frames_dir: Path,
    scene_pass: _ScenePass,
    duration_seconds: int | None,
    temp_dir: str,
) -> tuple[list[dict], str]:
    """Rungs 2 and 3 on top of rung 1's result; returns (frames, rung label)."""
    frames, rung = list(scene_pass.frames), "scene"
    if not frames and scene_pass.floor_timestamps:
        picks = _spread(scene_pass.floor_timestamps, _LADDER_MAX_SEEKS)
        frames = await seek_frames(
            video, frames_dir, picks, prefix="ladder", index_offset=0, temp_dir=temp_dir
        )
        rung = f"floor{LADDER_FLOOR}"
    if _needs_uniform(frames, duration_seconds):
        next_index = max((f["index"] for f in frames), default=-1) + 1
        uniform = await seek_frames(
            video,
            frames_dir,
            uniform_timestamps(duration_seconds),
            prefix="interval",
            index_offset=next_index,
            temp_dir=temp_dir,
        )
        rung = f"{rung}+uniform" if frames else "uniform"
        frames = sorted([*frames, *uniform], key=lambda f: f.get("timestamp", 0.0))
    return frames, rung


async def detect_candidate_frames(
    video: Path,
    frames_dir: Path,
    *,
    duration_seconds: int | None,
    temp_dir: str,
    threshold: float,
) -> list[dict]:
    """Candidate frames from the local pass-1 file; [] only when every rung failed."""
    detect_started = time.monotonic()
    scene_pass = await _run_scene_pass(video, frames_dir, threshold, duration_seconds, temp_dir)
    mark_step("frames.scene_detect", detect_started)
    if scene_pass is None:
        return []
    if not _needs_uniform(scene_pass.frames, duration_seconds):
        logger.info("Scene detection: %d frames (rung=scene)", len(scene_pass.frames))
        return scene_pass.frames

    ladder_started = time.monotonic()
    frames, rung = await _ladder(video, frames_dir, scene_pass, duration_seconds, temp_dir)
    mark_step("frames.scene_ladder", ladder_started)
    logger.info(
        "Scene ladder: %d scene frames, %d floor timestamps -> %d candidates (rung=%s)",
        len(scene_pass.frames),
        len(scene_pass.floor_timestamps),
        len(frames),
        rung,
    )
    return frames
