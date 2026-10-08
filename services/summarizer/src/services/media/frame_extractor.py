"""Single-frame ffmpeg extraction at a timestamp.

``extract_frame`` seeks one JPEG out of any ffmpeg input — in the pipeline,
the run's local 720p file (hi-res refinement and moment fill).
``frame_s3_key`` names the on-demand single frames moment fill uploads.

Security:
- Timestamps are clamped to [0, 86400] to reject hallucinated LLM values
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_MIN_FRAME_BYTES = 5 * 1024  # 5KB — black/blank frames are typically <3KB
_MAX_TIMESTAMP_SECONDS = 86400  # 24 hours — reject absurd LLM-generated values


def frame_s3_key(youtube_id: str, timestamp_seconds: int) -> str:
    """S3 key of an on-demand single frame."""
    return f"videos/{youtube_id}/frames/{timestamp_seconds}.jpg"


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        logger.debug("ffmpeg already exited before the kill")


async def extract_frame(
    source: str,
    timestamp_seconds: int,
) -> bytes | None:
    """Extract a single JPEG frame at the given timestamp using ffmpeg.

    ``source`` is any ffmpeg input (the pipeline passes a local file path);
    -ss before -i seeks by keyframe first. Writes to a temporary file and
    returns bytes. Temp file is always cleaned up.

    Returns frame bytes on success, None on failure.
    """
    if timestamp_seconds < 0 or timestamp_seconds > _MAX_TIMESTAMP_SECONDS:
        logger.warning("Timestamp out of bounds (%d), skipping frame extraction", timestamp_seconds)
        return None

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(tmp_fd)

    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-ss",
                str(timestamp_seconds),
                "-i",
                source,
                "-vframes",
                "1",
                "-q:v",
                "2",
                "-y",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            logger.warning("Frame extraction error at %ds: %s", timestamp_seconds, e)
            return None

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()  # drain pipes
            logger.warning("ffmpeg timed out extracting frame at %ds", timestamp_seconds)
            return None
        except asyncio.CancelledError:
            # An outer budget expired (moment fill, hi-res refinement): stop
            # ffmpeg before the finally removes its output, or it outlives the
            # run and writes a stray jpg into the temp dir.
            await _kill(proc)
            raise

        if proc.returncode == 0 and os.path.exists(tmp_path):
            size = os.path.getsize(tmp_path)
            if size > 0:
                if size < _MIN_FRAME_BYTES:
                    logger.debug(
                        "Frame at %ds too small (%d bytes), likely blank", timestamp_seconds, size
                    )
                    return None
                with open(tmp_path, "rb") as f:
                    frame_bytes = f.read()
                logger.debug("Extracted frame at %ds (%.1fKB)", timestamp_seconds, size / 1024)
                return frame_bytes

        logger.warning(
            "ffmpeg failed for ts=%ds (rc=%s): %s",
            timestamp_seconds,
            proc.returncode,
            stderr.decode()[-200:] if stderr else "no output",
        )
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
