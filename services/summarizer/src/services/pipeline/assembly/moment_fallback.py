"""moment_track fallback chain (pipeline-1min phase-1 close, Kfir's decision on g1).

A moment_track tab is never dropped for lack of data. When its planned
dataSource cannot feed it (no data, an inactive domain, or every moment past
the video's end), the moments come from the first source that has entries:

1. the memory outline (``video_meta["outline"]``: start/end/title sections; a
   section's ``establishes`` line rides as the moment's description — D5 / R7),
2. the YouTube chapters (``video_meta["chapters"]``),
3. the description's timestamps (``description_analysis["timestamps"]``).

Every source is converted to the chapter shape ``_chapters_to_moments`` turns
into MomentTrack spans, so the three read the same in the UI.

A Moments tab always exists (Kfir 2026-10-12, pipeline-1min 2.1): a response
whose plan designed none gets the always-Moments tab from the same chain
(``always_moments_tab``), and the plan-done provisional copy
(``provisional_moments_tab``) carries the id of whichever tab will replace it.
No source with an entry → no Moments tab: one is never invented. When the
steps are the timeline (D6: a food, fitness or project response with a step
player, flow canvas or workout room, unless its domain requires moment_track),
assembly ships no Moments tab, so none is previewed either.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any

from src.shared_config.assembly_policy import assembly_policy, injected_tab

from .assemblers_primary import assemble_moment_track
from .normalizers import _chapters_to_moments, _coerce_int
from .registry import infer_component

logger = logging.getLogger(__name__)

_MOMENT_COMPONENT = "moment_track"
_QUIZ_COMPONENT = "quiz_arena"
_OVERVIEW = "overview"

# The Moments tab of a response whose plan designed none.
_ALWAYS_MOMENTS_ID = "key_moments"
# Label, emoji and goal from the registry (``injectedTabs.moments``): the goal names
# what the viewer gets, never an assembly template (task 1.10's rule).
_ALWAYS_MOMENTS_LABEL = injected_tab("moments")["label"]
_ALWAYS_MOMENTS_EMOJI = injected_tab("moments")["emoji"]
_ALWAYS_MOMENTS_GOAL = injected_tab("moments")["goal"]


def _outline_entries(video_meta: Mapping[str, Any]) -> list[dict]:
    # What the section establishes is the moment's description (1.9); an
    # outline from before the line (or one that only repeats the title) ships
    # the title alone, as the chapter shape always did.
    return [
        {
            "start_time": s.get("start"),
            "title": s.get("title", ""),
            "description": s.get("establishes"),
        }
        for s in video_meta.get("outline") or []
        if isinstance(s, dict)
    ]


def _chapter_entries(video_meta: Mapping[str, Any]) -> list[dict]:
    return [ch for ch in video_meta.get("chapters") or [] if isinstance(ch, dict)]


def _description_entries(description_analysis: Mapping[str, Any] | None) -> list[dict]:
    timestamps = (description_analysis or {}).get("timestamps") or []
    return [
        {"start_time": t.get("seconds"), "title": t.get("label", "")}
        for t in timestamps
        if isinstance(t, dict)
    ]


def _usable(entries: list[dict], duration: int | None) -> list[dict]:
    """Entries with a start inside the video, in time order."""
    kept = [
        e
        for e in entries
        if (start := _coerce_int(e.get("start_time"))) is not None
        and start >= 0
        and (duration is None or start < duration)
        and str(e.get("title") or "").strip()
    ]
    return sorted(kept, key=lambda e: _coerce_int(e["start_time"]) or 0)


_SOURCES: tuple[
    tuple[str, Callable[[Mapping[str, Any], Mapping[str, Any] | None], list[dict]]], ...
] = (
    ("memory_outline", lambda meta, _desc: _outline_entries(meta)),
    ("youtube_chapters", lambda meta, _desc: _chapter_entries(meta)),
    ("description_timestamps", lambda _meta, desc: _description_entries(desc)),
)


def fallback_moments(
    video_meta: Mapping[str, Any] | None, description_analysis: Mapping[str, Any] | None
) -> list[dict] | None:
    """Moment data from the first fallback source with entries; None when none has any."""
    meta = video_meta or {}
    duration = _coerce_int(meta.get("duration"))
    for name, entries_of in _SOURCES:
        entries = _usable(entries_of(meta, description_analysis), duration)
        if entries:
            logger.info("moment_track fallback: %d moments from %s", len(entries), name)
            return _chapters_to_moments(entries, duration)
    return None


def fallback_moment_props(
    video_meta: Mapping[str, Any] | None, description_analysis: Mapping[str, Any] | None
) -> dict | None:
    """Assembled moment_track props built from the fallback chain."""
    moments = fallback_moments(video_meta, description_analysis)
    return assemble_moment_track({}, moments, {}, None) if moments else None


def cannot_feed_moments(data: Any) -> bool:
    """True when resolved moment data is missing or empty."""
    return data is None or (isinstance(data, list) and len(data) == 0)


# ─── The Moments tab that always exists ───


def tab_component(tab: Mapping[str, Any]) -> str:
    """The component a plan tab assembles as — assembly's own inference for an unnamed one."""
    if tab.get("id") == _OVERVIEW:
        return _OVERVIEW
    return str(tab.get("component") or infer_component(str(tab.get("id", ""))))


