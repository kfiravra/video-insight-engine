"""Media downloads in ``pipeline.timing.downloads`` — one entry per file.

Every entry carries ``kind``, ``purpose``, ``startMs``, ``wallMs``, ``bytes``
(read from the finished file), ``ok`` and ``mbPerSec`` = bytes ÷ 10⁶ ÷ wall
seconds — the throughput the proxy exit gave this file (null without a file).
Downloaders add their own fields through ``detail``; the yt-dlp video
downloads record the rendition yt-dlp picked (``format``, ``height``), the
player clients and the proxy exit that delivered it (``clients``,
``proxyExit`` = "n/m", null when direct) and, on failure, a short
credential-free ``error``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from src.services.pipeline.pipeline_timing import current_timing

logger = logging.getLogger(__name__)


def _file_size(path: Path | None, kind: str) -> int | None:
    try:
        return path.stat().st_size if path is not None and path.exists() else None
    except OSError as exc:
        logger.debug("Timing download size lookup failed (%s): %s", kind, exc)
        return None


def record_download(
    *,
    kind: str,
    purpose: str,
    start_monotonic: float,
    path: Path | None,
    ok: bool,
    detail: Mapping[str, object] | None = None,
) -> None:
    """Record one media download. Never raises; a no-op outside a run."""
    recorder = current_timing()
    if recorder is None:
        return
    size = _file_size(path, kind) if ok else None
    start_ms = recorder.offset_ms(start_monotonic)
    wall_ms = recorder.offset_ms() - start_ms
    recorder.downloads.append(
        {
            "kind": kind,
            "purpose": purpose,
            "startMs": start_ms,
            "wallMs": wall_ms,
            "bytes": size,
            "mbPerSec": round(size / 1000 / wall_ms, 2) if size and wall_ms > 0 else None,
            "ok": ok,
            **(detail or {}),
        }
    )
