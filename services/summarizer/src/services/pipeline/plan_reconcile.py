"""Plan-time reconcile — the simple form of phase 3.2, moved up (hotfix 2.1).

After the plan returns, a tab whose dataSource cannot have content is
re-pointed or dropped before extraction runs:

- ``evidence_false``: the registry's ``requiresEvidence`` key for the
  dataSource is false in the plan's own evidence;
- ``inactive_domain``: the dataSource's domain is not one of the plan's
  contentTags/modifiers (``news.claims`` on a learning/tech video — the
  extraction is domain-keyed, so that path is never filled).

A dataSource rendered by the same component in an active domain replaces it
(the registry's declared siblings first, then the active domains in plan
order); otherwise the tab is dropped. Every decision is recorded for
``pipeline.reconcile``; drops also join ``droppedTabs`` so the tab accounting
stays complete.
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


def _violation(
    spec: DataSourceSpec | None, active: set[str], evidence: Mapping[str, bool]
) -> str | None:
    """Why a registered dataSource cannot have content here; None when it can.

    Non-content sources (``enrichment.quiz``) skip the domain check but keep
    the evidence one. A key the plan did not answer is unknown, not false.
    """
    if spec is None:
        return None
    domain = spec["domain"]
    if domain in valid_content_tags() | valid_modifiers() and domain not in active:
        return "inactive_domain"
    key = spec.get("requiresEvidence")
    if key and evidence.get(key) is False:
        return "evidence_false"
    return None


def _eligible(
    spec: DataSourceSpec, component: str, active: set[str], evidence: Mapping[str, bool]
) -> bool:
    key = spec.get("requiresEvidence")
    return (
        spec["domain"] in active
        and component in spec["components"]
        and (not key or evidence.get(key) is not False)
    )


def _replacement(
    path: str,
    component: str,
    active_order: Sequence[str],
    evidence: Mapping[str, bool],
    taken: set[tuple[str, str]],
) -> str | None:
    """The first same-component dataSource in an active domain.

    A source another tab already renders with the SAME component is skipped
    (a duplicate tab); a different component on the same source is a second
    view of it (steps in step_player and moment_track) and is allowed.
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


def reconcile_plan_tabs(
    tabs: list[dict],
    content_tags: Sequence[str],
    modifiers: Sequence[str],
    evidence: Mapping[str, bool],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Re-point or drop the tabs whose dataSource cannot have content.

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
        reason = _violation(data_source(path), active, evidence)
        if reason is None:
            kept.append(tab)
            continue
        replacement = _replacement(path, tab["component"], active_order, evidence, taken)
        records.append(
            {
                "tabId": tab["id"],
                "component": tab["component"],
                "from": path,
                "to": replacement,
                "reason": reason,
            }
        )
        if replacement is None:
            logger.warning(
                "Plan tab dropped: id=%r dataSource=%r (%s) — no %s dataSource in %s",
                tab["id"],
                path,
                reason,
                tab["component"],
                active_order,
            )
            dropped.append(
                {
                    "id": tab["id"],
                    "component": tab["component"],
                    "dataSource": path,
                    "reason": f"reconcile_{reason}",
                }
            )
            continue
        logger.info("Plan tab %r: %s %r -> %r", tab["id"], reason, path, replacement)
        taken.add((replacement, tab["component"]))
        kept.append({**tab, "dataSource": replacement})
    return kept, dropped, records
