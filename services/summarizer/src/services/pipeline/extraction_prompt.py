"""Extraction prompt rendering — ``base_extraction.txt`` + schemas + example + briefs (pipeline-1min B.5).

The template has three sections: the rules (no per-video text), the video —
title, length, ``{transcript}``, ``<video_memory>`` — and the job (what to emit,
the planned tabs' briefs and caps, schemas, example, visual annotations, key
frames). Counts come from the plan's briefs and the registry caps, never from
the video's duration.

Cache layout (A26): the rules go out as the system prompt — byte-identical for
every video — and the user message is two blocks: ``[video + transcript +
video_memory]`` carrying the one cache breakpoint, then the job. Everything a
call varies (batch transcript, batch context, the batch's annotation slice) and
all raw frame text (key-frame captions + OCR) is bound per call, in one pass,
so on-screen text is never read as a placeholder. A call whose annotation slice
or key frames are empty loses the matching guide/element too.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from ...models.pipeline_types import TabBrief
from ...shared_config.domain_config import NON_EXTRACTION_DATASOURCES, DataSourceSpec, data_source
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ..llm_messages import TextBlock, text_block
from ..transcript.render import format_marker
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import PROMPTS_DIR, load_prompt_text
from .visual_annotations import CLOSE_TAG, OPEN_TAG, annotation_entries

logger = logging.getLogger(__name__)

TEMPLATE_PATH = PROMPTS_DIR / "base_extraction.txt"
SCHEMAS_DIR = PROMPTS_DIR / "schemas"
EXAMPLES_DIR = PROMPTS_DIR / "examples"

# Bound per call by the extractor, in one pass, never at template-build time.
LATE_BOUND_PLACEHOLDERS = frozenset(
    {"transcript", "batch_context", "visual_annotations", "frame_context"}
)

# Section openers, each on its own line: rules end before <video>, the job starts at <your_job>.
_VIDEO_LINE = "\n<video>\n"
_JOB_LINE = "\n<your_job>\n"

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


def _source_domain(source: str) -> str:
    """The domain (or modifier) a dataSource reads: ``food.ingredients`` → ``food``."""
    spec = data_source(source)
    return spec["domain"] if spec is not None else source.split(".", 1)[0]


def _serves_extraction(tab: Mapping[str, object], emitted: Collection[str]) -> bool:
    """Tabs that read a field this extraction emits — not the overview, not the
    frames filmstrip, not another stage's data (the quiz tab reads enrichment)."""
    source = _data_source(tab)
    return (
        bool(source)
        and source not in NON_EXTRACTION_DATASOURCES
        and tab.get("component") != "overview"
        and _source_domain(source) in emitted
    )


def _amount(expect: int, spec: DataSourceSpec | None) -> str:
    """``expect ~14, cap 30`` — the plan's count (never above the cap) and the
    registry's cap; an object field is ``one object``, with no count."""
    if spec is not None and spec["kind"] == "object":
        return "one object"
    if spec is None:
        return f"expect ~{expect}" if expect else ""
    expected = f"expect ~{min(expect, spec['cap'])}, " if expect else ""
    return f"{expected}cap {spec['cap']}"


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
    if amount := _amount(brief.expect, data_source(source)):
        parts.append(amount)
    return " — ".join(parts)


def render_tabs_to_serve(tabs: Sequence[Mapping[str, object]], emitted: Collection[str]) -> str:
    """One line per planned tab that reads a field of an ``emitted`` domain or
    modifier: brief + count + cap."""
    lines = [_tab_line(tab) for tab in tabs if _serves_extraction(tab, emitted)]
    return "\n".join(lines) or _NO_TABS


# ─── Template ───


def drop_element(text: str, tag: str) -> str:
    """Remove the whole ``<tag …>…</tag>`` element (and the blank lines after it)."""
    return re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>\n*", "", text, flags=re.DOTALL)


def _drop_absent_blocks(template: str, inp: ExtractionPromptInput, example: str) -> str:
    """Run-level optional elements leave the template whole when their content is empty."""
    if not example:
        template = drop_element(template, "extraction_example")
    if not inp.video_memory:
        template = template.replace("{video_memory}\n\n", "")
    return template


