"""Plan prompt rendering — ``plan.txt`` filled from the registry and one video.

Static blocks come from config (toolkit, registry caps, conditional domain
requirements); the dynamic ``<video>`` block carries title, channel, duration,
the tier probe's ``Hint:``, the playbook, the description, and then the FULL
transcript with ``[m:ss]`` markers (pipeline-1min Appendix B.2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ...shared_config.domain_config import (
    effective_requirements,
    get_playbook,
    map_category_to_tag,
    render_density_gate_table,
    render_domain_requirements,
    render_extraction_caps,
    render_requirement,
    render_valid_component_names,
    render_valid_datasources,
)
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "plan.txt"
COMPONENT_TOOLKIT_PATH = Path(__file__).parent.parent.parent / "prompts" / "component_toolkit.txt"

_MAX_HINT_CHARS = 300
_NO_TRANSCRIPT = "(no transcript — plan from the title and description)"


@dataclass(frozen=True)
class PlanVideo:
    """The per-video inputs of one plan call."""

    title: str
    channel: str
    description: str
    duration: int
    category_hint: str | None
    content_format: str | None
    transcript: str
    probe_hint: str | None = None


def _load_plan_prompt() -> str:
    """Registry-first plan prompt. Records version on the active trace."""
    return load_prompt_text(PROMPT_PATH)


def _load_component_toolkit() -> str:
    """Registry-first component toolkit reference. Missing local file → ``""``."""
    if not COMPONENT_TOOLKIT_PATH.exists():
        logger.warning("Component toolkit not found at %s", COMPONENT_TOOLKIT_PATH)
        return ""
    return load_prompt_text(COMPONENT_TOOLKIT_PATH)


def _render_playbook(category_hint: str | None, content_format: str | None) -> str:
    """Render the {domain_playbook} block for the plan prompt's dynamic part.

    Empty string when no playbook matches — the placeholder simply vanishes.
    Uses the hint domain (a confident tier probe's, else the metadata category —
    neither is final until the plan itself runs); the code-level policy in
    plan._enforce_domain_policy uses the plan's own primaryTag, so a hint/plan
    disagreement is still safe.
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
        # Conditional like <domain_requirements>: the plan's own evidence decides.
        required = ", ".join(render_requirement(domain, c) for c in policy["required"])
        lines.append(f"Required components: {required}")
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


def _render_static(template: str) -> str:
    """Fill the config-derived blocks — registry and toolkit text, no video data.

    ``{density_gates}`` and ``{valid_datasources}`` live inside the toolkit, so
    they are replaced AFTER ``{component_toolkit}`` is injected. str.replace is
    a no-op for a registry-served template that predates a placeholder.
    """
    return (
        template.replace("{component_toolkit}", _load_component_toolkit())
        .replace("{density_gates}", render_density_gate_table())
        .replace("{valid_datasources}", render_valid_datasources())
        .replace("{valid_components}", render_valid_component_names())
        .replace("{extraction_caps}", render_extraction_caps())
        .replace("{domain_requirements}", render_domain_requirements())
    )


def _extra_line(text: str) -> str:
    """``text`` as an extra line of the ``<video>`` block, or "" — never a blank line."""
    return f"\n{text}" if text else ""


def _render_hint(probe_hint: str | None) -> str:
    """The tier probe's guess as one ``Hint:`` line, or "" when unset."""
    hint = (probe_hint or "").strip()
    return f"Hint: {sanitize_for_prompt(hint, max_len=_MAX_HINT_CHARS)}" if hint else ""


def _render_video(template: str, video: PlanVideo) -> str:
    """Fill the per-video values; every untrusted one is brace/tag-sanitized.

    The transcript is the whole marked transcript (no truncation) and goes in
    LAST, so no later replace can touch text inside it.
    """
    duration_minutes = str(round(video.duration / 60)) if video.duration > 0 else "unknown"
    description = video.description[:1000] if video.description else "N/A"
    playbook = _render_playbook(video.category_hint, video.content_format)
    transcript = video.transcript.strip() or _NO_TRANSCRIPT
    return (
        template.replace("{title}", sanitize_for_prompt(video.title[:200]))
        .replace("{channel}", sanitize_for_prompt(video.channel[:100] or "Unknown"))
        .replace("{duration_minutes}", duration_minutes)
        .replace("{probe_hint}", _extra_line(_render_hint(video.probe_hint)))
        .replace("{domain_playbook}", _extra_line(playbook))
        .replace("{description}", sanitize_for_prompt(description, max_len=1000))
        .replace("{transcript}", sanitize_for_prompt(transcript, max_len=len(transcript)))
    )


def render_plan_prompt(video: PlanVideo) -> str:
    """The full plan prompt: language directive + static blocks + this video.

    Raises:
        FileNotFoundError: plan.txt is missing from disk and the registry.
    """
    template = _load_plan_prompt()
    return ENGLISH_OUTPUT_DIRECTIVE + "\n\n" + _render_video(_render_static(template), video)
