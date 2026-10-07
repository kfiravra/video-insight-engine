"""``<video_memory>`` block — the per-run context every writer reads (pipeline-1min B.4).

Rendered by code from the plan and the memory stage and placed in the
``{video_context}`` slot of extraction, synthesis and enrichment. ≈ 250–400 tokens.

Deterministic: the same plan + memory always give the same bytes (no clock, no
set/dict iteration order, every list capped in a fixed order). Render it once per
run, after plan ∥ memory, and hand the same string to every call — that is what
keeps it byte-identical across the calls of a run (and cacheable from 1c on).

Evidence time ranges come from the plan's tab briefs: a tab whose dataSource's
registry entry ``requiresEvidence`` K lends its brief ``where`` ranges to K. The
memory outline is not used for them — its sections carry titles, not evidence
keys, so mapping one to the other would be a guess. A true key without such a
tab renders without ranges.

``memory=None`` (memory failed) renders the plan's view only: no outline line,
evidence = the plan's.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...models.memory_types import MemoryResult, OutlineSection
from ...models.pipeline_types import PlanResult
from ...shared_config.domain_config import (
    EVIDENCE_KEYS,
    data_source,
    quiz_policy,
    requirement_evidence,
)
from ...utils.data_helpers import parse_timestamp_to_seconds
from .memory import OUTLINE_TITLE_MAX_CHARS, format_clock, truncate_words
from .pipeline_helpers import sanitize_for_prompt

OPEN_TAG = "<video_memory>"
CLOSE_TAG = "</video_memory>"

MAX_RANGES_PER_KEY = 2
MAX_TERMS = 12
# Per-value caps: they bound the block when the plan rambles; typical values
# are far shorter, which is what lands the block at ≈ 250–400 tokens.
DOMAINS_MAX_CHARS = 80
GOAL_MAX_CHARS = 120
PROSE_MAX_CHARS = 120
CREATOR_MAX_CHARS = 40
TONE_MAX_CHARS = 30
TERM_MAX_CHARS = 48


# ─── Text ───


def _clean(text: str, max_chars: int) -> str:
    """One prompt-safe line: no braces/tags (LLM text echoes the transcript), capped."""
    flat = " ".join(sanitize_for_prompt(text, max_len=len(text)).split())
    return truncate_words(flat, max_chars)


def _labelled(parts: list[tuple[str, str, int]]) -> str:
    """``label: value · label: value`` over the parts that have a value."""
    cleaned = [(label, _clean(value, cap)) for label, value, cap in parts]
    return " · ".join(f"{label}: {value}" for label, value in cleaned if value)


# ─── Plan lines ───


def _domains(plan: PlanResult) -> str:
    tags = ", ".join(plan.content_tags)
    if plan.modifiers:
        tags += f" (+{', +'.join(plan.modifiers)})"
    return tags


def _identity_lines(plan: PlanResult) -> list[str]:
    guidance = plan.extraction_guidance
    rows = [
        _labelled(
            [
                ("domains", _domains(plan), DOMAINS_MAX_CHARS),
                ("goal", plan.user_goal, GOAL_MAX_CHARS),
            ]
        ),
        _labelled(
            [
                ("promise", plan.core_promise, PROSE_MAX_CHARS),
                ("angle", plan.unique_angle, PROSE_MAX_CHARS),
                ("creator", plan.identity.creator_type, CREATOR_MAX_CHARS),
                ("tone", plan.identity.tone, TONE_MAX_CHARS),
            ]
        ),
        _labelled(
            [
                ("focus", guidance.primary_focus, PROSE_MAX_CHARS),
                ("watch out", guidance.watch_out_for, PROSE_MAX_CHARS),
            ]
        ),
    ]
    return [row for row in rows if row]


def _terms_lines(plan: PlanResult) -> list[str]:
    terms = [_clean(term, TERM_MAX_CHARS) for term in plan.terms[:MAX_TERMS]]
    terms = [term for term in terms if term]
    return [f"terms: {'; '.join(terms)}"] if terms else []


# ─── Memory lines ───


def _outline_row(section: OutlineSection) -> str:
    span = f"{format_clock(section.start)}–{format_clock(section.end)}"
    return f"  {span} {_clean(section.title, OUTLINE_TITLE_MAX_CHARS)}"


def _outline_lines(memory: MemoryResult | None) -> list[str]:
    if memory is None or not memory.outline:
        return []
    return ["outline:", *map(_outline_row, memory.outline)]


# ─── Evidence ───


def merge_evidence(
    plan_evidence: Mapping[str, bool], memory_evidence: Mapping[str, bool] | None
) -> dict[str, bool]:
    """The readers' combined view, in vocabulary order.

    True when either reader says true (Appendix C: a key is false only when
    it is doubly false); False when a reader answered false and none true;
    absent when neither answered.
    """
    readers = [plan_evidence] if memory_evidence is None else [plan_evidence, memory_evidence]
    merged: dict[str, bool] = {}
    for key in EVIDENCE_KEYS:
        answers = [reader[key] for reader in readers if key in reader]
        if answers:
            merged[key] = any(answers)
    return merged


def _tab_evidence_key(tab: Mapping[str, object]) -> str | None:
    """The evidence key the tab's dataSource requires (registry), if any."""
    path = tab.get("dataSource")
    spec = data_source(path) if isinstance(path, str) and path else None
    return spec["requiresEvidence"] if spec else None


