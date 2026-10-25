"""Plan-parse guard — the evidence-free half of reconcile (hotfix 2.1, narrowed by 3.2).

Right after the plan returns, a tab whose dataSource's domain is not one of
the plan's contentTags/modifiers (``inactive_domain``: ``news.claims`` on a
learning/tech video — the extraction is domain-keyed, so that path is never
filled) is re-pointed or dropped. Evidence is NOT judged here: the plan's
answers alone cannot make a key doubly false, so the evidence trigger runs at
plan-done, once the memory's second opinion is in (``reconcile.py``).

A dataSource rendered by the same component in an active domain replaces it
(the registry's declared siblings first, then the active domains in plan
order); otherwise the tab is dropped — except a moment_track tab, which stays
and is filled by assembly's moment fallback chain. Every decision is recorded
for ``pipeline.reconcile`` (``action``: repointed / dropped /
kept_for_moment_fallback, ``stage``: plan_parse); drops also join
``droppedTabs`` so the tab accounting stays complete.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from ...shared_config.domain_config import (
    DataSourceSpec,
    data_source,
    data_sources,
    valid_content_tags,
    valid_modifiers,
)

logger = logging.getLogger(__name__)

PLAN_PARSE = "plan_parse"
INACTIVE_DOMAIN = "inactive_domain"

# A moment_track tab is never dropped (Kfir, phase-1 close): with no same-
# component source it stays as planned and assembly fills it from the moment
# fallback chain (memory outline → YouTube chapters → description timestamps).
NEVER_DROPPED = frozenset({"moment_track"})

# D10: learning.keyIdeas is the argument tab of a talk, a lecture, a podcast or an
# interview, whatever the primary domain. A plan that names it has asked for that
# extraction, so its domain is switched on rather than the tab re-pointed (S13,
# 2026-10-21: tagged podcast + tech, its Positions tab became the guest list).
ARGUMENT_PATHS = frozenset({"learning.keyIdeas"})
# 4.1: an interview's Q&A and guest card are podcast extraction whatever domain the
# probe gave (S14 arrives as learning + interview through ``playbookAliases``): a plan
# that names them has asked for podcast, never for learning.timestamps in their place.
INTERVIEW_PATHS = frozenset({"podcast.qa", "podcast.guests"})
# Only on a run the probe calls an interview or a podcast (phase-4 review A-W4): a talk
# or lecture plan that names podcast.qa for its audience questions is re-pointed to
# learning.timestamps as before, never podcast-tagged (podcast.txt in block 1, a "Guest"
# section in Key Ideas). With no probe format (an override, a failed probe) nothing says
# otherwise, so the plan's tab stands.
INTERVIEW_FORMATS = frozenset({"interview", "podcast"})
MAX_CONTENT_TAGS = 3


def _activating(path: str, content_format: str | None) -> bool:
    if path in ARGUMENT_PATHS:
        return True
    return path in INTERVIEW_PATHS and (
        content_format is None or content_format in INTERVIEW_FORMATS
    )


def activate_argument_domains(
    tabs: Sequence[Mapping], content_tags: Sequence[str], content_format: str | None = None
) -> list[str]:
    """``content_tags`` plus the domain of every argument path a tab reads, and of every
    interview path on an interview or podcast run (``content_format``, the probe's).

    When that pushes the list past :data:`MAX_CONTENT_TAGS`, tags no tab reads
    (never the primary, the first) give way from the end.
    """
    tags = list(content_tags)
    for tab in tabs:
        path = str(tab.get("dataSource") or "")
        spec = data_source(path)
        if spec is not None and _activating(path, content_format) and spec["domain"] not in tags:
            tags.append(spec["domain"])
    return _trim_unused_tags(tags, tabs)


def _trim_unused_tags(tags: list[str], tabs: Sequence[Mapping]) -> list[str]:
    read = {str(t.get("dataSource") or "").split(".", 1)[0] for t in tabs}
    for tag in reversed(tags[1:]):
        if len(tags) <= MAX_CONTENT_TAGS:
            break
        if tag not in read:
            tags.remove(tag)
    return tags


def _inactive(spec: DataSourceSpec | None, active: set[str]) -> bool:
    """True for a content/modifier path whose domain the plan did not tag.

    Non-content sources (``enrichment.quiz``) and unregistered paths never are.
    """
    if spec is None:
        return False
    domain = spec["domain"]
    return domain in valid_content_tags() | valid_modifiers() and domain not in active


def _eligible(
    spec: DataSourceSpec, component: str, active: set[str], evidence: Mapping[str, bool]
) -> bool:
    key = spec.get("requiresEvidence")
    return (
        spec["domain"] in active
        and component in spec["components"]
        and (not key or evidence.get(key) is not False)
    )


def replacement(
    path: str,
    component: str,
    active_order: Sequence[str],
    evidence: Mapping[str, bool],
    taken: set[tuple[str, str]],
) -> str | None:
    """The first same-component dataSource in an active domain.

    A source whose ``requiresEvidence`` key is False in ``evidence`` is
    skipped (callers pass the strict doubly-false view, or ``{}`` when no
    evidence applies). A source another tab already renders with the SAME
    component is skipped (a duplicate tab); a different component on the same
    source is a second view of it (steps in step_player and moment_track) and
    is allowed.
    """
    spec = data_source(path)
    siblings = list(spec.get("siblings") or []) if spec else []
    registry = data_sources()
    by_domain = [p for d in active_order for p, s in registry.items() if s["domain"] == d]
    active = set(active_order)
    for candidate in dict.fromkeys([*siblings, *by_domain]):
        candidate_spec = registry.get(candidate)
        if (
            candidate_spec is not None
            and (candidate, component) not in taken
            and _eligible(candidate_spec, component, active, evidence)
        ):
            return candidate
    return None


def _decide(tab: dict, target: str | None) -> tuple[str, dict | None, dict | None]:
    """``(action, kept_tab, dropped_entry)`` for a tab on an inactive domain."""
    if target is not None:
        return "repointed", {**tab, "dataSource": target}, None
    if tab["component"] in NEVER_DROPPED:
        return "kept_for_moment_fallback", tab, None
    entry = {
        "id": tab["id"],
        "component": tab["component"],
        "dataSource": tab.get("dataSource") or "",
        "reason": f"reconcile_{INACTIVE_DOMAIN}",
    }
    return "dropped", None, entry


def _record(tab: dict, path: str, target: str | None, action: str) -> dict:
    """One ``pipeline.reconcile`` row — the same shape reconcile.py writes."""
    return {
        "tabId": tab["id"],
        "component": tab["component"],
        "from": path,
        "to": target,
        "reason": INACTIVE_DOMAIN,
        "action": action,
        "stage": PLAN_PARSE,
        "evidence": None,
    }


def reconcile_plan_tabs(
    tabs: list[dict], content_tags: Sequence[str], modifiers: Sequence[str]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Re-point, keep (moment_track) or drop the tabs on a domain the plan did not tag.

    Returns ``(kept_tabs, dropped_tabs, records)``: ``dropped_tabs`` in the
    ``droppedTabs`` shape assembly persists, ``records`` for ``pipeline.reconcile``.
    """
    active_order = list(dict.fromkeys([*content_tags, *modifiers]))
    active = set(active_order)
    taken = {(t["dataSource"], t["component"]) for t in tabs if t.get("dataSource")}
    kept: list[dict] = []
    dropped: list[dict] = []
    records: list[dict] = []
    for tab in tabs:
        path = tab.get("dataSource") or ""
        if not _inactive(data_source(path), active):
            kept.append(tab)
            continue
        target = replacement(path, tab["component"], active_order, {}, taken)
        action, kept_tab, drop = _decide(tab, target)
        records.append(_record(tab, path, target, action))
        log = logger.warning if action == "dropped" else logger.info
        log(
            "Plan tab %r: inactive domain %r — %s %r (active %s)",
            tab["id"],
            path,
            action,
            target,
            active_order,
        )
        if kept_tab is not None:
            kept.append(kept_tab)
        if target is not None:
            taken.add((target, tab["component"]))
        if drop is not None:
            dropped.append(drop)
    return kept, dropped, records