def _drop_absent_visual_blocks(tail: str, values: Mapping[str, str]) -> str:
    """Per call: no annotation slice → no visual guide; no key frames → no element."""
    if not values["visual_annotations"]:
        tail = drop_element(tail, "visual_context_guide")
        tail = tail.replace("{visual_annotations}\n\n", "")
    if not values["frame_context"]:
        tail = drop_element(tail, "key_frames")
    return tail


def _fill(template: str, values: Mapping[str, str]) -> str:
    """Single pass: inserted text (OCR code, schemas) is never re-scanned for placeholders."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def build_extraction_template(inp: ExtractionPromptInput) -> str:
    """The run's extraction prompt with ``LATE_BOUND_PLACEHOLDERS`` left to bind."""
    primary_tag = inp.primary_tag or (inp.content_tags[0] if inp.content_tags else "learning")
    example = _load_domain_example(primary_tag)
    template = _drop_absent_blocks(load_prompt_text(TEMPLATE_PATH), inp, example)
    values = {
        "quality_rules": inp.quality_rules,
        "title": sanitize_for_prompt(inp.title),
        "duration": _format_duration(inp.duration_seconds),
        "video_memory": inp.video_memory,
        "emit_domains": _emit_domains(inp.content_tags, inp.modifiers),
        "tabs_to_serve": render_tabs_to_serve(inp.tabs, {*inp.content_tags, *inp.modifiers}),
        "content_emphasis": get_content_emphasis(primary_tag),
        "domain_schemas": _domain_schemas(inp.content_tags, inp.modifiers),
        "primary_tag": primary_tag,
        "domain_example": example,
    }
    return f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{_fill(template, values)}"


# ─── Sections + per-call binding ───


@dataclass(frozen=True)
class ExtractionPrompt:
    """One run's extraction prompt in its cache sections.

    ``system`` = the rules (no per-video text); ``head`` = ``<video>`` +
    ``{transcript}`` + ``<video_memory>``, the cached prefix; ``tail`` = the job
    with ``{batch_context}`` / ``{visual_annotations}`` / ``{frame_context}``
    open. An empty ``head`` means the template lost its section lines:
    everything is one uncached block.
    """

    system: str
    head: str
    tail: str
    visual_annotations: str = ""
    frame_context: str = ""

    def user_blocks(
        self,
        transcript: str,
        batch_context: str = "",
        visual_annotations: str | None = None,
    ) -> list[TextBlock]:
        """The user content for one call; the breakpoint ends the head block."""
        values = {
            "transcript": transcript,
            "batch_context": batch_context,
            "visual_annotations": (
                self.visual_annotations if visual_annotations is None else visual_annotations
            ),
            "frame_context": self.frame_context,
        }
        tail = text_block(_fill(_drop_absent_visual_blocks(self.tail, values), values))
        if not self.head:
            return [tail]
        return [text_block(_fill(self.head, values), cache=True), tail]


def _split_sections(text: str) -> tuple[str, str, str]:
    video_at = text.find(_VIDEO_LINE)
    job_at = text.find(_JOB_LINE)
    if video_at == -1 or job_at < video_at:
        logger.warning(
            "Extraction template has no <video>/<your_job> section lines — "
            "sending it as one uncached user block"
        )
        return "", "", text
    return text[:video_at].rstrip(), text[video_at + 1 : job_at].rstrip(), text[job_at + 1 :]


def build_extraction_prompt(inp: ExtractionPromptInput) -> ExtractionPrompt:
    """The run's prompt, split into system rules / cached head / job tail."""
    system, head, tail = _split_sections(build_extraction_template(inp))
    return ExtractionPrompt(system, head, tail, inp.visual_annotations, inp.frame_context)


def slice_visual_annotations(
    block: str, start_seconds: float | None, end_seconds: float | None
) -> str:
    """The entries of a ``<visual_annotations>`` block with ``start <= t < end`` ("" if none).

    ``None`` leaves that side open — the first and last chunked batches keep the
    frames before the first chapter boundary and at the video's very end.
    """
    entries = [
        entry.text
        for entry in annotation_entries(block)
        if (start_seconds is None or entry.seconds >= start_seconds)
        and (end_seconds is None or entry.seconds < end_seconds)
    ]
    if not entries:
        return ""
    return "\n".join([OPEN_TAG, *entries, CLOSE_TAG])
