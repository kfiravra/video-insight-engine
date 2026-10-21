"""Reconcile at plan-done — the memory's second opinion over the planned tabs (3.2).

Runs once plan ∥ memory are both in, before ``<video_memory>`` renders and
before any extraction (pipeline-1min brief, Appendix C). One evidence view for
every reader downstream: a key is true when either reader says true and false
only when BOTH answered false (doubly false); a silent or failed reader is no
opinion (the ``MemoryResult`` contract). Each decision is recorded for
``pipeline.reconcile`` as ``{tabId, component, from, to, reason, action,
stage, evidence}``.

Step 1 (here) — a tab whose dataSource ``requiresEvidence`` key is doubly
false is re-pointed to a same-component source, else kept when it is a
moment_track (assembly's moment fallback chain fills it), else demoted down
``demoteTo`` onto an eligible source no other tab reads, else dropped.
``has_code`` paths are read off the screen and wait for frames anyway, so they
are deferred and decided at frames-done (:func:`resolve_screen_code`): code on
screen keeps them; with no vision or OCR the transcript readers decide alone.
The same goes for a required component ``has_code`` gates (tech's
``code_playground``) that no tab was planned for: its backfill is deferred
too — code on screen adds it, else it is recorded ``not_backfilled``; its field
waits for frames as well, so it joins the visual group at no latency cost.
Whenever something was deferred, frames-done re-runs the backfill, quiz and
3–6 steps over the decided tabs. Steps 1b–5 (the primary's moment source,
required backfill, quiz, dedupe, 3–6 tabs) live in ``reconcile_pass``. The plan-parse guard
(``plan_reconcile``) keeps the checks that need no evidence (unregistered
path, inactive domain).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...models.memory_types import MemoryResult
from ...models.pipeline_types import PlanResult, TabBrief
from ...shared_config.domain_config import (
    EVIDENCE_KEYS,
    build_fallback_tabs,
    data_source,
    data_sources,
    demote_to,
    valid_content_tags,
)
from .plan_reconcile import NEVER_DROPPED, replacement
from .reconcile_pass import (
    Action,
    ReconcileOutcome,
    ReconcilePass,
    dedupe_step,
    drop,
    is_quiz_tab,
    moment_source_step,
    quiz_step,
    record,
    required_step,
    size_step,
    source_allowed,
)
from .visual_annotations import collect_visual_annotations

PLAN_DONE = "plan_done"
FRAMES_DONE = "frames_done"
# The one evidence key a frame can show.
SCREEN_KEY = "has_code"

# Code-shaped on-screen text (Kfir's decision 7: "code on screen in the visual
# annotations"). Each alternative needs a shape prose does not take: a shell
# prompt before a lowercase command ("$ 500" is a price); a package/VCS command
# at a line start with a known subcommand, a flag, a URL or a package-shaped
# argument ("curl up with a book" is prose); a named or empty-argument
# ``function`` ("Loss function (MSE)" is prose); a definition, declaration or
# import; an arrow between code tokens; an empty call statement.
_CMD = r"(?:npm|npx|pnpm|yarn|pip3?|git|docker|curl|brew|cargo|kubectl)"
_CODE_TEXT = re.compile(
    r"^[ \t]*\$ [a-z]"
    r"|^[ \t]*(?:sudo[ \t]+)?(?:npm|pnpm|yarn|pip3?|brew|cargo)[ \t]+"
    r"(?:install|add|i|run|init|uninstall|remove)\b"
    r"|^[ \t]*(?:sudo[ \t]+)?git[ \t]+(?:clone|commit|push|pull|checkout|add|init|merge)\b"
    r"|^[ \t]*(?:sudo[ \t]+)?docker[ \t]+(?:run|build|compose|pull|push|exec)\b"
    rf"|^[ \t]*(?:sudo[ \t]+)?{_CMD}(?:[ \t]+\S+)*?[ \t]+"
    r"(?:--?[A-Za-z]|https?://|@?\w[\w.]*[@/=-][\w./@=^~-]+)"
    r"|\bfunction\s+[\w$]+\s*\(|\bfunction\s*\(\s*\)"
    r"|\bdef \w+\s*\("
    r"|\b(?:const|let|var)\s+[\w$]+\s*="
    r"|^[ \t]*import\s+[\w{*]"
    r"|^[ \t]*from\s+[\w.]+\s+import\s"
    r"|[\w$)]\s*=>\s*[{\w$(]"
    r"|\(\);",
    re.MULTILINE,
)


# ─── Evidence ───


def doubly_false_view(
    plan_evidence: Mapping[str, bool], memory_evidence: Mapping[str, bool] | None
) -> dict[str, bool]:
    """True when either reader says true, False only when BOTH answered false.

    A key one reader left unanswered (or memory ``None``) is absent — no
    opinion, so nothing is demoted on the plan's word alone (Appendix C).
    """
    memory = memory_evidence or {}
    view: dict[str, bool] = {}
    for key in EVIDENCE_KEYS:
        answers = (plan_evidence.get(key), memory.get(key))
        if True in answers:
            view[key] = True
        elif answers == (False, False):
            view[key] = False
    return view


def screen_shows_code(
    frame_descriptions: Sequence[Mapping[str, Any]], all_frames: Sequence[Mapping[str, Any]]
) -> bool:
    """True when a frame is code: vision's ``scene_type: code``, or code-shaped text
    in the visual annotations (vision's ``text_visible``, else the frame's OCR)."""
    if any(desc.get("scene_type") == "code" for desc in frame_descriptions):
        return True
    entries = collect_visual_annotations(frame_descriptions, all_frames)
    return any(_CODE_TEXT.search(entry.text) for entry in entries)


# ─── Step 1: evidence ───


def _gate_key(tab: Mapping[str, Any]) -> str | None:
    """The evidence key the tab's dataSource requires; the quiz has its own step."""
    path = tab.get("dataSource")
    spec = data_source(path) if isinstance(path, str) and path else None
    if spec is None or is_quiz_tab(tab):
        return None
    return spec["requiresEvidence"]


