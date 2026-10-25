"""Density enforcement — caps cell length and folds under-populated tabs.

`component_toolkit.txt` asks the model to keep cells short, but that guidance is
advisory only: the audited `info_grid` bug shipped 150–190-char paragraphs in a
single cell. This module caps cells in code for the compact layouts that cannot
show a paragraph (see `_CAPPED_MODES`), and folds a tab whose list shrinks below
the minimum. Runs in ``assemble_response`` after promotion and before validation.

output-quality 2.0 b (G1-2): no tab ships with empty props — every value None,
"", [] or {} — whatever its component: ``enforce_density`` folds one an assembler
built, ``drop_empty_tabs`` removes one assembly added on its own.
"""

from __future__ import annotations

import logging

from .sampling import is_described_track
from .text_utils import truncate_words

logger = logging.getLogger(__name__)

# component -> (list_key, text_fields_to_cap, char_cap, min_items)
# char_cap is the per-cell ceiling in a capped layout (`_CAPPED_MODES`);
# min_items folds the tab to None when too few rows survive normalization.
_DENSITY_RULES: dict[str, tuple[str, tuple[str, ...], int, int]] = {
    "info_grid": ("items", ("key", "value", "evidence"), 120, 1),
}
# Cells are capped only in the layouts that cannot show a paragraph (a table
# row, a tag chip). The default `key_value` cards render every value in full —
# cutting them at 120 chars destroyed the content they summarised (0b.4, L23).
# FE-mode forward-compat: nothing in the summarizer writes `props.mode` today
# (the planner emits no layout mode — plan.py keeps id/label/emoji/dataSource/
# component/goal/brief per tab), so every info_grid ships uncapped as
# `key_value`. The cap waits for a producer of InfoGridInteractive's `mode`.
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


def moment_gate_passes(props: dict) -> bool:
    """R7, the moment_track density gate: most moments say what they established
    (``is_described_track``). A label-only track — chapter jumps — fails it, and
    yields to a described fallback where the run has one
    (``moment_sources.described_or_fallback``)."""
    items = props.get("items")
    return isinstance(items, list) and is_described_track(items)


def has_content(value: object) -> bool:
    """False for None, a blank string, [] and {} — and a list or dict holding only those."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(has_content(member) for member in value.values())
    if isinstance(value, list | tuple):
        return any(has_content(member) for member in value)
    return True


def drop_empty_tabs(tabs: list[dict]) -> list[dict]:
    """Remove every tab whose props carry no content (output-quality 2.0 b, G1-2): an
    empty tab is a broken promise in the strip. Mutates ``tabs``; returns the removed."""
    removed = [tab for tab in tabs if not has_content(tab.get("props"))]
    if removed:
        tabs[:] = [tab for tab in tabs if has_content(tab.get("props"))]
        logger.warning(
            "[assembly] dropped %d tab(s) with empty props: %s",
            len(removed),
            [tab.get("id") for tab in removed],
        )
    return removed


def enforce_density(component: str, props: dict) -> dict | None:
    """Cap cell lengths in-place; return None to fold a too-sparse tab — and a tab
    whose props carry no content at all (``has_content``), whatever its component."""
    if not has_content(props):
        logger.info("[assembly] density folded %s (empty props)", component)
        return None
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
