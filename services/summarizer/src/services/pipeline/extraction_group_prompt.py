"""One group call's request — three content blocks, no system prompt (pipeline-1min 3.4, B.5).

Kfir's layout (2026-10-12), cut from today's ``build_extraction_prompt``
output, which stays unmodified:

1. ``ttl="1h"``: the rules + ``<schema>`` (every requested domain's full
   schema) + ``<extraction_example>`` (the primary's) — the same bytes for every
   group and chunk of the run;
2. 5 minutes: today's head block — ``<video>`` + the chunk's transcript +
   ``<video_memory>`` — the same bytes for every group of a chunk;
3. the group's job (``extraction_group_job.txt``, uncached): the emit-only
   line + output shape, the tabs it serves, the primary's emphasis; the visual
   group adds the annotation guide and the chunk's annotations and key frames.

The 1-hour breakpoint precedes the 5-minute one (Anthropic rejects the other
order): two breakpoints of the four allowed. The schema and example are cut
from the UNBOUND tail — schemas and example are filled at template time, and no
on-screen text (late-bound) can sit between the tags there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..llm_messages import CacheTTL, TextBlock, text_block
from .extraction_chunks import ExtractionChunk, chunk_annotations
from .extraction_prompt import (
    ExtractionPrompt,
    _drop_absent_visual_blocks,
    _fill,
    get_content_emphasis,
    render_tabs_to_serve,
)
from .field_groups import ExtractionGroup
from .field_requests import FieldRequest
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import PROMPTS_DIR, load_prompt_text
from .schema_fields import SchemaParseError, render_emit_only, slice_skeleton

JOB_TEMPLATE_PATH = PROMPTS_DIR / "extraction_group_job.txt"

# Block 1 lives an hour: same-domain-set runs within it read the rules + schemas.
PREFIX_TTL: CacheTTL = "1h"

_SCHEMA_TAG = "schema"
_EXAMPLE_TAG = "extraction_example"
# A domain whose schema text does not parse gets its keys named without a shape.
_UNSLICED_LEAD = "Emit ONLY these keys (anything else is discarded): "
_UNSLICED_MORE = "Also emit (shapes as in <schema>): "
_LABEL_MAX_CHARS = 80


@dataclass(frozen=True)
class GroupJob:
    """What a group's job block reads besides the group: the tabs it was cut from,
    the run's domains, and — the visual group only — the frame inputs.

    ``frame_context`` = the whole video's key frames; a long video's chunk listed
    in ``chunk_frame_context`` reads its own window's instead.
    """

    tabs: Sequence[Mapping[str, object]]
    active: Sequence[str]  # content tags + modifiers
    primary_tag: str
    visual_annotations: str = ""
    frame_context: str = ""
    chunk_frame_context: Mapping[int, str] = field(default_factory=dict)


# ─── Block 1 + 2 ───


def _element(text: str, tag: str) -> str:
    """The whole ``<tag …>…</tag>`` element of ``text`` ("" when it has none)."""
    start = text.find(f"<{tag}")
    end = text.rfind(f"</{tag}>")
    if start == -1 or end < start:
        return ""
    return text[start : end + len(tag) + 3]


def prefix_block(prompt: ExtractionPrompt) -> TextBlock:
    """Block 1: rules + full schemas + the primary's example, cached for an hour.

    Raises ``ValueError`` when the template lost its sections or its
    ``<schema>`` element — a group call cannot extract without them.
    """
    schema = _element(prompt.tail, _SCHEMA_TAG)
    if not prompt.system or not schema:
        raise ValueError("extraction template has no rules section or <schema> element")
    parts = [prompt.system, schema, _element(prompt.tail, _EXAMPLE_TAG)]
    return text_block("\n\n".join(p for p in parts if p), cache=True, ttl=PREFIX_TTL)


def head_block(prompt: ExtractionPrompt, chunk: ExtractionChunk) -> TextBlock:
    """Block 2: today's head block for the chunk's transcript (5-minute breakpoint)."""
    if not prompt.head:
        raise ValueError("extraction template has no <video> section")
    return prompt.user_blocks(chunk.transcript)[0]


# ─── Block 3 ───


def _by_domain(fields: Sequence[FieldRequest]) -> dict[str, list[FieldRequest]]:
    grouped: dict[str, list[FieldRequest]] = {}
    for request in fields:
        grouped.setdefault(request.domain, []).append(request)
    return grouped


def _plain_label(request: FieldRequest) -> str:
    capped = request.cap is not None and request.kind == "list"
    return f"{request.path} (cap {request.cap})" if capped else request.path


def _holds(tree: Mapping[str, object], field: str) -> bool:
    """Whether a key tree has the (dotted) key path."""
    node: object = tree
    for key in field.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return False
        node = node[key]
    return True


def _slice(domain: str, fields: Sequence[FieldRequest]) -> tuple[dict, list[FieldRequest]]:
    """The domain's key tree for ``fields`` and the fields it cannot shape.

    Every field when the schema text does not parse (``SchemaParseError``);
    else the ones the schema lacks (an unknown domain has an empty schema).
    """
    try:
        tree = slice_skeleton(domain, [f.field for f in fields])
    except SchemaParseError:
        return {}, list(fields)
    return tree, [f for f in fields if not _holds(tree, f.field)]


def emit_only(group: ExtractionGroup) -> str:
    """The emit-only line + output shape for the group's keys — never empty for a group.

    Keys no schema can shape are named on a plain line instead.
    """
    slices: dict[str, dict[str, object]] = {}
    caps: dict[str, int | None] = {}
    unsliced: list[FieldRequest] = []
    for domain, fields in _by_domain(group.fields).items():
        tree, missing = _slice(domain, fields)
        unsliced.extend(missing)
        if len(missing) < len(fields):
            slices[domain] = tree
            caps.update({f.path: f.cap for f in fields if f not in missing})
    shaped = render_emit_only(slices, caps) if slices else ""
    if not unsliced:
        return shaped
    lead = _UNSLICED_MORE if shaped else _UNSLICED_LEAD
    plain = lead + ", ".join(_plain_label(f) for f in unsliced)
    return f"{shaped}\n{plain}" if shaped else plain


def _group_tabs(
    group: ExtractionGroup, tabs: Sequence[Mapping[str, object]]
) -> list[Mapping[str, object]]:
    return [tab for tab in tabs if str(tab.get("id") or "") in group.tab_ids]


def _owns(group: ExtractionGroup, tab: Mapping[str, object]) -> bool:
    """The group extracts the tab's own (planned) field, not only a fallback or side read."""
    tab_id = str(tab.get("id") or "")
    return any(f.reason == "planned" and tab_id in f.tab_ids for f in group.fields)


