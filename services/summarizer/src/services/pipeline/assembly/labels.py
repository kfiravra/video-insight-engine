"""Labels carry the shipped count (output-quality 0b.7 — R3, D7).

The leading-count rule and the count the hero strip shows for a tab, moved
out of ``core`` (which re-exports them).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Counts are true (R3, D7). The planner writes "9 Core Arguments" before
# extraction, so its number is a guess; the stored label carries the shipped
# count instead. Mirrors the FE rule (``LEADING_COUNT`` / ``inferItemCount``
# in apps/web/src/components/vie/cards/domain-meta.ts): a count claim is 1–3
# leading digits, whitespace, then a letter in any script — "7-Minute Workout"
# and "2024 Review" are names and never rewritten.
_LEADING_COUNT = re.compile(r"^(\d{1,3})\s+(?=[^\W\d_])")
# The list the hero strip counts per component (``COUNT_PROP_BY_COMPONENT``).
_LABEL_COUNT_KEYS: dict[str, str] = {
    "flash_deck": "cards",
    "step_player": "steps",
    "step_flow_canvas": "steps",
    "checklist": "items",
    "info_grid": "items",
    "spot_explorer": "spots",
    "moment_track": "items",
    "budget": "breakdown",
    "code_playground": "snippets",
    "quiz_arena": "questions",
    "packing_mission": "items",
    "workout_room": "exercises",
    "video_filmstrip": "frames",
    "concept_canvas": "concepts",
    "connect_canvas": "pairs",
    "lyrics_karaoke": "sections",
    "claims_tracker": "claims",
    "tier_list": "items",
    "formation_diagram": "positions",
}
_COMPARISON_COMPONENTS = frozenset({"comparison", "comparison_radar"})


def _list_len(props: Mapping[str, Any], key: str) -> int:
    value = props.get(key)
    return len(value) if isinstance(value, list) else 0


def shipped_count(component: str, props: Mapping[str, Any]) -> int | None:
    """The item count the strip shows for a tab (``inferItemCount``); None when ambiguous."""
    if component in _COMPARISON_COMPONENTS:
        total = sum(_list_len(props, key) for key in ("comparisons", "pros", "cons"))
        return total or None
    key = _LABEL_COUNT_KEYS.get(component)
    if key is None:
        return None
    if component == "moment_track" and not isinstance(props.get(key), list):
        key = "entries"  # the older moment_track shape
    return _list_len(props, key) if isinstance(props.get(key), list) else None


def true_count_label(label: str, count: int | None) -> str:
    """Replace a leading count claim with the shipped count; drop it when the count
    is unknown; return a label without a count claim verbatim."""
    rest = _LEADING_COUNT.sub("", label)
    if rest == label:
        return label
    return f"{count} {rest}" if count is not None else rest
