"""Plan prompt rendering — ``plan.txt`` filled from the registry and one video.

Static blocks come from config (toolkit, registry caps, conditional domain
requirements); the dynamic ``<video>`` block carries title, channel, duration,
the tier probe's ``Hint:``, the playbook, the description, and then the FULL
transcript with ``[m:ss]`` markers (pipeline-1min Appendix B.2).

The template is cut at its ``<video>`` line and the part before it is rendered
without any video input, so that static half is byte-identical for every video
and can be served from the prompt cache across runs (``PlanPrompt``).

``goal_rule_violation`` is the mechanical side of the prompt's ``<goal_rule>``
(output-quality 1.10): a tab goal names what the viewer gets, never the
component or the topic.
"""

from __future__ import annotations

import logging
import re
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
    valid_components,
)
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from .assembly import STEP_FLOW_THRESHOLD
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "plan.txt"
COMPONENT_TOOLKIT_PATH = Path(__file__).parent.parent.parent / "prompts" / "component_toolkit.txt"

_MAX_HINT_CHARS = 300
_NO_TRANSCRIPT = "(no transcript — plan from the title and description)"
# The first per-video line of plan.txt; everything above it is static.
_VIDEO_LINE = "\n<video>\n"

# ─── Goal rule ──────────────────────────────────────────────────────────

# Words a goal uses when it describes the surface instead of the viewer's
# outcome ("A timeline of…", "Browsable cards…", "Drag concepts around…").
_COMPONENT_WORDS = re.compile(
    r"\b(?:timeline|canvas|checklist|grid|table|cards?|deck|flashcards|playground|"
    r"tracker|list|map|diagram|radar|filmstrip|quiz|scrubber|browsable|searchable|"
    r"browse|drag|scrub)\b",
    re.IGNORECASE,
)
# Openers that announce a topic rather than an outcome ("Key points about…").
_TOPIC_OPENER = re.compile(
    r"^(?:key points|key ideas|highlights|everything|all|notes|an? (?:overview|summary))"
    r" (?:about|on|of|from)\b",
    re.IGNORECASE,
)
_ARTICLE_OPENER = re.compile(r"^(?:a|an|the)\b", re.IGNORECASE)
# An article goal describes the surface only when a component word sits in its
# opening words ("A timeline of…", "An interactive map…"); further in, the
# word is usually a verb aimed at the viewer ("The tricks you can map to…").
_ARTICLE_SPAN_TOKENS = 3
_VIEWER = re.compile(r"\byou(?:'ll|'re|'ve|r|rself)?\b", re.IGNORECASE)


def _opens_on_component(text: str) -> bool:
    """True when the goal opens on the component rather than the viewer's outcome."""
    if _COMPONENT_WORDS.match(text):
        return True
    opening = " ".join(text.split()[:_ARTICLE_SPAN_TOKENS])
    return bool(_ARTICLE_OPENER.match(text) and _COMPONENT_WORDS.search(opening))


def goal_rule_violation(goal: str) -> str | None:
    """Why ``goal`` breaks the viewer-outcome rule, or None when it follows it.

    The rule (plan Q12, task 1.10): a goal is one line naming what the viewer
    gets from the tab — "after this you know / can …" — never the component
    ("A timeline of…") or the topic ("Key points about…"). These are surface
    reads: a goal that passes may still be weak, one that fails is wrong.
    """
    text = goal.strip()
    if not text:
        return "empty"
    if any(name in text.lower() for name in valid_components() if "_" in name):
        return "names a component"
    if _opens_on_component(text):
        return "describes the component, not what the viewer gets"
    if _TOPIC_OPENER.match(text):
        return "names the topic, not what the viewer gets"
    if not _VIEWER.search(text):
        return "does not address the viewer (no 'you')"
    return None


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


@dataclass(frozen=True)
class PlanPrompt:
    """The rendered plan prompt in its two halves.

    ``static`` is the language directive plus everything above ``<video>``
    (rules, toolkit, registry blocks, examples), rendered from config alone;
    ``video`` is the ``<video>`` block, the transcript and the closing line.
    ``static`` is empty only when the template has no ``<video>`` line.
    """

    static: str
    video: str


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
        # The step count at which assembly promotes step_player to
        # step_flow_canvas — one number for the planner and the assembler.
        .replace("{step_flow_threshold}", str(STEP_FLOW_THRESHOLD))
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


def render_plan_parts(video: PlanVideo) -> PlanPrompt:
    """The plan prompt split at ``<video>``: the static half never sees ``video``.

    Config blocks are filled in the static half only — nothing below
    ``<video>`` is a config placeholder. A template without a ``<video>`` line
    (a registry version that lost it) renders whole into ``video`` with an
    empty ``static``, and is logged.

    Raises:
        FileNotFoundError: plan.txt is missing from disk and the registry.
    """
    template = _load_plan_prompt()
    head = ENGLISH_OUTPUT_DIRECTIVE + "\n\n"
    cut = template.find(_VIDEO_LINE) + 1
    if cut == 0:
        logger.warning("plan.txt has no <video> line; sending the plan prompt uncached")
        return PlanPrompt(static="", video=head + _render_video(_render_static(template), video))
    return PlanPrompt(
        static=head + _render_static(template[:cut]),
        video=_render_video(template[cut:], video),
    )
