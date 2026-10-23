"""A seek point written into an item's text: "… [timestamp ~100s]" (pC review).

A schema with no ``timestamp`` key leaves the model nowhere to put an item's second,
so it writes it into the prose: every one of S09's ten ``gaming.loadout`` notes ended
in "[timestamp ~Ns]", and assembly shipped the marker as row text. The info_grid row
and the spot card read an item through :func:`lift_stamp_marker`: a trailing marker
leaves the text and its second becomes the item's ``timestamp`` — whole seconds, and
only when the item has no stamp of its own (``clear_impossible_item_timestamps`` still
clears one past the video's end).
"""

from __future__ import annotations

import re

from .derived_guard import anchor_seconds

# "[timestamp ~100s]", "[Timestamp: 100 sec]", "[timestamp ~100.5 seconds]" at the end.
_MARKER = re.compile(
    r"\s*\[\s*timestamp\s*:?\s*~?\s*(\d{1,6})(?:\.\d+)?\s*(?:s|secs?|seconds)?\s*\]\s*$",
    re.IGNORECASE,
)


def split_stamp_marker(text: str) -> tuple[str, int | None]:
    """``text`` without its trailing marker, and the marker's second (None without one)."""
    match = _MARKER.search(text)
    if match is None:
        return text, None
    return text[: match.start()].rstrip(), int(match.group(1))


def lift_stamp_marker(item: dict) -> dict:
    """``item`` with a trailing marker cut from each of its string fields, the first
    marker's second as its ``timestamp`` when it has no stamp; the same dict when no
    field carries one."""
    split = {
        key: split_stamp_marker(value) for key, value in item.items() if isinstance(value, str)
    }
    marked = {key: pair for key, pair in split.items() if pair[1] is not None}
    if not marked:
        return item
    lifted = {**item, **{key: text for key, (text, _) in marked.items()}}
    if anchor_seconds(item) is None:
        lifted["timestamp"] = next(iter(marked.values()))[1]
    return lifted
