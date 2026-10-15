"""One reconcile pass — its working state, its records, and the layout steps 2–5 (3.2).

``reconcile.py`` owns the evidence view, the screen-code check, step 1
(evidence) and the entry points; this module holds what every step shares
(:class:`ReconcilePass`, the ``pipeline.reconcile`` row writer) and the steps
that shape the tab list once the evidence is settled:

2. required — the primary domain's required components are backfilled from
   its defaultTabs unless their ``requirementEvidence`` key is doubly false
   (``not_backfilled``, naming the defaultTab it would have added); a key the
   screen can still answer (``has_code``) defers the decision to frames-done
   (``deferred_to_frames``) instead;
3. quiz — dropped outside ``quizDomains`` or when ``is_learnable`` is doubly
   false; otherwise moved last;
4. dedupe — one tab per ``(dataSource, component)``, a component and what
   assembly promotes it to counting as one (flash_deck → concept_canvas), the
   first wins; assembly dedupes again on the assembled items (``assembly/dedupe.py``);
5. size — 3–6 tabs: beyond 6 the last other planned tab is trimmed (never a
   required tab, a moment_track or the quiz), a short plan is filled from the
   primary's defaultTabs.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ...models.pipeline_types import TabBrief
from ...shared_config.domain_config import (
    build_fallback_tabs,
    data_source,
    default_tab_id,
    effective_requirements,
    quiz_enrichment,
    quiz_policy,
    requirement_evidence,
)
from .assembly.promotion import COMPONENT_PROMOTIONS
from .enrichment import QUIZ_COMPONENT, QUIZ_DATA_SOURCE
from .plan_reconcile import NEVER_DROPPED

logger = logging.getLogger(__name__)

MIN_TABS = 3
MAX_TABS = 6
# Actions that add a tab the planner never designed (assembly's tab accounting).
ADDED_ACTIONS = frozenset({"backfilled", "filled"})
# Assembly adds the overview hero itself; a filler tab must carry content.
_NOT_A_FILLER = frozenset({"overview"})


@dataclass(frozen=True)
class ReconcileOutcome:
    """One reconcile pass: the tabs to keep and everything decided on the way."""

    tabs: list[dict]
    dropped: list[dict]  # ``droppedTabs`` shape: id, component, dataSource, reason
    records: list[dict]  # ``pipeline.reconcile`` rows
    evidence: dict[str, bool]
    deferred: tuple[str, ...]  # tab ids awaiting the screen-code check
    # Required components whose backfill awaits it (no tab planned for them).
    deferred_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class Action:
    """One decision about one tab, as ``pipeline.reconcile`` records it."""

    name: str
    reason: str
    to: str | None = None
    key: str | None = None  # the evidence key behind the decision
    to_component: str | None = None  # a demotion's rung


@dataclass
class ReconcilePass:
    """The working state one reconcile pass threads through its steps."""

    tabs: list[dict]
    active: list[str]
    evidence: dict[str, bool]
    stage: str
    # Reader name → its answers, quoted per evidence key in each record.
    readers: dict[str, Mapping[str, bool]]
    primary: str = ""
    content_format: str | None = None
    # A required component its doubly-false key rules out: plan-done defers it
    # when the screen can still answer the key (``defer_keys``); frames-done
    # records only the keys the screen answered (``recheck_keys``) — every other
    # rule-out was recorded at plan-done and nothing about it changed.
    defer_keys: frozenset[str] = frozenset()
    recheck_keys: frozenset[str] | None = None
    dropped: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    deferred_requirements: list[str] = field(default_factory=list)

    def outcome(self) -> ReconcileOutcome:
        ids = {tab["id"] for tab in self.tabs}
        return ReconcileOutcome(
            tabs=self.tabs,
            dropped=self.dropped,
            records=self.records,
            evidence=self.evidence,
            deferred=tuple(tab_id for tab_id in self.deferred if tab_id in ids),
            deferred_requirements=tuple(self.deferred_requirements),
        )


# ─── Records ───


def _note(run: ReconcilePass, key: str | None) -> dict[str, Any] | None:
    """``{"key": K, <reader>: its answer or None, …}`` for an evidence decision."""
    if key is None:
        return None
    return {"key": key, **{name: answers.get(key) for name, answers in run.readers.items()}}


def record(
    run: ReconcilePass, tab: Mapping[str, Any], action: Action, *, added: bool = False
) -> None:
    """Append one ``pipeline.reconcile`` row (``from`` is None for an added tab)."""
    row = {
        "tabId": tab.get("id", ""),
        "component": tab.get("component", ""),
        "from": None if added else tab.get("dataSource") or "",
        "to": action.to,
        "reason": action.reason,
        "action": action.name,
        "stage": run.stage,
        "evidence": _note(run, action.key),
    }
    if action.to_component is not None:
        row["toComponent"] = action.to_component
    run.records.append(row)
    log = logger.warning if action.name == "dropped" else logger.info
    log(
        "Reconcile %s: tab %r %s (%s) %r -> %r",
        run.stage,
        row["tabId"],
        action.name,
        action.reason,
        row["from"],
        action.to,
    )


def drop(run: ReconcilePass, tab: Mapping[str, Any], reason: str, key: str | None = None) -> None:
    """Record the drop and list it in the ``droppedTabs`` shape assembly persists."""
    record(run, tab, Action("dropped", reason, key=key))
    run.dropped.append(
        {
            "id": tab.get("id", ""),
            "component": tab.get("component", ""),
            "dataSource": tab.get("dataSource") or "",
            "reason": f"reconcile_{reason}",
        }
    )


# ─── Tab helpers ───


def is_quiz_tab(tab: Mapping[str, Any]) -> bool:
    return tab.get("component") == QUIZ_COMPONENT or tab.get("dataSource") == QUIZ_DATA_SOURCE


def source_allowed(run: ReconcilePass, path: str) -> bool:
    """The path's own evidence is not doubly false (unregistered paths have none)."""
    spec = data_source(path)
    key = spec["requiresEvidence"] if spec else None
    return not key or run.evidence.get(key) is not False


