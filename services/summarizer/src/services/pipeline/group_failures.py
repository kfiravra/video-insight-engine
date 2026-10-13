"""What a failed extraction group costs on the split path (pipeline-1min 3.8).

A group call whose answer is lost is asked once more, unstreamed
(``extraction_groups.run_group_call``); a group still without data is a
``failed`` outcome. Failed fields count as landed (``group_emission``), so no
tab waits for them, and final assembly drops or demotes the tabs they fed
through today's fallbacks (registry siblings, the demote ladder, the
minimum-3 fill). Here the failures are told apart and named:

- :func:`answer_lost` — an answer validation kept nothing from either lost the
  group's content (ask again) or says there is none here (an empty, landed
  group: a later chunk's window, a video without that content);
- :func:`relabel_group_drops` — a tab assembly dropped for want of data while
  a field it requested failed is recorded in ``droppedTabs`` as
  ``extraction_group_failed``, assembly's own reason kept in ``detail``;
- :func:`flag_failed_groups` — a failed call marks the run degraded even when
  no timestamped field let ``finish_extraction`` measure coverage.

Every group of every chunk failing is today's error (``split_extraction``); a
failed plan call hands the domain's default tabs to the same request path
(``plan._build_fallback_plan``) — no evidence, so reconcile leaves them be.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .field_requests import FieldRequest, requested_fields
from .schema_fields import scalar_fields

if TYPE_CHECKING:
    from ...models.domain_validation import ValidationReport
    from ...models.pipeline_types import PlanResult
    from .context import PipelineContext
    from .field_groups import ExtractionGroup

logger = logging.getLogger(__name__)

EXTRACTION_GROUP_FAILED = "extraction_group_failed"
_FAILED = "failed"
# assembly/core.py's reasons for a tab left without usable data. Policy drops
# (domain_forbidden, domain_max_cap), evidence skips and impossible timestamps
# keep their own reason: a failed group is not what removed them.
_DATA_DROP_REASONS = frozenset(
    {"assembler_returned_none", "validation_empty_list", "assembler_raised"}
)


# ─── A lost answer ───


def _blank(value: Any) -> bool:
    """``null``, or an object holding only blanks: the model's "nothing here"."""
    if value is None:
        return True
    return isinstance(value, dict) and all(_blank(v) for v in value.values())


def _group_keys(group: ExtractionGroup) -> frozenset[str]:
    """The top-level keys an answer for ``group`` uses: its domains (as ``<domain>`` or
    ``<domain>Data``) or, flat, its fields' top keys."""
    domains = {f.domain for f in group.fields}
    flat = {f.field.split(".", 1)[0] for f in group.fields}
    return frozenset({*domains, *(f"{domain}Data" for domain in domains), *flat})


def answer_lost(
    answer: Mapping[str, Any], group: ExtractionGroup, report: ValidationReport
) -> bool:
    """Whether a parsed answer validation kept nothing from lost the group's content.

    Lost: validation reset a domain (``<domain>.*``), every key falls outside
    the group, or a group key held content validation dropped. Not lost: ``{}``,
    or the group's keys answered with ``null`` / ``{}`` only.
    """
    dropped = report.get("dropped") or {}
    if any(f"{f.domain}.*" in dropped for f in group.fields):
        return True
    named = [key for key in answer if key in _group_keys(group)]
    if answer and not named:
        return True
    return not all(_blank(answer[key]) for key in named)


# ─── Failed fields ───


def failed_paths(rows: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The field paths of every failed group call (``pipeline.extraction._groups`` rows).

    A field fails when any chunk's call of its group did: its data is incomplete.
    """
    return frozenset(
        str(path)
        for row in rows
        if row.get("status") == _FAILED
        for path in row.get("fields") or []
    )


def failed_requests(
    plan: PlanResult | None, rows: Iterable[Mapping[str, Any]]
) -> list[FieldRequest]:
    """The fields the plan's tabs request — as the split extraction asks for them — whose
    group call failed. The final plan's: frames-done may have re-pointed a tab."""
    paths = failed_paths(rows)
    if plan is None or not paths:
        return []
    requests = requested_fields(
        plan.tabs, plan.content_tags, plan.modifiers, scalar_fields, primary_tag=plan.primary_tag
    )
    return [request for request in requests if request.path in paths]


# ─── droppedTabs ───


def _failed_by_tab(failed_fields: Sequence[FieldRequest]) -> dict[str, list[str]]:
    """Tab id → the failed paths it waited for (its own field, siblings, side reads)."""
    by_tab: dict[str, list[str]] = {}
    for request in failed_fields:
        for tab_id in request.tab_ids:
            by_tab.setdefault(tab_id, []).append(request.path)
    return by_tab


def _relabeled(entry: Mapping[str, Any], paths: Sequence[str]) -> dict[str, Any]:
    detail = f"{', '.join(paths)} failed — {entry.get('reason')}"
    if entry.get("detail"):
        detail += f" ({entry['detail']})"
    return {**entry, "reason": EXTRACTION_GROUP_FAILED, "detail": detail}


def relabel_group_drops(
    dropped: Sequence[Mapping[str, Any]], failed_fields: Sequence[FieldRequest]
) -> list[dict[str, Any]]:
    """``dropped`` (the ``droppedTabs`` shape) with every data drop of a tab that waited
    for a failed field relabeled ``extraction_group_failed``; the rest unchanged."""
    by_tab = _failed_by_tab(failed_fields)
    relabeled: list[dict[str, Any]] = []
    for entry in dropped:
        paths = by_tab.get(str(entry.get("id") or ""))
        if paths and entry.get("reason") in _DATA_DROP_REASONS:
            relabeled.append(_relabeled(entry, paths))
        else:
            relabeled.append(dict(entry))
    return relabeled


def group_drops(ctx: PipelineContext, dropped: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Assembly's drops with the failed groups' tabs relabeled (as given on today's path:
    it records no group rows)."""
    # getattr: assembly's lightweight contexts (tests) predate the split path.
    rows = getattr(ctx, "extraction_groups", None) or []
    return relabel_group_drops(dropped, failed_requests(ctx.plan_result, rows))


# ─── Degraded ───


def flag_failed_groups(ctx: PipelineContext, calls: int, landed: int) -> None:
    """A failed group call marks the run degraded (``coverage_is_degraded``).

    ``finish_extraction`` records the call counts only beside a measured
    coverage, which needs a timestamped field — most split runs request none.
    """
    if landed >= calls or ctx.extraction_coverage is not None:
        return
    ctx.extraction_coverage = {
        "batchesTotal": calls,
        "batchesSucceeded": landed,
        "batchesDropped": calls - landed,
    }
    logger.warning(
        "[pipeline] %d of %d extraction group calls failed — run degraded video_id=%s",
        calls - landed,
        calls,
        ctx.video_summary_id,
    )