# D6 (output-quality 0b.2): recipes, workouts and step tutorials are navigated by
# their steps (a step player, a step flow canvas or a workout room) and get no
# Moments tab — the overview is the hero. The response's PRIMARY domain decides:
# one step tab under a podcast or a lecture leaves the domain's timeline alone,
# and a domain that requires moment_track always keeps it (phase-0 review C-A1).
# domains.json ``assemblyPolicy``: the procedures and the primaries they navigate.
_TIMELINE_STEP_COMPONENTS = frozenset(assembly_policy()["sequentialComponents"])
_STEP_TIMELINE_PRIMARIES = frozenset(assembly_policy()["stepTimelinePrimaries"])


def steps_are_the_timeline(
    tabs: Iterable[Mapping[str, Any]], primary_tag: str, required: Collection[str]
) -> bool:
    """D6: a food / fitness / project response navigates by its steps.

    True when ``primary_tag`` is one of those domains, its ``required`` components
    (``effective_requirements``) do not include moment_track, and a step player,
    flow canvas or workout room ships. Plan tabs and assembled tabs alike: an
    unnamed plan tab counts by its id.
    """
    if primary_tag not in _STEP_TIMELINE_PRIMARIES or _MOMENT_COMPONENT in required:
        return False
    return any(tab_component(t) in _TIMELINE_STEP_COMPONENTS for t in tabs)


def _always_moments_id(taken: Iterable[str]) -> str:
    """``key_moments`` unless a tab holds it; plan-done and assembly reach the same id."""
    used = set(taken)
    candidate, suffix = _ALWAYS_MOMENTS_ID, 2
    while candidate in used:
        candidate, suffix = f"{_ALWAYS_MOMENTS_ID}_{suffix}", suffix + 1
    return candidate


def _always_moments_stub(taken: Iterable[str]) -> dict[str, str]:
    return {
        "id": _always_moments_id(taken),
        "label": _ALWAYS_MOMENTS_LABEL,
        "emoji": _ALWAYS_MOMENTS_EMOJI,
        "goal": _ALWAYS_MOMENTS_GOAL,
    }


def _moments_tab(stub: Mapping[str, str], props: dict) -> dict:
    """A tab in assembly's TabEntry shape."""
    return {**stub, "component": _MOMENT_COMPONENT, "props": props, "crossTabLinks": []}


def _tab_ids(tabs: Iterable[Any]) -> list[str]:
    return [str(t.get("id", "")) for t in tabs if isinstance(t, dict)]


def always_moments_tab(
    tabs: list[dict],
    plan_tabs: list[Any],
    video_meta: Mapping[str, Any] | None,
    description_analysis: Mapping[str, Any] | None,
    forbidden: Collection[str],
) -> dict | None:
    """The fallback-chain Moments tab for a response without a moment_track tab.

    None when ``tabs`` already holds one, when the domain policy forbids the
    component, or when no fallback source has an entry.
    """
    if _MOMENT_COMPONENT in forbidden or any(t.get("component") == _MOMENT_COMPONENT for t in tabs):
        return None
    props = fallback_moment_props(video_meta, description_analysis)
    if props is None:
        return None
    taken = [*_tab_ids(plan_tabs), *_tab_ids(tabs)]
    return _moments_tab(_always_moments_stub(taken), props)


def _kept_in_plan_order(tab: Mapping[str, Any]) -> bool:
    """Assembly puts its own overview first and moves the quiz last; the rest keep plan order."""
    return tab_component(tab) not in (_OVERVIEW, _QUIZ_COMPONENT)


def provisional_moments_tab(
    plan_tabs: list[Any],
    video_meta: Mapping[str, Any] | None,
    description_analysis: Mapping[str, Any] | None,
    primary_tag: str,
    requirements: Mapping[str, Any],
) -> tuple[dict, int] | None:
    """The plan-done Moments tab and its estimated position, or None.

    ``requirements`` is the primary's ``effective_requirements`` — assembly
    decides with the same. None when it forbids moment_track, when the steps
    are the timeline (D6 — assembly would only withdraw the preview at
    ``complete``) or when no fallback source has an entry.

    Its id, label, emoji and goal are those of the tab that will replace it:
    the plan's first moment_track tab, else the always-Moments tab, which
    assembly adds after the planned tabs. The estimate counts the overview
    assembly puts first and the planned tabs before it, quiz excluded.
    """
    tabs = [t for t in plan_tabs if isinstance(t, dict)]
    if _MOMENT_COMPONENT in requirements["forbidden"] or steps_are_the_timeline(
        tabs, primary_tag, requirements["required"]
    ):
        return None
    props = fallback_moment_props(video_meta, description_analysis)
    if props is None:
        return None
    index = next((i for i, t in enumerate(tabs) if tab_component(t) == _MOMENT_COMPONENT), None)
    if index is None:
        index, stub = len(tabs), _always_moments_stub(_tab_ids(tabs))
    else:
        planned = tabs[index]
        planned_id = str(planned.get("id", ""))
        stub = {
            "id": planned_id,
            "label": str(planned.get("label", planned_id)),
            "emoji": str(planned.get("emoji", "")),
            "goal": str(planned.get("goal", "")),
        }
    position = 1 + sum(1 for t in tabs[:index] if _kept_in_plan_order(t))
    return _moments_tab(stub, props), position