def _equivalents(component: str) -> frozenset[str]:
    """A required component and what assembly may promote it to."""
    promoted = COMPONENT_PROMOTIONS.get(component)
    return frozenset({component, promoted} if promoted else {component})


def _usable_default(run: ReconcilePass, cand: Mapping[str, Any], fresh_source: bool) -> bool:
    """A defaultTab not planned yet whose source has evidence (and, for a filler,
    that no other tab reads)."""
    path, component = cand.get("dataSource", ""), cand.get("component", "")
    if any(t["id"] == cand.get("id") for t in run.tabs):
        return False
    if any((t.get("dataSource"), t["component"]) == (path, component) for t in run.tabs):
        return False
    if fresh_source and any(t.get("dataSource") == path for t in run.tabs):
        return False
    return source_allowed(run, path)


def _default_tab(
    run: ReconcilePass, allowed: frozenset[str] | None, refused: frozenset[str], fresh: bool
) -> dict | None:
    """A copy of the primary's first usable defaultTab (a copy: the config is cached)."""
    for cand in build_fallback_tabs(run.primary):
        component = cand.get("component", "")
        if allowed is not None and component not in allowed:
            continue
        if component not in refused and _usable_default(run, cand, fresh):
            return {**cand, "brief": TabBrief().model_dump()}
    return None


# ─── Step 2: required components ───


def _rule_out(run: ReconcilePass, component: str, key: str) -> None:
    """A missing required component its doubly-false ``key`` rules out: deferred to
    the screen check, else recorded with the defaultTab it would have added."""
    if key in run.defer_keys:
        run.deferred_requirements.append(component)
        name = "deferred_to_frames"
    elif run.recheck_keys is None or key in run.recheck_keys:
        name = "not_backfilled"
    else:
        return
    live = {tab["id"] for tab in run.tabs}
    would_add = {"id": default_tab_id(run.primary, component, live), "component": component}
    record(run, would_add, Action(name, "evidence_false", key=key), added=True)


