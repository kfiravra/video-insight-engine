"""No Moments tab when the steps are the timeline (output-quality D6, 0b.2).

Moved out of ``core`` (task 1.8) with the one rule it lacked: D6 decides on the
assembled tabs before the dedupe, so a step tab the dedupe then drops as a twin
of another tab would leave neither the steps nor the Moments — the Moments tabs
D6 removed come back (``restore_moments_without_steps``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping

from .moment_fallback import steps_are_the_timeline

logger = logging.getLogger(__name__)

STEPS_ARE_THE_TIMELINE = "steps_are_the_timeline"
_MOMENTS = "moment_track"


def drop_moments_when_steps_are_the_timeline(
    tabs: list[dict],
    dropped_sink: list[dict],
    primary_tag: str,
    required: Collection[str],
    sources: Mapping[str, str] | None = None,
    removed: list[tuple[int, dict]] | None = None,
) -> bool:
    """Remove every moment_track tab when the steps are the timeline (D6); True when so.

    ``primary_tag`` and its ``required`` components decide (``steps_are_the_timeline``:
    a moment_track the domain requires is never dropped). ``sources`` (tab id →
    planned dataSource) labels each drop record; ``removed`` collects
    ``(index, tab)`` of each tab taken out, for a restore.
    """
    if not steps_are_the_timeline(tabs, primary_tag, required):
        return False
    for idx in reversed(range(len(tabs))):
        if tabs[idx].get("component") != _MOMENTS:
            continue
        popped = tabs.pop(idx)
        if removed is not None:
            removed.insert(0, (idx, popped))
        logger.info(
            "TAB DROPPED: id=%r, component='moment_track' — the steps are the timeline (D6)",
            popped.get("id"),
        )
        dropped_sink.append(
            {
                "id": popped.get("id", ""),
                "component": _MOMENTS,
                "dataSource": (sources or {}).get(str(popped.get("id", "")), ""),
                "reason": STEPS_ARE_THE_TIMELINE,
            }
        )
    return True


def restore_moments_without_steps(
    tabs: list[dict],
    dropped_sink: list[dict],
    removed: list[tuple[int, dict]],
    primary_tag: str,
    required: Collection[str],
) -> bool:
    """Put the D6-removed Moments tabs back when no step tab survived; True when so."""
    if not removed or steps_are_the_timeline(tabs, primary_tag, required):
        return False
    ids = {str(tab.get("id", "")) for _, tab in removed}
    dropped_sink[:] = [
        entry
        for entry in dropped_sink
        if not (entry.get("reason") == STEPS_ARE_THE_TIMELINE and entry.get("id") in ids)
    ]
    for index, tab in removed:
        tabs.insert(min(index, len(tabs)), tab)
    logger.info("D6 undone: no step tab survived the dedupe — %s restored", sorted(ids))
    return True


def dedupe_under_steps_timeline(
    tabs: list[dict],
    dropped_sink: list[dict],
    primary_tag: str,
    required: Collection[str],
    sources: Mapping[str, str],
    dedupe: Callable[[list[dict], list[dict], Collection[str], Mapping[str, str]], None],
) -> bool:
    """D6, then ``dedupe``, then D6 undone when no step tab outlived the dedupe;
    True when the steps are the timeline of what ships."""
    removed: list[tuple[int, dict]] = []
    steps_timeline = drop_moments_when_steps_are_the_timeline(
        tabs, dropped_sink, primary_tag, required, sources, removed
    )
    dedupe(tabs, dropped_sink, required, sources)
    if steps_timeline and restore_moments_without_steps(
        tabs, dropped_sink, removed, primary_tag, required
    ):
        return False
    return steps_timeline
