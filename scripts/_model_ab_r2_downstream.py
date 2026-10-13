"""Where a plan's duplicate dataSources go downstream (model A/B round 2, pipeline-1min 4.0d).

Every plan answer goes through the real code that turns a plan into tabs and
split-extraction groups, offline:

1. ``plan._plan_from_response`` (plan.py:300) — ``_normalize_plan_data``
   (plan.py:251) runs the plan-parse guard ``reconcile_plan_tabs``
   (plan.py:276 → plan_reconcile.py:126; inactive domains only);
2. ``reconcile.reconcile_plan`` (reconcile.py:322) — the plan-done pass,
   whose ``dedupe_step`` (reconcile_pass.py:286) keeps one tab per
   (dataSource, component): a second tab on a source with the SAME component
   is dropped, one with another component is kept as a second view;
3. ``requested_fields`` (field_requests.py:395; ``_put`` field_requests.py:340
   merges a field several tabs read into one request) → ``build_groups``
   (field_groups.py:156), as ``split_extraction`` calls them
   (phases/split_extraction.py:71, :104).

A duplicate is two tabs of the parsed plan on one dataSource. Its outcome:
dropped at plan-done, or kept as a second tab — and whether that costs a group
call: the groups of the final plan against those of the same plan with each
source's later tabs removed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from _model_ab_scores import _plan_video

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.services.pipeline import plan as plan_module
from src.services.pipeline.field_groups import build_groups
from src.services.pipeline.field_requests import requested_fields
from src.services.pipeline.memory import parse_memory_output
from src.services.pipeline.reconcile import reconcile_plan
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.domain_config import grouping_config
from src.utils.json_parsing import parse_json_response

CODE_PATH = (
    "plan.py:300 _plan_from_response → plan.py:276 reconcile_plan_tabs (plan_reconcile.py:126) → "
    "reconcile.py:322 reconcile_plan → reconcile_pass.py:286 dedupe_step (one tab per "
    "dataSource + component) → field_requests.py:395 requested_fields (_put :340 merges a "
    "field several tabs read) → field_groups.py:156 build_groups (split_extraction.py:71, :104)"
)


def _sources(tabs: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """dataSource → the ``id(component)`` of every tab on it (tabs without one skipped)."""
    found: dict[str, list[str]] = {}
    for tab in tabs:
        source = str(tab.get("dataSource") or "")
        if source:
            found.setdefault(source, []).append(f"{tab['id']}({tab['component']})")
    return found


def _groups(plan: PlanResult, tabs: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    fields = requested_fields(
        tabs, plan.content_tags, plan.modifiers, scalar_fields, primary_tag=plan.primary_tag
    )
    return [
        [f.path for f in group.fields] for group in build_groups(fields, tabs, grouping_config())
    ]


def _first_per_source(tabs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    kept = []
    for tab in tabs:
        source = str(tab.get("dataSource") or "")
        if source and source in seen:
            continue
        seen.add(source)
        kept.append(tab)
    return kept


def _outcome(parsed: Sequence[str], final: Sequence[str]) -> str:
    """What plan-done made of the tabs sharing one source (dedupe drops a repeated component)."""
    dropped = len(parsed) - len(final)
    kept = f"{len(final)} kept ({' + '.join(final)})" if final else "none kept"
    return f"{kept}, {dropped} dropped at plan-done" if dropped else kept


def memory_of(raw: str | None, duration: int) -> MemoryResult | None:
    """The memory answer the plan-done reconcile reads (None without one)."""
    data = parse_json_response(raw) if raw else None
    return parse_memory_output(data, duration) if data else None


def downstream(raw: str, context: Mapping[str, Any], memory: MemoryResult | None) -> dict[str, Any]:
    """One plan answer's duplicate sources through parse, plan-done reconcile and grouping."""
    parsed = plan_module._plan_from_response(raw, _plan_video(context))
    final = reconcile_plan(parsed, memory, context.get("content_format"))
    before = {s: t for s, t in _sources(parsed.tabs).items() if len(t) > 1}
    after = _sources(final.tabs)
    groups = _groups(final, final.tabs)
    deduped = _groups(final, _first_per_source(final.tabs))
    outcomes = {source: _outcome(tabs, after.get(source, [])) for source, tabs in before.items()}
    return {
        "duplicates_parsed": before,
        "outcomes": outcomes,
        "tabs_final": [
            f"{t['id']}({t['component']}:{t.get('dataSource') or '-'})" for t in final.tabs
        ],
        "group_calls": len(groups),
        "group_calls_deduped": len(deduped),
        "extra_group_calls": len(groups) - len(deduped),
        "same_fields_as_deduped": sorted(map(sorted, groups)) == sorted(map(sorted, deduped)),
    }
