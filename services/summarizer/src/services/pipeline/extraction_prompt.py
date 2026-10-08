"""Extraction prompt rendering — ``base_extraction.txt`` + schemas + example + briefs (pipeline-1min B.5).

The template has three sections: the rules (no per-video text), the video —
title, length, ``{transcript}``, ``<video_memory>`` — and the job (what to emit,
the planned tabs' briefs and caps, schemas, example, visual annotations, key
frames). Counts come from the plan's briefs and the registry caps, never from
the video's duration.

``{transcript}`` and ``{batch_context}`` stay in the rendered template: the
extractor binds them per call (single call or one per chunked batch).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ...models.pipeline_types import TabBrief
from ...shared_config.domain_config import NON_EXTRACTION_DATASOURCES, data_source
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ..transcript.render import format_marker
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import PROMPTS_DIR, load_prompt_text

logger = logging.getLogger(__name__)

TEMPLATE_PATH = PROMPTS_DIR / "base_extraction.txt"
SCHEMAS_DIR = PROMPTS_DIR / "schemas"
EXAMPLES_DIR = PROMPTS_DIR / "examples"

# Bound per call by the extractor, never at template-build time.
LATE_BOUND_PLACEHOLDERS = frozenset({"transcript", "batch_context"})

# Allowed schema/example names — alphanumeric + underscore only (no path traversal)
_SAFE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

_LABEL_MAX_CHARS = 80
_BRIEF_MAX_CHARS = 240
_NO_TABS = "No tabs planned — extract the fields the video actually covers."
_DEFAULT_EMPHASIS = "PRIORITY: Exact names, numbers and terms as the speaker says them."
_EMPHASIS = {
    "food": "PRIORITY: Every ingredient with exact measurement. Steps in order. Temps with units.",
    "tech": "PRIORITY: Every code snippet exactly. Variable names preserved. Commands reproducible.",
    "travel": "PRIORITY: Every location map-searchable. Costs with currency. Transport specific.",
    "fitness": "PRIORITY: Every exercise with sets/reps/rest. Form cues verbatim. Modifications included.",
    "review": "PRIORITY: All pros AND cons. Specs with numbers. Verdict unmodified.",
    "learning": "PRIORITY: All concepts with definitions. Examples preserved. Progression maintained.",
    "music": "PRIORITY: Lyrics exact or omitted. Credits complete. Genre specific.",
    "project": "PRIORITY: All materials with specs. Steps in order. Safety verbatim.",
}


@dataclass(frozen=True)
class ExtractionPromptInput:
    """Everything one run's extraction prompt is rendered from.

    ``primary_tag`` (the plan's; default: the first content tag) picks the
    example and the priority line. ``tabs`` are the plan's tab dicts (``label``, ``component``, ``dataSource``,
    ``goal``, ``brief``); ``video_memory`` and ``visual_annotations`` are the
    rendered ``<video_memory>`` / ``<visual_annotations>`` blocks ("" = absent).
    """

    content_tags: Sequence[str]
    modifiers: Sequence[str]
    quality_rules: str
    primary_tag: str = ""
    title: str = ""
    duration_seconds: float = 0
    tabs: Sequence[Mapping[str, object]] = ()
    video_memory: str = ""
    frame_context: str = ""
    visual_annotations: str = ""


# ─── Files ───


def _load_schema(name: str) -> str:
    """A domain or modifier schema file; ``""`` for an unsafe or unknown name."""
    if not _SAFE_NAME_RE.match(name):
        logger.warning("Rejected unsafe schema name: %r", name)
        return ""
    schema_path = SCHEMAS_DIR / f"{name}.txt"
    if not schema_path.exists():
        logger.warning("Schema file not found: %s", schema_path)
        return ""
    return load_prompt_text(schema_path)


def _load_domain_example(tag: str) -> str:
    """The domain's example file; ``""`` when the domain has none.

    No cross-domain fallback: another domain's example teaches the wrong
    fields and item shapes (learning's keyPoints for a gaming video), so a
    domain without an example gets no example block at all.
    """
    if not _SAFE_NAME_RE.match(tag):
        logger.warning("Rejected unsafe example tag: %r", tag)
        return ""
    example_path = EXAMPLES_DIR / f"{tag}.txt"
    if not example_path.exists():
        logger.info("No example for domain %r — prompt gets no example block", tag)
        return ""
    return load_prompt_text(example_path)


def get_content_emphasis(primary_tag: str) -> str:
    """The primary domain's one-line priority instruction."""
    return _EMPHASIS.get(primary_tag, _DEFAULT_EMPHASIS)


# ─── Blocks ───


def _domain_schemas(content_tags: Sequence[str], modifiers: Sequence[str]) -> str:
    parts = [
        f"--- {name.upper()} {kind} ---\n{schema}"
        for names, kind in ((content_tags, "DOMAIN"), (modifiers, "MODIFIER"))
        for name in names
        if (schema := _load_schema(name))
    ]
    return "\n\n".join(parts) if parts else "Use general-purpose extraction."


def _emit_domains(content_tags: Sequence[str], modifiers: Sequence[str]) -> str:
    return ", ".join([*content_tags, *modifiers]) or "the fields the video covers"


def _format_duration(seconds: float) -> str:
    return format_marker(seconds)[1:-1] if seconds > 0 else "unknown"


def _clean(text: str, max_chars: int) -> str:
    """One prompt-safe line from plan (LLM) text: no braces/tags, capped."""
    return " ".join(sanitize_for_prompt(text, max_len=max_chars).split())


def _data_source(tab: Mapping[str, object]) -> str:
    value = tab.get("dataSource")
    return value if isinstance(value, str) else ""


def _serves_extraction(tab: Mapping[str, object]) -> bool:
    """Tabs that read extraction fields — not the overview, not the frames filmstrip."""
    source = _data_source(tab)
    return (
        bool(source)
        and source not in NON_EXTRACTION_DATASOURCES
        and tab.get("component") != "overview"
    )


def _amount(expect: int, source: str) -> str:
    """``expect ~14, cap 30`` — the plan's count and the registry's cap, as known."""
    spec = data_source(source)
    cap = ""
    if spec is not None:
        cap = "one object" if spec["kind"] == "object" else f"cap {spec['cap']}"
    expected = f"expect ~{expect}" if expect else ""
    return ", ".join(part for part in (expected, cap) if part)


def _tab_line(tab: Mapping[str, object]) -> str:
    brief = TabBrief.from_raw(tab.get("brief"))
    source = _data_source(tab)
    label = _clean(str(tab.get("label") or tab.get("id") or "Tab"), _LABEL_MAX_CHARS)
    parts = [f"- {label} — {tab.get('component', '')} ← {source}"]
    if brief.what:
        parts.append(f"what: {_clean(brief.what, _BRIEF_MAX_CHARS)}")
    elif isinstance(goal := tab.get("goal"), str) and goal:
        parts.append(f"goal: {_clean(goal, _BRIEF_MAX_CHARS)}")
    if brief.where:
        parts.append("where " + ", ".join(r.replace("-", "–") for r in brief.where))
    if amount := _amount(brief.expect, source):
        parts.append(amount)
    return " — ".join(parts)


def render_tabs_to_serve(tabs: Sequence[Mapping[str, object]]) -> str:
    """One line per planned tab that reads extraction data: brief + count + cap."""
    lines = [_tab_line(tab) for tab in tabs if _serves_extraction(tab)]
    return "\n".join(lines) or _NO_TABS


# ─── Template ───


def drop_element(text: str, tag: str) -> str:
    """Remove the whole ``<tag …>…</tag>`` element (and the blank lines after it)."""
    return re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>\n*", "", text, flags=re.DOTALL)


def _drop_absent_blocks(template: str, inp: ExtractionPromptInput, example: str) -> str:
    """Optional elements leave the template whole when their content is empty."""
    if not example:
        template = drop_element(template, "extraction_example")
    if not inp.visual_annotations:
        template = drop_element(template, "visual_context_guide")
        template = template.replace("{visual_annotations}\n\n", "")
    if not inp.frame_context:
        template = drop_element(template, "key_frames")
    if not inp.video_memory:
        template = template.replace("{video_memory}\n\n", "")
    return template


def _fill(template: str, values: Mapping[str, str]) -> str:
    """Single pass: inserted text (OCR code, schemas) is never re-scanned for placeholders."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def build_extraction_template(inp: ExtractionPromptInput) -> str:
    """The run's extraction prompt with ``{transcript}`` / ``{batch_context}`` left to bind."""
    primary_tag = inp.primary_tag or (inp.content_tags[0] if inp.content_tags else "learning")
    example = _load_domain_example(primary_tag)
    template = _drop_absent_blocks(load_prompt_text(TEMPLATE_PATH), inp, example)
    values = {
        "quality_rules": inp.quality_rules,
        "title": sanitize_for_prompt(inp.title),
        "duration": _format_duration(inp.duration_seconds),
        "video_memory": inp.video_memory,
        "emit_domains": _emit_domains(inp.content_tags, inp.modifiers),
        "tabs_to_serve": render_tabs_to_serve(inp.tabs),
        "content_emphasis": get_content_emphasis(primary_tag),
        "domain_schemas": _domain_schemas(inp.content_tags, inp.modifiers),
        "primary_tag": primary_tag,
        "domain_example": example,
        "visual_annotations": inp.visual_annotations,
        "frame_context": inp.frame_context,
    }
    return f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{_fill(template, values)}"
