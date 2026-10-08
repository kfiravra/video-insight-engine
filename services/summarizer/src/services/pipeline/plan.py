"""Plan pipeline stage — video analysis + tab design in one Sonnet call.

The planner reads the whole transcript (``[m:ss]`` markers) and returns the
domain, 3–6 tabs with a ``brief`` each, the Appendix-C ``evidence`` booleans
and the canonical ``terms`` (pipeline-1min Appendix B.2). Prompt rendering
lives in ``plan_prompt``; this module calls the LLM and validates the answer.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ...models.domain_types import MODIFIER_MODELS
from ...models.pipeline_types import PlanResult, TabBrief
from ...shared_config.domain_config import (
    build_fallback_tabs,
    effective_requirements,
    map_category_to_tag,
    registered_data_source,
    valid_components,
    valid_content_tags,
    valid_modifiers,
)
from ...utils.json_parsing import parse_json_response
from ...utils.llm_retry import call_llm_with_retry
from .assembly import infer_component
from .plan_prompt import PROMPT_PATH, PlanVideo, render_plan_prompt
from .plan_reconcile import reconcile_plan_tabs

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6

# The plan sits on the critical path and its fallback plan still ships tabs, so
# a slow call gets one retry, not two. There is one plan call per run, so a
# prompt cache would be written and never read (A26): no cache_control.
# Sized from measured phase-1 plans: 1,814–1,920 output tokens in 36.7–39.8 s.
# 2,048 tokens truncated the answer (the JSON repair then silently dropped the
# trailing tabs) and the brief's 45 s sat ~5 s above the measured wall, so a
# slow answer timed out and retried into the fallback plan. 3,500 tokens is
# ~1.8x the measured answer; 60 s covers it at the measured rate.
PLAN_TIMEOUT_SECONDS = 60.0
PLAN_MAX_RETRIES = 1
PLAN_MAX_TOKENS = 3500

# Components whose assembler builds from synthesis/meta and ignores the tab's
# data, so their dataSource needs no registry check (mirrors the overview half
# of ``_SELF_SUFFICIENT`` in assembly/core.py — ``budget`` does read its data).
_SELF_SUFFICIENT_COMPONENTS = frozenset({"overview"})


def _build_fallback_plan(category_hint: str | None = None) -> PlanResult:
    """Build a fallback PlanResult when LLM fails or confidence is low."""
    primary = map_category_to_tag(category_hint) if category_hint else "learning"
    tags = [primary]

    return PlanResult.model_validate(
        {
            "contentTags": tags,
            "modifiers": [],
            "primaryTag": primary,
            "userGoal": "General summary of the video content",
            "tabs": build_fallback_tabs(primary),
            "confidence": 0.0,
        }
    )


def _enforce_domain_policy(
    tabs: list[dict], primary_tag: str, content_format: str | None
) -> list[dict]:
    """Remove tabs whose component is forbidden for this (domain, format).

    Runs after primaryTag normalization so the policy lookup is correct, and
    before extraction so forbidden tabs never consume downstream tokens.
    """
    forbidden = effective_requirements(primary_tag, content_format)["forbidden"]
    if not forbidden:
        return tabs

    kept: list[dict] = []
    for tab in tabs:
        component = tab.get("component") or infer_component(tab.get("id", ""))
        if component in forbidden:
            logger.warning(
                "Plan tab removed: id=%r component=%r — forbidden for domain=%s format=%s",
                tab.get("id"),
                component,
                primary_tag,
                content_format,
            )
            continue
        kept.append(tab)
    return kept


def _validate_tabs(tabs: list[dict]) -> list[dict]:
    """Validate and normalize tabs from LLM response."""

    if not isinstance(tabs, list):
        return []

    valid_component_names = valid_components()
    valid_tabs = []
    seen_ids: set[str] = set()

    for tab in tabs[:6]:
        if not isinstance(tab, dict) or "id" not in tab or "label" not in tab:
            continue

        tid = tab["id"]
        if not re.match(r"^[a-z][a-z0-9_]*$", tid):
            logger.info("Invalid tab ID format '%s', skipping", tid)
            continue
        if tid in seen_ids:
            logger.info("Duplicate tab ID '%s', skipping", tid)
            continue
        seen_ids.add(tid)

        component = tab.get("component", "")
        if component and component not in valid_component_names:
            logger.info(
                "Invalid component '%s' for tab '%s', inferring from tab ID", component, tid
            )
            component = ""
        if not component:
            component = infer_component(tid)

        valid_tabs.append(
            {
                "id": tid,
                "label": tab["label"],
                "emoji": tab.get("emoji", ""),
                "dataSource": _coerce_data_source(tab.get("dataSource")),
                "component": component,
                "goal": tab.get("goal", ""),
                "brief": TabBrief.from_raw(tab.get("brief")).model_dump(),
            }
        )

    return valid_tabs


def _coerce_data_source(raw: object) -> str:
    """The LLM's dataSource as a string; null/list/dict → "" (an unset source)."""
    return raw if isinstance(raw, str) else ""


