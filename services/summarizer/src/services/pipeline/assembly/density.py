"""Density enforcement — caps cell length and folds under-populated tabs.

`component_toolkit.txt` asks the model to keep cells short, but that guidance is
advisory only: the audited `info_grid` bug shipped 150–190-char paragraphs in a
single cell. This module enforces the caps in code so a tab never renders
paragraph-length cells, and folds a tab whose list shrinks below the minimum.
Runs in ``assemble_response`` after promotion and before validation.
"""

from __future__ import annotations

import logging

from .text_utils import truncate_words

logger = logging.getLogger(__name__)

# component -> (list_key, text_fields_to_cap, char_cap, min_items)
# char_cap is the hard per-cell ceiling; min_items folds the tab to None when
# too few rows survive normalization.
_DENSITY_RULES: dict[str, tuple[str, tuple[str, ...], int, int]] = {
    "info_grid": ("items", ("key", "value", "evidence"), 120, 1),
}
# Cells are capped only in the layouts that cannot show a paragraph (a table
# row, a tag chip). The default `key_value` cards render every value in full —
# cutting them at 120 chars destroyed the content they summarised (0b.4, L23).
_CAPPED_MODES: dict[str, frozenset[str]] = {
    "info_grid": frozenset({"table", "tag_cloud"}),
}


def _truncate(text: str, cap: int) -> str:
    """Trim text to `cap` chars on a word boundary (shared helper)."""
    return truncate_words(text.strip(), cap)


def _caps_cells(component: str, props: dict) -> bool:
    """Whether this tab's layout needs its cells capped (see `_CAPPED_MODES`)."""
    modes = _CAPPED_MODES.get(component)
    return modes is None or props.get("mode") in modes


def enforce_density(component: str, props: dict) -> dict | None:
    """Cap cell lengths in-place; return None to fold a too-sparse tab."""
    rule = _DENSITY_RULES.get(component)
    if rule is None:
        return props
    list_key, fields, cap, min_items = rule
    items = props.get(list_key)
    if not isinstance(items, list):
        return props

    caps_cells = _caps_cells(component, props)
    kept: list = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in fields if caps_cells else ():
            value = item.get(field)
            if isinstance(value, str) and value:
                item[field] = _truncate(value, cap)
        kept.append(item)

    if len(kept) < min_items:
        logger.info(
            "[assembly] density folded %s (%d < %d items)",
            component,
            len(kept),
            min_items,
        )
        return None
    props[list_key] = kept
    return props
