"""info_grid never stands alone (output-quality R6, task 1.6).

Key/value cells live inside the tab whose question they answer: S06's chef tips
inside the method, S07's pros inside the verdict, S10's coach tips inside the
workout, a talk's numbers inside its key ideas. The decision is the plan's, so
every reader sees one tab list (the per-group previews included):

- plan-done (``reconcile_pass.fold_step`` → :func:`fold_host`): a planned
  info_grid tab whose question another planned tab answers leaves the plan and
  rides on that host as ``folds: [{id, label, dataSource, brief}]``. The host is
  the planned tab whose component ``infoGridHosts`` (domains.json) ranks
  highest, a tab of the grid's own domain first among equals; with no host the
  grid stays a tab. ``field_requests`` asks for a fold's field on the host's
  behalf, so the host waits for it;
- assembly (:func:`apply_folds`): when the host ships, each fold's data is
  assembled as an info_grid and joins it — a spot_explorer host takes the rows
  as cards (key → name, value → description, the row's timestamp, evidence →
  quote, category badge) in a section named after the fold's label, a row with
  no value dropped (R5); any other host takes the grid as a bottom attachment.

A host that does not ship gives its folds back as tabs of their own
(:func:`unfolded_tabs`). A grid the assemblers only reach by demotion is no
planned tab and stays one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from src.shared_config.domain_config import info_grid_hosts

from .assemblers_primary import assemble_info_grid
from .normalizers import _normalize_to_spot
from .promotion import _info_row_to_spot

logger = logging.getLogger(__name__)

FOLDS_KEY = "folds"
INFO_GRID = "info_grid"
_SPOTS = "spot_explorer"

Resolve = Callable[[str, dict | None, dict | None], Any]


# ─── Plan-done: where a planned grid lives ───


def _domain(tab: Mapping[str, Any]) -> str:
    return str(tab.get("dataSource") or "").partition(".")[0]


def fold_host(
    tabs: Sequence[dict], grid: Mapping[str, Any], unsettled: Collection[str] = ()
) -> dict | None:
    """The planned tab ``grid`` folds into: the best-ranked host component, the
    grid's own domain first, the earliest first; never a tab whose fate is still
    open (``unsettled``: deferred to frames-done); None when no tab can host it."""
    ranked = info_grid_hosts()
    best: tuple[int, int, int] | None = None
    host: dict | None = None
    for index, tab in enumerate(tabs):
        component = str(tab.get("component") or "")
        if tab is grid or component not in ranked or tab.get("id") in unsettled:
            continue
        key = (ranked.index(component), int(_domain(tab) != _domain(grid)), index)
        if best is None or key < best:
            best, host = key, tab
    return host


_FOLD_KEYS = ("id", "label", "emoji", "goal", "dataSource", "brief")


def fold_entry(grid: Mapping[str, Any]) -> dict:
    """What a host carries for one folded grid — enough to ship it alone again."""
    entry = {key: grid[key] for key in _FOLD_KEYS if key in grid}
    return {**entry, "component": INFO_GRID}


# ─── Assembly: the folded cells join the host ───


def _row_spot(row: Any) -> dict | None:
    """One grid row as a spot card; None without a value (R5)."""
    if not isinstance(row, dict) or not str(row.get("value") or "").strip():
        return None
    spot = _normalize_to_spot(_info_row_to_spot(row))
    if spot is None:
        return None
    if row.get("evidence"):
        spot["quote"] = row["evidence"]
    if row.get("category"):
        spot["category"] = row["category"]
    return spot


def _into_spots(host: dict, grid_props: dict, label: str) -> None:
    """The grid's rows join the host's spots as a section named ``label``."""
    props = host["props"]
    spots = props.get("spots") if isinstance(props.get("spots"), list) else []
    added = [spot for spot in map(_row_spot, grid_props.get("items") or []) if spot is not None]
    if not added:
        return
    sections = list(props.get("sections") or [])
    if not sections:
        sections = [
            {"label": host.get("label") or host["id"], "spotIndices": list(range(len(spots)))}
        ]
    start = len(spots)
    sections.append({"label": label, "spotIndices": list(range(start, start + len(added)))})
    props["spots"] = [*spots, *added]
    props["sections"] = sections


def _as_attachment(host: dict, grid_props: dict) -> None:
    attachment = {"slot": "bottom", "component": INFO_GRID, "props": grid_props, "size": "strip"}
    host["attachments"] = [*(host.get("attachments") or []), attachment]


def _fold_props(
    fold: Mapping[str, Any], extraction: dict | None, enrichment: dict | None, resolve: Resolve
) -> dict | None:
    source = str(fold.get("dataSource") or "")
    props = assemble_info_grid(dict(fold), resolve(source, extraction, enrichment), {}, None)
    return props if props and props.get("items") else None


def apply_folds(
    host: dict,
    folds: Sequence[Mapping[str, Any]],
    extraction: dict | None,
    enrichment: dict | None,
    resolve: Resolve,
) -> None:
    """Assemble each folded grid over its own field and join it to the shipped host."""
    for fold in folds:
        props = _fold_props(fold, extraction, enrichment, resolve)
        if props is None:
            continue
        label = str(fold.get("label") or fold.get("id") or "")
        if host.get("component") == _SPOTS:
            _into_spots(host, props, label)
        else:
            _as_attachment(host, props)
        logger.info("Assembly: folded grid %r joins %r (R6)", fold.get("id"), host.get("id"))


def unfolded_tabs(
    folds: Sequence[Mapping[str, Any]],
    extraction: dict | None,
    enrichment: dict | None,
    resolve: Resolve,
) -> list[tuple[str, dict]]:
    """``(dataSource, tab)`` for each folded grid of a host that did not ship, back
    as a tab of its own — degrade, never drop: a lost host keeps no cells hostage."""
    tabs: list[tuple[str, dict]] = []
    for fold in folds:
        props = _fold_props(fold, extraction, enrichment, resolve)
        if props is None:
            continue
        tab_id = str(fold.get("id") or "")
        tab = {
            "id": tab_id,
            "label": fold.get("label", tab_id),
            "emoji": fold.get("emoji", ""),
            "component": INFO_GRID,
            "props": props,
            "goal": fold.get("goal", ""),
            "crossTabLinks": [],
        }
        tabs.append((str(fold.get("dataSource") or ""), tab))
    return tabs