def _is_model_field(model: type[BaseModel], key: str) -> bool:
    return any(key in (name, info.alias) for name, info in model.model_fields.items())


def _bypasses_registry(tab: dict) -> bool:
    """True for a dataSource assembly resolves although the registry does not list it.

    - self-sufficient components (overview) ignore their data entirely;
    - a bare domain (``"review"``, ``"fitness"``) or ``"<domain>.*"`` reads the
      whole domain object — e.g. review pros_cons needs pros AND cons;
    - a modifier field (``finance.costs``) — modifiers extract into their own
      model, and finance has no registry entries at all.
    """
    if tab["component"] in _SELF_SUFFICIENT_COMPONENTS or tab["id"] == "overview":
        return True
    domain, _, field = tab["dataSource"].partition(".")
    if field in ("", "*"):
        return domain in valid_content_tags() | valid_modifiers()
    modifier_model = MODIFIER_MODELS.get(domain)
    return modifier_model is not None and _is_model_field(modifier_model, field.split(".")[0])


def _resolve_data_source(tab: dict) -> str | None:
    """The dataSource a validated tab should keep; None when it must be dropped."""
    planned = tab["dataSource"]
    if not planned or _bypasses_registry(tab):
        return planned
    return registered_data_source(planned, tab["component"])


def _validate_data_sources(tabs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Check every tab's dataSource against the registry before extraction.

    An unregistered path (copied verbatim from the LLM) used to reach
    extraction and burned 3 of 7 extraction retries without ever resolving.
    Swap in a same-domain path rendered by the same component; otherwise drop
    the tab and return it in the ``droppedTabs`` shape assembly persists.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for tab in tabs:
        planned = tab["dataSource"]
        resolved = _resolve_data_source(tab)
        if resolved is None:
            logger.warning(
                "Plan tab dropped: id=%r dataSource=%r is not registered and no %s sibling exists",
                tab["id"],
                planned,
                tab["component"],
            )
            dropped.append(
                {
                    "id": tab["id"],
                    "component": tab["component"],
                    "dataSource": planned,
                    "reason": "invalid_datasource",
                }
            )
            continue
        if resolved != planned:
            logger.info(
                "Plan tab %r: unregistered dataSource %r -> sibling %r",
                tab["id"],
                planned,
                resolved,
            )
        kept.append({**tab, "dataSource": resolved})
    return kept, dropped


def _plan_modifiers(data: dict) -> list[str]:
    raw = data.get("modifiers") or []
    items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [m for m in items if isinstance(m, str)]


def _plan_evidence(data: dict) -> dict[str, bool]:
    """The plan's evidence answers as PlanResult will keep them (booleans only)."""
    raw = data.get("evidence")
    return {k: v for k, v in raw.items() if isinstance(v, bool)} if isinstance(raw, dict) else {}


def _normalize_plan_data(data: dict, content_format: str | None) -> None:
    """Validate tabs and tags in the raw plan dict before PlanResult sees it.

    Order matters: dataSources are checked against the registry first, then
    primaryTag is normalized so the forbidden-component policy looks up the
    right domain, then the reconcile guard re-points or drops tabs whose
    dataSource the plan's own evidence/domains rule out. The prompt asks the
    planner to avoid both; this is the guarantee (assembly enforces the
    forbidden components again for cached plans).
    """
    validated_tabs, dropped_tabs = _validate_data_sources(_validate_tabs(data.get("tabs", [])))

    content_tags = data.get("contentTags", [])
    if isinstance(content_tags, str):
        content_tags = [content_tags]
    if not content_tags:
        content_tags = ["learning"]
    data["contentTags"] = content_tags

    primary_tag = data.get("primaryTag", content_tags[0])
    if primary_tag not in content_tags:
        primary_tag = content_tags[0]
    data["primaryTag"] = primary_tag

    reconciled_tabs, reconcile_drops, data["reconcile"] = reconcile_plan_tabs(
        validated_tabs, content_tags, _plan_modifiers(data), _plan_evidence(data)
    )
    data["droppedTabs"] = [*dropped_tabs, *reconcile_drops]
    data["tabs"] = _enforce_domain_policy(reconciled_tabs, primary_tag, content_format)
    # Fallback tabs if none valid — flagged so assembly does not count the
    # plan's drops on top of a tab set the planner never designed.
    if not data["tabs"]:
        data["tabs"] = build_fallback_tabs(primary_tag)
        data["planFallback"] = True


def _plan_from_response(raw: str, video: PlanVideo) -> PlanResult:
    """Parse + validate the planner's JSON; fallback plan on empty or low confidence."""
    data = parse_json_response(raw)
    if not data:
        logger.warning("Empty JSON from plan, falling back. Raw: %.300s", raw[:300])
        return _build_fallback_plan(video.category_hint)

    _normalize_plan_data(data, video.content_format)
    result = PlanResult.model_validate(data)
    if result.confidence < CONFIDENCE_THRESHOLD:
        logger.info("Low plan confidence (%.2f), falling back", result.confidence)
        fallback = _build_fallback_plan(video.category_hint)
        fallback.confidence = result.confidence
        return fallback
    return result


async def run_plan(
    title: str,
    channel: str,
    description: str,
    duration: int,
    category_hint: str | None,
    content_format: str | None,
    transcript: str,
    llm_service: LLMService,
    probe_hint: str | None = None,
) -> PlanResult:
    """Run the plan stage — one Sonnet call for video analysis + tab design.

    Args:
        title: Video title.
        channel: Channel name.
        description: Video description (first 1,000 chars reach the prompt).
        duration: Video duration in seconds.
        category_hint: Domain from the tier probe or metadata (playbook + fallback).
        content_format: Presentation format (playbook + forbidden-component policy).
        transcript: The FULL transcript with ``[m:ss]`` markers
            (``render_transcript``) — never a preview.
        llm_service: LLM service instance.
        probe_hint: The tier probe's one-line guess; rendered as ``Hint:`` when set.

    Returns:
        PlanResult on success, fallback PlanResult on failure.
    """
    video = PlanVideo(
        title=title,
        channel=channel,
        description=description,
        duration=duration,
        category_hint=category_hint,
        content_format=content_format,
        transcript=transcript,
        probe_hint=probe_hint,
    )
    try:
        prompt = render_plan_prompt(video)
    except FileNotFoundError:
        logger.error("Plan prompt not found at %s", PROMPT_PATH)
        return _build_fallback_plan(category_hint)

    try:
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            max_tokens=PLAN_MAX_TOKENS,
            timeout=PLAN_TIMEOUT_SECONDS,
            max_retries=PLAN_MAX_RETRIES,
            stage_name="plan",
            json_mode=True,
        )
        if not raw:
            logger.warning("Plan LLM call failed after retries, using fallback")
            return _build_fallback_plan(category_hint)

        logger.debug("Plan raw response (len=%d): %.500s", len(raw), raw)
        return _plan_from_response(raw, video)

    except Exception as e:
        logger.error("Plan failed (%s): %s — falling back", type(e).__name__, e)
        return _build_fallback_plan(category_hint)
