"""Plan pipeline stage — merged manifest + triage in a single LLM call.

Replaces the old 2-call flow (manifest → triage) with a single Sonnet call
that produces both video analysis and tab layout design.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ...models.domain_types import MODIFIER_MODELS
from ...models.pipeline_types import PlanResult
from ...shared_config.domain_config import (
    build_fallback_tabs,
    effective_requirements,
    get_playbook,
    map_category_to_tag,
    registered_data_source,
    render_density_gate_table,
    render_valid_component_names,
    render_valid_datasources,
    valid_components,
    valid_content_tags,
    valid_modifiers,
)
from ...utils.json_parsing import parse_json_response
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ...utils.llm_retry import call_llm_with_retry
from .assembly import infer_component
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "plan.txt"
COMPONENT_TOOLKIT_PATH = Path(__file__).parent.parent.parent / "prompts" / "component_toolkit.txt"

CONFIDENCE_THRESHOLD = 0.6

# Components whose assembler builds from synthesis/meta and ignores the tab's
# data, so their dataSource needs no registry check (mirrors the overview half
# of ``_SELF_SUFFICIENT`` in assembly/core.py — ``budget`` does read its data).
_SELF_SUFFICIENT_COMPONENTS = frozenset({"overview"})


def _load_plan_prompt() -> str:
    """Registry-first plan prompt. Records version on the active trace."""
    return load_prompt_text(PROMPT_PATH)


def _load_component_toolkit() -> str:
    """Registry-first component toolkit reference. Missing local file → ``""``."""
    if not COMPONENT_TOOLKIT_PATH.exists():
        logger.warning("Component toolkit not found at %s", COMPONENT_TOOLKIT_PATH)
        return ""
    return load_prompt_text(COMPONENT_TOOLKIT_PATH)


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


def _render_playbook(category_hint: str | None, content_format: str | None) -> str:
    """Render the {domain_playbook} block for the plan prompt's dynamic part.

    Empty string when no playbook matches — the placeholder simply vanishes.
    Uses the CATEGORY-derived domain (the classifier's domain isn't final until
    the plan itself runs); the code-level policy in _enforce_domain_policy uses
    the plan's own primaryTag, so a category/plan disagreement is still safe.
    """
    domain = map_category_to_tag(category_hint) if category_hint else None
    if not domain or not content_format:
        return ""
    # Gate on the PLAYBOOK existing, not on the merged policy (which unions
    # domain-level forbidden and would render for nearly every video). A
    # forbidden/required-only playbook must still steer the planner — its
    # tabs would otherwise only be stripped post-hoc, silently losing slots.
    if not get_playbook(domain, content_format):
        return ""
    policy = effective_requirements(domain, content_format)
    lines = [f'<playbook for="{domain}:{content_format}">']
    if policy["required"]:
        lines.append(f"Required components: {', '.join(policy['required'])}")
    if policy["preferred"]:
        lines.append(f"Preferred components: {', '.join(policy['preferred'])}")
    if policy["forbidden"]:
        lines.append(
            f"Forbidden components (validation removes them): {', '.join(sorted(policy['forbidden']))}"
        )
    if policy["planGuidance"]:
        lines.append(policy["planGuidance"])
    lines.append("</playbook>")
    return "\n".join(lines)


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

        # ``outboundLinks`` is an LLM-generated map of {target_tab_id: label}
        # in source language. Cross-tab resolution reads these to build the
        # per-link CTA text — keeps the labels in the same language as the
        # rest of the assembled output so the walker can translate them.
        raw_links = tab.get("outboundLinks")
        outbound_links: dict[str, str] = {}
        if isinstance(raw_links, dict):
            for target_id, lbl in raw_links.items():
                if isinstance(target_id, str) and isinstance(lbl, str) and lbl.strip():
                    outbound_links[target_id] = lbl.strip()

        valid_tabs.append(
            {
                "id": tid,
                "label": tab["label"],
                "emoji": tab.get("emoji", ""),
                "dataSource": _coerce_data_source(tab.get("dataSource")),
                "component": component,
                "goal": tab.get("goal", ""),
                "outboundLinks": outbound_links,
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


async def run_plan(
    title: str,
    channel: str,
    description: str,
    duration: int,
    category_hint: str | None,
    content_format: str | None,
    transcript_preview: str,
    llm_service: LLMService,
    content_traits: str | None = None,
) -> PlanResult:
    """Run the plan stage — single Sonnet call for video analysis + tab design.

    Merges the old manifest + triage stages into one call. Uses plan.txt prompt.

    Args:
        title: Video title.
        channel: Channel name.
        description: Video description (truncated).
        duration: Video duration in seconds.
        category_hint: Category from classifier or metadata.
        content_format: Content format from classifier (tutorial, commentary, etc.).
        transcript_preview: First ~3K chars of cleaned transcript.
        llm_service: LLM service instance.

    Returns:
        PlanResult on success, fallback PlanResult on failure.
    """
    try:
        prompt_template = _load_plan_prompt()
    except FileNotFoundError:
        logger.error("Plan prompt not found at %s", PROMPT_PATH)
        return _build_fallback_plan(category_hint)

    component_toolkit = _load_component_toolkit()
    duration_minutes = str(round(duration / 60)) if duration > 0 else "unknown"

    # Split prompt into static (cacheable) and dynamic parts.
    # Static: role + instructions + component_toolkit + output_schema + examples + rules
    # Dynamic: video details + transcript_preview
    # All four are config-derived static content (no video data) and single-
    # sourced from domains.json. {density_gates} and {valid_datasources} live
    # inside the toolkit text, so they must be replaced AFTER {component_toolkit}
    # is injected. str.replace is a no-op for a registry-served toolkit that
    # predates a placeholder, so old Langfuse versions still render.
    static_template = (
        ENGLISH_OUTPUT_DIRECTIVE
        + "\n\n"
        + (
            prompt_template.replace("{component_toolkit}", component_toolkit)
            .replace("{density_gates}", render_density_gate_table())
            .replace("{valid_datasources}", render_valid_datasources())
            .replace("{valid_components}", render_valid_component_names())
        )
    )

    # Split at the <video> tag for Anthropic prompt caching: the per-video
    # blocks are deliberately LAST in plan.txt, so everything before them — role
    # + instructions + toolkit + density gates + schema + examples + rules — is
    # the cached static system block, and only the small video + transcript blocks
    # are the dynamic per-request user message. (If <video> were near the top, the
    # cache prefix would collapse to the role preamble — see the note in plan.txt.)
    video_marker = "<video>"
    split_idx = static_template.find(video_marker)

    if split_idx > 0:
        cache_static = static_template[:split_idx]
        dynamic_part = static_template[split_idx:]
    else:
        cache_static = None
        dynamic_part = static_template

    prompt = (
        dynamic_part.replace("{title}", sanitize_for_prompt(title[:200]))
        .replace("{channel}", sanitize_for_prompt(channel[:100] if channel else "Unknown"))
        .replace("{duration_minutes}", duration_minutes)
        .replace("{category_hint}", category_hint or "unknown")
        .replace("{content_format}", content_format or "unknown")
        .replace("{domain_playbook}", _render_playbook(category_hint, content_format))
        .replace(
            "{description}",
            sanitize_for_prompt(description[:1000] if description else "N/A", max_len=1000),
        )
        .replace(
            "{transcript_preview}", sanitize_for_prompt(transcript_preview[:3000], max_len=3000)
        )
        .replace("{content_traits}", content_traits or "Not available")
    )

    try:
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            # Plan timeout bumped 30→60s after observing fresh-video failures:
            # KuXjwB4LzSA (3B1B Convolution) timed out 3× at 30s while Sonnet
            # processed Arabic auto-translated transcripts + the longer plan
            # prompt (post-overhaul). 60s gives realistic headroom; the assembler
            # fallback path still kicks in if all 3 attempts fail.
            max_tokens=2048,
            timeout=60.0,
            max_retries=2,
            stage_name="plan",
            json_mode=True,
            cache_static=cache_static,
        )
        if not raw:
            logger.warning("Plan LLM call failed after retries, using fallback")
            return _build_fallback_plan(category_hint)

        logger.debug("Plan raw response (len=%d): %.500s", len(raw), raw)
        data = parse_json_response(raw)

        if not data:
            logger.warning("Empty JSON from plan, falling back. Raw: %.300s", raw[:300])
            return _build_fallback_plan(category_hint)

        # Validate tabs before creating PlanResult
        raw_tabs = data.get("tabs", [])
        validated_tabs, dropped_tabs = _validate_data_sources(_validate_tabs(raw_tabs))
        data["tabs"] = validated_tabs
        data["droppedTabs"] = dropped_tabs

        # Normalize content tags
        content_tags = data.get("contentTags", [])
        if isinstance(content_tags, str):
            content_tags = [content_tags]
        if not content_tags:
            content_tags = ["learning"]
        data["contentTags"] = content_tags

        # Validate primary tag
        primary_tag = data.get("primaryTag", content_tags[0])
        if primary_tag not in content_tags:
            primary_tag = content_tags[0]
        data["primaryTag"] = primary_tag

        # Domain policy: strip forbidden components BEFORE extraction burns
        # tokens on them. The prompt asks the planner to avoid these, but a
        # prompt is a request — this is the guarantee. Assembly enforces the
        # same policy again as a backstop for cached/backfilled plans.
        validated_tabs = _enforce_domain_policy(validated_tabs, primary_tag, content_format)
        data["tabs"] = validated_tabs

        # Fallback tabs if none valid — flagged so assembly does not count the
        # plan's drops on top of a tab set the planner never designed.
        if not validated_tabs:
            data["tabs"] = build_fallback_tabs(primary_tag)
            data["planFallback"] = True

        result = PlanResult.model_validate(data)

        if result.confidence < CONFIDENCE_THRESHOLD:
            logger.info("Low plan confidence (%.2f), falling back", result.confidence)
            fallback = _build_fallback_plan(category_hint)
            fallback.confidence = result.confidence
            return fallback

        return result

    except Exception as e:
        logger.error("Plan failed (%s): %s — falling back", type(e).__name__, e)
        return _build_fallback_plan(category_hint)