def _demotion_sources(path: str) -> list[str]:
    """The source's declared siblings, then the other paths of its own domain."""
    spec = data_source(path)
    if spec is None:
        return []
    own = [p for p, s in data_sources().items() if s["domain"] == spec["domain"] and p != path]
    return list(dict.fromkeys([*spec["siblings"], *own]))


def _can_take(run: ReconcilePass, candidate: str, rung: str, read: set[str]) -> bool:
    spec = data_source(candidate)
    return (
        spec is not None
        and candidate not in read
        and spec["domain"] in run.active
        and rung in spec["components"]
        and source_allowed(run, candidate)
    )


def _demotion(
    run: ReconcilePass, path: str, component: str, read: set[str]
) -> tuple[str, str] | None:
    """``(rung, source)``: the first ``demoteTo`` rung an eligible source renders."""
    candidates = _demotion_sources(path)
    for rung in demote_to().get(component, []):
        source = next((c for c in candidates if _can_take(run, c, rung, read)), None)
        if source is not None:
            return rung, source
    return None


def _default_for(source: str, rung: str) -> Mapping[str, Any] | None:
    """The defaultTab that shows ``source`` (as ``rung`` when one does), if any."""
    spec = data_source(source)
    if spec is None or spec["domain"] not in valid_content_tags():
        return None
    shows = [t for t in build_fallback_tabs(spec["domain"]) if t.get("dataSource") == source]
    return next((t for t in shows if t.get("component") == rung), shows[0] if shows else None)


def _demoted(tab: dict, rung: str, source: str) -> dict:
    """``tab`` rendered as ``rung`` over ``source``: its brief keeps only the time
    ranges and its goal goes (both described the old field); the label follows
    the defaultTab that shows ``source`` when the registry has one."""
    brief = tab.get("brief")
    where = brief.get("where") if isinstance(brief, Mapping) else None
    demoted = {key: value for key, value in tab.items() if key != "goal"}
    demoted.update(
        component=rung,
        dataSource=source,
        brief=TabBrief.from_raw({"where": where}).model_dump(),
    )
    default = _default_for(source, rung)
    if default is not None:
        demoted.update(label=default.get("label", tab["label"]), emoji=default.get("emoji", ""))
    return demoted


def _fall_back(run: ReconcilePass, tab: dict, key: str, others: Sequence[dict]) -> dict | None:
    """Re-point → keep a moment_track → demote → drop, for one doubly-false tab."""
    path, component = tab.get("dataSource") or "", tab["component"]
    taken = {(t["dataSource"], t["component"]) for t in [*others, tab] if t.get("dataSource")}
    target = replacement(path, component, run.active, run.evidence, taken)
    if target is not None:
        record(run, tab, Action("repointed", "evidence_false", to=target, key=key))
        return {**tab, "dataSource": target}
    if component in NEVER_DROPPED:
        record(run, tab, Action("kept_for_moment_fallback", "evidence_false", key=key))
        return tab
    demoted = _demotion(run, path, component, {t.get("dataSource") or "" for t in others})
    if demoted is None:
        drop(run, tab, "evidence_false", key)
        return None
    rung, source = demoted
    record(run, tab, Action("demoted", "evidence_false", to=source, key=key, to_component=rung))
    return _demoted(tab, rung, source)


def _others(slots: Sequence[dict | None], index: int) -> list[dict]:
    return [tab for i, tab in enumerate(slots) if tab is not None and i != index]


