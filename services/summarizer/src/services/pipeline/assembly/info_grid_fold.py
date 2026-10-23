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
  as cards (key → name, value → description, the row's timestamp, its evidence —
  the card's quote only when it is the item's checked quote, else prose after the
  description (R10) — and category badge) in a section named after the fold's
  label, a row with no value dropped (R5); any other host takes the grid as a
  bottom attachment.
  Each side is capped as it would ship alone first (``cap_rows`` for the grid,
  the caller for the host), so folded rows never cut the host's own items.
  A fold keeps the rows its brief planned it for (pC review, S04): only a row inside
  one of the brief's ``where`` windows (an unstamped row stays), never one a spot
  host already shows (a row within ``SAME_ITEM_SECONDS`` of a host card's second —
  the same item under its other name), and a fold left with no row joins as
  nothing. Its section label loses its emoji, as a tab label does.

A host that does not ship gives its folds back as tabs of their own
(:func:`unfolded_tabs`). A grid the assemblers only reach by demotion is no
planned tab and stays one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from src.shared_config.domain_config import info_grid_hosts
from src.utils.data_helpers import parse_timestamp_to_seconds

from .assemblers_primary import assemble_info_grid
from .derived_guard import anchor_seconds
from .normalizers import _normalize_to_spot
from .promotion import _info_row_to_spot, checked_quotes, spot_evidence

logger = logging.getLogger(__name__)

FOLDS_KEY = "folds"
INFO_GRID = "info_grid"
_SPOTS = "spot_explorer"

Resolve = Callable[[str, dict | None, dict | None], Any]
# A folded row this close to a host card's second is that card again (S04: 15 of 20
# rows of "Agent Workflow" sat on a key idea's second, a few one second off).
SAME_ITEM_SECONDS = 5
# How far past a brief window a row may sit and still be the fold's: the drift a
# stamp may carry (the gates' ± 30 s).
_WINDOW_SLACK_S = 30


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


def _row_spot(row: Any, quotes: dict[str, dict]) -> dict | None:
    """One grid row as a spot card; None without a value (R5). Its evidence is the
    card's quote only when it is the item's checked quote (``spot_evidence``, R10)."""
    if not isinstance(row, dict) or not str(row.get("value") or "").strip():
        return None
    spot = _normalize_to_spot(_info_row_to_spot(row))
    if spot is None:
        return None
    spot_evidence(spot, row, quotes)
    if row.get("category"):
        spot["category"] = row["category"]
    return spot


def _shown_by_host(row: Any, host_seconds: list[int]) -> bool:
    """Whether a host card sits within ``SAME_ITEM_SECONDS`` of ``row``'s second."""
    seconds = anchor_seconds(row)
    return seconds is not None and any(
        abs(seconds - shown) <= SAME_ITEM_SECONDS for shown in host_seconds
    )


def _into_spots(host: dict, grid_props: dict, label: str, quotes: dict[str, dict]) -> None:
    """The grid's rows join the host's spots as a section named ``label`` — each row
    the host does not already show."""
    props = host["props"]
    spots = props.get("spots") if isinstance(props.get("spots"), list) else []
    shown = [s for s in (anchor_seconds(spot) for spot in spots) if s is not None]
    rows = [row for row in grid_props.get("items") or [] if not _shown_by_host(row, shown)]
    added = [spot for spot in (_row_spot(row, quotes) for row in rows) if spot is not None]
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


def _fold_data(
    fold: Mapping[str, Any], extraction: dict | None, enrichment: dict | None, resolve: Resolve
) -> Any:
    return resolve(str(fold.get("dataSource") or ""), extraction, enrichment)


def _window(span: Any) -> tuple[int, int] | None:
    """A ``where`` span in seconds ("17:00-18:40"; a lone time is both ends)."""
    start, _, end = str(span).replace("–", "-").partition("-")
    first = parse_timestamp_to_seconds(start)
    last = parse_timestamp_to_seconds(end) if end else first
    return None if first is None or last is None else (first, last)


def _brief_windows(fold: Mapping[str, Any]) -> list[tuple[int, int]]:
    """The fold brief's readable ``where`` windows; [] without one."""
    brief = fold.get("brief")
    where = brief.get("where") if isinstance(brief, Mapping) else None
    spans = where if isinstance(where, list) else []
    return [window for window in map(_window, spans) if window is not None]


def _in_windows(row: Any, windows: list[tuple[int, int]]) -> bool:
    """Whether ``row`` sits inside one of ``windows`` (with the slack); an unstamped row,
    or a fold with no window, keeps it."""
    seconds = anchor_seconds(row)
    if seconds is None or not windows:
        return True
    return any(a - _WINDOW_SLACK_S <= seconds <= b + _WINDOW_SLACK_S for a, b in windows)


def _fold_props(fold: Mapping[str, Any], data: Any) -> dict | None:
    """The fold's grid over its field, kept to its brief's windows; None when no row is left."""
    props = assemble_info_grid(dict(fold), data, {}, None)
    if not props:
        return None
    windows = _brief_windows(fold)
    props["items"] = [row for row in props.get("items") or [] if _in_windows(row, windows)]
    return props if props["items"] else None


def _fold_label(fold: Mapping[str, Any]) -> str:
    """The fold's section label without its leading emoji (as ``_post_process_tabs`` does
    for a tab's)."""
    label = str(fold.get("label") or fold.get("id") or "")
    emoji = str(fold.get("emoji") or "")
    return label[len(emoji) :].lstrip() if emoji and label.startswith(emoji) else label


def apply_folds(
    host: dict,
    folds: Sequence[Mapping[str, Any]],
    extraction: dict | None,
    enrichment: dict | None,
    resolve: Resolve,
    cap_rows: Callable[[dict], None] | None = None,
) -> None:
    """Assemble each folded grid over its own field — capped in place by ``cap_rows``
    as a grid tab would be — and join it to the shipped host."""
    for fold in folds:
        data = _fold_data(fold, extraction, enrichment, resolve)
        props = _fold_props(fold, data)
        if props is None:
            continue
        if cap_rows is not None:
            cap_rows(props)
        if host.get("component") == _SPOTS:
            _into_spots(host, props, _fold_label(fold), checked_quotes(data))
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
        props = _fold_props(fold, _fold_data(fold, extraction, enrichment, resolve))
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
