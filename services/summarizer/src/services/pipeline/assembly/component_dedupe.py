"""One tab per component (tab-dedupe hotfix, 2026-11-05).

A page shows each component once, whatever the dataSources (S02 and S13 planned two
moment_tracks, S06 two checklists, S07 two comparisons; the planner was told to).

- Re-route first (:func:`reroute_duplicates`): a repeated tab with a second view of
  its own (``REROUTES``) changes component instead of dropping. An interview's Q&A on
  ``podcast.qa`` beside another planned moment_track ships as an info_grid of its
  questions, and the other tab stays the one timeline (Kfir 2026-11-05).
- Then keep the first (:func:`dedupe_components`): the first tab of a family keeps
  it, and every later one is dropped, logged and listed in ``droppedTabs``.

Plan parse (``plan._normalize_plan_data``) only re-routes: whether the first tab of a
family ships is known in assembly alone (an interview's Q&A drops when fewer than 3
questions check out), so a later tab is never dropped before then — the fallback plan's
config defaults excepted, which the planner never designed. In assembly the first tab
in plan order wins (Kfir 2026-11-05): the re-route runs on the plan; the loop skips a
tab whose family an earlier tab planned in it already shipped (the first tab that
SHIPS keeps it, so a planned tab that dies hands the slot on); a tab whose family an
earlier tab only reached by promotion or demotion walks its own demote ladder instead
(S09: the tier list demoted to spot cards, the later spot tab ships as a grid);
promotion and demotion refuse only a family an EARLIER tab ships as; and
:func:`dedupe_components` runs once more on the assembled tabs as the backstop (an
unfolded grid, a backfill). Plan-done reconcile applies the same order to its
evidence demotions (``reconcile._settle_demoted_components``).

A family is a component plus its promotion target when both serve one purpose:
comparison_radar is a comparison, step_flow_canvas a step player. info_grid and
spot_explorer serve different purposes, and so do flash_deck and concept_canvas,
so each pair may share a page. The overview is assembly's own hero and never takes
part. This module imports nothing from assembly, so ``plan.py`` can use it too.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

DUPLICATE_COMPONENT = "duplicate_component"
_OVERVIEW = "overview"
# A component and the sibling that renders the same purpose.
FAMILY: Mapping[str, str] = {
    "comparison_radar": "comparison",
    "step_flow_canvas": "step_player",
}
# (component, dataSource) → the component a repeated tab ships as instead of dropping:
# a second view of the same data that serves another purpose (the registry lists it
# among the source's components).
REROUTES: Mapping[tuple[str, str], str] = {
    ("moment_track", "podcast.qa"): "info_grid",
}


def family(component: str) -> str:
    """The component name a tab counts as: its family head."""
    return FAMILY.get(component, component)


def _component(tab: Mapping[str, Any]) -> str:
    return str(tab.get("component") or "")


def _takes_part(tab: Any) -> bool:
    return isinstance(tab, dict) and _component(tab) not in ("", _OVERVIEW)


def planned_components(tabs: Iterable[Any]) -> frozenset[str]:
    """The families the tabs hold, the overview left out."""
    return frozenset(family(_component(t)) for t in tabs if _takes_part(t))


def reroute_duplicates(tabs: list[dict]) -> list[dict]:
    """Change a repeated tab with a second view (``REROUTES``) to that view, in place.

    A tab is re-routed whenever another tab holds its family: the other tab stays the
    one timeline. A grid it lands beside is merged by the plan-done fold (R6) or settled
    by assembly's first-that-ships rule, never left to cost the page its timeline.
    Returns ``tabs``.
    """
    for index, tab in enumerate(tabs):
        if not _takes_part(tab):
            continue
        target = REROUTES.get((_component(tab), str(tab.get("dataSource") or "")))
        if target is None:
            continue
        others = [t for t in tabs if t is not tab]
        if family(_component(tab)) not in planned_components(others):
            continue
        logger.info(
            "TAB REROUTED: id=%r, %s -> %s — another tab is the %s",
            tab.get("id"),
            _component(tab),
            target,
            _component(tab),
        )
        tabs[index] = {**tab, "component": target}
    return tabs


def duplicate_record(tab: Mapping[str, Any], kept: str, data_source: str) -> dict[str, str]:
    """The ``droppedTabs`` entry of a tab dropped for repeating ``kept``'s component."""
    return {
        "id": str(tab.get("id", "")),
        "component": _component(tab),
        "dataSource": data_source,
        "reason": DUPLICATE_COMPONENT,
        "detail": f"kept {kept}",
    }


def log_duplicate(stage: str, tab: Mapping[str, Any], kept: str) -> None:
    logger.warning(
        "TAB DROPPED (%s): id=%r, component=%r — a second %r tab; %r keeps it",
        stage,
        tab.get("id"),
        _component(tab),
        family(_component(tab)),
        kept,
    )


def dedupe_components(
    tabs: list[dict],
    dropped_sink: list[dict],
    *,
    stage: str,
    sources: Mapping[str, str] | None = None,
) -> list[dict]:
    """Keep the first tab of each component family, in list order; drop the rest in place.

    Every drop is logged and listed in ``dropped_sink`` (``droppedTabs``) with the
    planned dataSource (``sources`` overrides the tab's own, for assembled tabs) and
    the id of the tab that keeps the component. Returns ``tabs``.
    """
    keeper: dict[str, str] = {}
    for tab in list(tabs):
        if not _takes_part(tab):
            continue
        head, tab_id = family(_component(tab)), str(tab.get("id", ""))
        if head not in keeper:
            keeper[head] = tab_id
            continue
        log_duplicate(stage, tab, keeper[head])
        source = (sources or {}).get(tab_id) or str(tab.get("dataSource") or "")
        dropped_sink.append(duplicate_record(tab, keeper[head], source))
        # By identity, never an equal tab before it.
        del tabs[next(i for i, other in enumerate(tabs) if other is tab)]
    return tabs