def required_step(run: ReconcilePass) -> None:
    """Backfill each missing required component unless its evidence is doubly false."""
    reqs = effective_requirements(run.primary, run.content_format)
    gates = requirement_evidence().get(run.primary, {})
    for component in reqs["required"]:
        accepted = _equivalents(component)
        key = gates.get(component)
        if any(t["component"] in accepted for t in run.tabs):
            continue
        if key and run.evidence.get(key) is False:
            _rule_out(run, component, key)
            continue
        tab = _default_tab(run, accepted, reqs["forbidden"], fresh=False)
        if tab is None:
            logger.info(
                "Reconcile: no defaultTab renders required %r of %r", component, run.primary
            )
            continue
        run.tabs.append(tab)
        record(
            run, tab, Action("backfilled", "required_missing", tab["dataSource"], key), added=True
        )


# ─── Step 3: quiz ───


def _quiz_drop_reason(run: ReconcilePass) -> tuple[str, str | None] | None:
    """``(reason, evidence key)`` when the quiz must go; None when it stays."""
    if run.primary not in quiz_enrichment()["quizDomains"]:
        return "quiz_domain", None
    key = quiz_policy()["requiresEvidence"]
    return ("evidence_false", key) if run.evidence.get(key) is False else None


def quiz_step(run: ReconcilePass) -> None:
    """Drop the quiz where it cannot be, else move it last (``quizPolicy``)."""
    quizzes = [tab for tab in run.tabs if is_quiz_tab(tab)]
    if not quizzes:
        return
    rest = [tab for tab in run.tabs if not is_quiz_tab(tab)]
    reason = _quiz_drop_reason(run)
    if reason is not None:
        for tab in quizzes:
            drop(run, tab, *reason)
        run.tabs = rest
        return
    if quiz_policy()["position"] != "last":
        return
    ordered = [*rest, *quizzes]
    for tab in quizzes:
        if run.tabs.index(tab) != ordered.index(tab):
            record(run, tab, Action("moved_last", "quiz_policy"))
    run.tabs = ordered


# ─── Steps 4–5: dedupe, size ───


def dedupe_step(run: ReconcilePass) -> None:
    """One tab per ``(dataSource, component)`` — the first wins. A component and
    its promotion target are the same component here: a flash_deck and a
    concept_canvas on one ``concepts`` list ship the same items (S03, L26)."""
    seen: set[tuple[str, str]] = set()
    kept: list[dict] = []
    for tab in run.tabs:
        component = tab["component"]
        pair = (tab.get("dataSource") or "", COMPONENT_PROMOTIONS.get(component, component))
        if pair[0] and pair in seen:
            drop(run, tab, "duplicate")
            continue
        seen.add(pair)
        kept.append(tab)
    run.tabs = kept


def _trimmable(run: ReconcilePass) -> list[int]:
    """Indices of the tabs a trim may remove: not required, not a moment_track,
    not the quiz (it sits last by policy, not because it matters least)."""
    required = effective_requirements(run.primary, run.content_format)["required"]
    protected = NEVER_DROPPED.union(*(_equivalents(component) for component in required))
    return [
        i
        for i, tab in enumerate(run.tabs)
        if tab["component"] not in protected and not is_quiz_tab(tab)
    ]


def _trim(run: ReconcilePass) -> None:
    while len(run.tabs) > MAX_TABS:
        candidates = _trimmable(run)
        if not candidates:
            return
        drop(run, run.tabs.pop(candidates[-1]), "over_max")


def _fill(run: ReconcilePass) -> None:
    refused = effective_requirements(run.primary, run.content_format)["forbidden"] | _NOT_A_FILLER
    while len(run.tabs) < MIN_TABS:
        tab = _default_tab(run, None, refused, fresh=True)
        if tab is None:
            return
        # A filler goes before a trailing quiz, which stays last.
        at = len(run.tabs) - 1 if run.tabs and is_quiz_tab(run.tabs[-1]) else len(run.tabs)
        run.tabs.insert(at, tab)
        record(run, tab, Action("filled", "under_min", to=tab["dataSource"]), added=True)


def size_step(run: ReconcilePass) -> None:
    """3–6 tabs (the overview hero is added at assembly and not counted)."""
    _trim(run)
    _fill(run)