def _support_line(tab: Mapping[str, object]) -> str:
    label = str(tab.get("label") or tab.get("id") or "Tab")
    clean = " ".join(sanitize_for_prompt(label, max_len=_LABEL_MAX_CHARS).split())
    return f"- supports: {clean} ({tab.get('component', '')})"


def tabs_to_serve(group: ExtractionGroup, job: GroupJob) -> str:
    """Full brief lines (what/where/expect/cap) for the tabs whose own field the group
    extracts; a bare ``supports:`` line, no count, for tabs it only backs up."""
    tabs = _group_tabs(group, job.tabs)
    owned = [tab for tab in tabs if _owns(group, tab)]
    lines = [render_tabs_to_serve(owned, job.active)] if owned else []
    lines += [_support_line(tab) for tab in tabs if not _owns(group, tab)]
    return "\n".join(lines) or render_tabs_to_serve([], job.active)


def job_block(group: ExtractionGroup, chunk: ExtractionChunk, job: GroupJob) -> TextBlock:
    """Block 3: the group's job; only the visual group carries frame text."""
    values = {
        "batch_context": chunk.batch_context,
        "emit_only": emit_only(group),
        "tabs_to_serve": tabs_to_serve(group, job),
        "content_emphasis": get_content_emphasis(job.primary_tag),
        "visual_annotations": (
            chunk_annotations(job.visual_annotations, chunk) if group.visual else ""
        ),
        "frame_context": (
            job.chunk_frame_context.get(chunk.index, job.frame_context) if group.visual else ""
        ),
    }
    # Today's binding: the visual elements leave with empty inputs, and the
    # single pass never re-reads inserted text (on-screen code) for placeholders.
    template = _drop_absent_visual_blocks(load_prompt_text(JOB_TEMPLATE_PATH), values)
    return text_block(_fill(template, values).rstrip() + "\n")


def build_group_messages(
    prompt: ExtractionPrompt, group: ExtractionGroup, chunk: ExtractionChunk, job: GroupJob
) -> list[TextBlock]:
    """The user content of one group call: block 1 (1h), block 2 (5m), the job."""
    return [prefix_block(prompt), head_block(prompt, chunk), job_block(group, chunk, job)]