def _evidence_step(run: ReconcilePass) -> None:
    """Doubly-false dataSources: ``has_code`` deferred to frames-done, the rest fall back."""
    slots: list[dict | None] = list(run.tabs)
    for index, tab in enumerate(slots):
        key = _gate_key(tab) if tab is not None else None
        if tab is None or key is None or run.evidence.get(key) is not False:
            continue
        if key == SCREEN_KEY:
            run.deferred.append(tab["id"])
            record(run, tab, Action("deferred_to_frames", "evidence_false", key=key))
            continue
        slots[index] = _fall_back(run, tab, key, _others(slots, index))
    run.tabs = [tab for tab in slots if tab is not None]


_PLAN_DONE_STEPS = (
    _evidence_step,
    moment_source_step,
    required_step,
    quiz_step,
    dedupe_step,
    size_step,
)
_SCREEN_KEYS = frozenset({SCREEN_KEY})


# ─── Entry points ───


def _active(plan: PlanResult) -> list[str]:
    return list(dict.fromkeys([*plan.content_tags, *plan.modifiers]))


def reconcile_at_plan_done(
    plan: PlanResult, memory: MemoryResult | None, content_format: str | None
) -> ReconcileOutcome:
    """Steps 1–5 over ``plan``'s tabs with the doubly-false evidence view."""
    memory_evidence = memory.evidence if memory is not None else None
    run = ReconcilePass(
        tabs=[dict(tab) for tab in plan.tabs],
        active=_active(plan),
        evidence=doubly_false_view(plan.evidence, memory_evidence),
        stage=PLAN_DONE,
        readers={"plan": plan.evidence, "memory": memory_evidence or {}},
        primary=plan.primary_tag,
        content_format=content_format,
        defer_keys=_SCREEN_KEYS,
    )
    for step in _PLAN_DONE_STEPS:
        step(run)
    return run.outcome()


_FRAMES_DONE_STEPS = (required_step, quiz_step, size_step)


def resolve_screen_code(
    plan: PlanResult,
    frame_descriptions: Sequence[Mapping[str, Any]],
    all_frames: Sequence[Mapping[str, Any]],
    content_format: str | None,
) -> ReconcileOutcome:
    """Frames-done: keep ``plan``'s deferred code tabs when the screen shows code,
    else fall them back as step 1 would — no frames or vision = the transcript
    decides. Then steps 2, 3 and 5 (backfill, quiz last, 3–6) re-run over the
    decided tabs whenever something was deferred: code on screen backfills a
    deferred code requirement, none records it ``not_backfilled``."""
    deferred = set(plan.deferred_tabs)
    shows = plan.awaits_screen and screen_shows_code(frame_descriptions, all_frames)
    run = ReconcilePass(
        tabs=list(plan.tabs),
        active=_active(plan),
        evidence={**plan.evidence, SCREEN_KEY: True} if shows else dict(plan.evidence),
        stage=FRAMES_DONE,
        readers={"screen": {SCREEN_KEY: shows}},
        primary=plan.primary_tag,
        content_format=content_format,
        recheck_keys=_SCREEN_KEYS,
    )
    slots: list[dict | None] = list(run.tabs)
    for index, tab in enumerate(slots):
        if tab is None or tab["id"] not in deferred:
            continue
        if shows:
            record(run, tab, Action("kept_screen_code", "screen_code", key=SCREEN_KEY))
            continue
        slots[index] = _fall_back(run, tab, SCREEN_KEY, _others(slots, index))
    run.tabs = [tab for tab in slots if tab is not None]
    if plan.awaits_screen:
        for step in _FRAMES_DONE_STEPS:
            step(run)
    return run.outcome()


def apply_outcome(plan: PlanResult, outcome: ReconcileOutcome) -> PlanResult:
    """``plan`` with the outcome's tabs, evidence and deferrals; records and drops appended."""
    return plan.model_copy(
        update={
            "tabs": outcome.tabs,
            "evidence": outcome.evidence,
            "deferred_tabs": list(outcome.deferred),
            "deferred_requirements": list(outcome.deferred_requirements),
            "reconcile": [*plan.reconcile, *outcome.records],
            "dropped_tabs": [*plan.dropped_tabs, *outcome.dropped],
        }
    )


def reconcile_plan(
    plan: PlanResult, memory: MemoryResult | None, content_format: str | None
) -> PlanResult:
    """The plan every later reader sees: reconciled at plan-done, readers' answers kept."""
    outcome = reconcile_at_plan_done(plan, memory, content_format)
    return apply_outcome(plan, outcome).model_copy(
        update={
            "plan_evidence": dict(plan.evidence),
            "memory_evidence": dict(memory.evidence) if memory is not None else None,
        }
    )


def resolve_deferred(
    plan: PlanResult,
    frame_descriptions: Sequence[Mapping[str, Any]],
    all_frames: Sequence[Mapping[str, Any]],
    content_format: str | None,
) -> PlanResult:
    """``plan`` with its deferred code tabs and requirements decided on the screen
    (unchanged when nothing was deferred)."""
    if not plan.awaits_screen:
        return plan
    outcome = resolve_screen_code(plan, frame_descriptions, all_frames, content_format)
    return apply_outcome(plan, outcome)
