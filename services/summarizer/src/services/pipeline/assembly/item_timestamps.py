"""Out-of-range timestamps on non-moment items (pipeline-1min phase-1 close).

Extraction occasionally invents timestamps past the end of the video (dev
golden 2026-10-12: a 9:30 workout with exercises at 9:40-12:04). Moment
tabs already drop such moments (``_drop_impossible_timestamps``). For every
other component the item itself is real content — an exercise, a step, a
tip — so only its ``timestamp`` is cleared: the item stays, the "jump to"
link that could never seek goes.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def clear_impossible_item_timestamps(
    tabs: list[dict], video_duration: float | int | None, tolerance: float
) -> int:
    """Set ``timestamp`` to None on non-moment items past ``video_duration + tolerance``.

    Looks at the dict items of every list directly under a tab's ``props``.
    Returns the number of timestamps cleared.
    """
    try:
        limit = float(video_duration) if video_duration else 0.0
    except (TypeError, ValueError):
        return 0
    if limit <= 0:
        return 0
    limit += tolerance
    cleared = 0
    for tab in tabs:
        props = tab.get("props")
        if tab.get("component") == "moment_track" or not isinstance(props, dict):
            continue
        for value in props.values():
            if not isinstance(value, list):
                continue
            for item in value:
                seconds = _seconds(item.get("timestamp")) if isinstance(item, dict) else None
                if seconds is not None and seconds > limit:
                    item["timestamp"] = None
                    cleared += 1
    if cleared:
        logger.warning(
            "Assembly: cleared %d item timestamp(s) beyond video duration (%.0fs)",
            cleared,
            limit - tolerance,
        )
    return cleared
