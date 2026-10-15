"""Labels carry the shipped count (output-quality 0b.7 — R3, D7).

The planner writes "9 Core Arguments" before extraction, so its number is a
guess; post-processing replaces it with the count the tab ships. Both halves
mirror the FE (apps/web/src/components/vie/cards/domain-meta.ts): what counts as
a count claim (``LEADING_COUNT``) and what the strip counts (``inferItemCount``
over ``COUNT_PROP_BY_COMPONENT``). ``core`` re-exports the public names.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.shared_config.domain_config import component_tier

from .component_lists import COMPARISON_COMPONENTS, COMPARISON_LIST_KEYS, COMPONENT_LIST_KEYS

# A count claim is ``/^\d{1,3}\s+(?=\p{L})/u``: one to three ASCII digits (JS
# ``\d`` never takes another script's digits), whitespace, then a letter in any
# script — ``\p{L}`` is exactly ``str.isalpha``, checked after the match because
# Python's ``\d`` / ``[^\W\d_]`` would also take Arabic-Indic digits, "½" or "Ⅻ".
# "7-Minute Workout" and "2024 Review" are names and never rewritten.
_LEADING_COUNT = re.compile(r"^[0-9]{1,3}\s+")
# The list the hero strip counts per component (``COUNT_PROP_BY_COMPONENT``):
# every tab-tier component's own list. Attachment-only components never head a
# tab, and a comparison counts its rows, pros and cons together.
_LABEL_COUNT_KEYS: dict[str, str] = {
    component: key
    for component, key in COMPONENT_LIST_KEYS.items()
    if component_tier(component) == "primary" and component not in COMPARISON_COMPONENTS
}


def _list_len(props: Mapping[str, Any], key: str) -> int:
    value = props.get(key)
    return len(value) if isinstance(value, list) else 0


def shipped_count(component: str, props: Mapping[str, Any]) -> int | None:
    """The item count the strip shows for a tab (``inferItemCount``); None when ambiguous."""
    if component in COMPARISON_COMPONENTS:
        total = sum(_list_len(props, key) for key in COMPARISON_LIST_KEYS)
        return total or None
    key = _LABEL_COUNT_KEYS.get(component)
    if key is None:
        return None
    if component == "moment_track" and not isinstance(props.get(key), list):
        key = "entries"  # the older moment_track shape
    return _list_len(props, key) if isinstance(props.get(key), list) else None


def _after_count_claim(label: str) -> str | None:
    """The label after its leading count claim; None when it makes no count claim."""
    match = _LEADING_COUNT.match(label)
    rest = label[match.end() :] if match else ""
    return rest if rest[:1].isalpha() else None


def true_count_label(label: str, count: int | None) -> str:
    """Replace a leading count claim with the shipped count; drop it when the count
    is unknown; return a label without a count claim verbatim."""
    rest = _after_count_claim(label)
    if rest is None:
        return label
    return f"{count} {rest}" if count is not None else rest