def _relevant_keys(plan: PlanResult) -> set[str]:
    """Keys whose ``✗`` steers this run: planned tabs, planned domains' requirements, quiz."""
    keys = {quiz_policy()["requiresEvidence"]}
    requirements = requirement_evidence()
    for tag in plan.content_tags:
        keys.update(requirements.get(tag, {}).values())
    keys.update(key for key in map(_tab_evidence_key, plan.tabs) if key)
    return keys


def _range_start(time_range: str) -> int:
    return parse_timestamp_to_seconds(time_range.split("-", 1)[0]) or 0


def _brief_where(tab: Mapping[str, object]) -> list[str]:
    """The tab's brief ranges (already normalized ``m:ss-m:ss`` by ``TabBrief``)."""
    brief = tab.get("brief")
    where = brief.get("where") if isinstance(brief, dict) else None
    return [r for r in where if isinstance(r, str)] if isinstance(where, list) else []


def _evidence_ranges(plan: PlanResult) -> dict[str, list[str]]:
    """Evidence key → the brief ``where`` ranges of the tabs that require it, earliest first."""
    ranges: dict[str, list[str]] = {}
    for tab in plan.tabs:
        key = _tab_evidence_key(tab)
        if key is None:
            continue
        bucket = ranges.setdefault(key, [])
        for time_range in _brief_where(tab):
            if time_range not in bucket:
                bucket.append(time_range)
    return {key: sorted(found, key=_range_start) for key, found in ranges.items()}


def _evidence_item(key: str, value: bool, ranges: list[str]) -> str:
    if not value:
        return f"{key} ✗"
    shown = ", ".join(r.replace("-", "–") for r in ranges[:MAX_RANGES_PER_KEY])
    return f"{key} ✓ {shown}" if shown else f"{key} ✓"


def _evidence_lines(plan: PlanResult, memory: MemoryResult | None) -> list[str]:
    """Every true key (with ranges) + the false keys this run's tabs/domains care about."""
    merged = merge_evidence(plan.evidence, memory.evidence if memory else None)
    relevant = _relevant_keys(plan)
    ranges = _evidence_ranges(plan)
    items = [
        _evidence_item(key, value, ranges.get(key, []))
        for key, value in merged.items()
        if value or key in relevant
    ]
    return [f"evidence: {' · '.join(items)}"] if items else []


# ─── Block ───


def render_video_memory(plan: PlanResult, memory: MemoryResult | None) -> str:
    """The ``<video_memory>`` block for one run (see the module docstring)."""
    lines = [
        OPEN_TAG,
        *_identity_lines(plan),
        *_outline_lines(memory),
        *_terms_lines(plan),
        *_evidence_lines(plan, memory),
        CLOSE_TAG,
    ]
    return "\n".join(lines)
