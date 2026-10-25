"""One group call's request — three content blocks, no system prompt (pipeline-1min 3.4, B.5).

Kfir's layout (2026-10-12), cut from today's ``build_extraction_prompt``
output, which stays unmodified:

1. ``ttl="1h"``: the rules + ``<schema>`` (every requested domain's full
   schema) + ``<extraction_example>`` (the primary's) — the same bytes for every
   group and chunk of the run. The run's rules end with the group calls'
   own (``with_group_rules``: ``extraction_group_rules.txt``, 4.0d round 2's
   completeness rule); the ``EXTRACTION_PARALLEL=false`` call never reads it;
2. 5 minutes: today's head block — ``<video>`` + the chunk's transcript +
   ``<video_memory>`` — the same bytes for every group of a chunk;
3. the group's job (``extraction_group_job.txt``, uncached): the emit-only
   line + output shape, the tabs it serves (a long video's chunk expecting its
   share of each count — output-quality 3.3 — and asking for every argument item
   in its part, the plan's windows as hints — 4.0), the primary's emphasis; the visual
   group adds the annotation guide and the chunk's annotations and key frames;
   the group extracting ``learning.keyIdeas`` adds the rules file's argument
   example of the run's primary domain (p1.11).

The 1-hour breakpoint precedes the 5-minute one (Anthropic rejects the other
order): two breakpoints of the four allowed. The schema and example are cut
from the UNBOUND tail — schemas and example are filled at template time, and no
on-screen text (late-bound) can sit between the tags there.

``extraction_group_rules.txt`` has two shares: its completeness rule (block 1,
every group of every run) and its ``<argument_example field=… primary=…>``
elements (block 3). An example is the one worked item of a talk (S01) or a
conversation (S02); it rides at the end of the ruled prompt's tail (one read
of the file per run — R40 B1) and ``job_block`` keeps it only where it
teaches — the group whose ``fields`` request the example's ``field``, in a run
whose ``GroupJob.primary_tag`` is the example's ``primary``. That is the
selection the builder can make: it knows the group's paths and the run's
domains, not the plan's content format (a talk or lecture is primary
``learning``; an interview is primary ``podcast``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

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
from .where_hints import PartWindow, part_window

logger = logging.getLogger(__name__)

JOB_TEMPLATE_PATH = PROMPTS_DIR / "extraction_group_job.txt"
GROUP_RULES_PATH = PROMPTS_DIR / "extraction_group_rules.txt"

# Block 1 lives an hour: same-domain-set runs within it read the rules + schemas.
PREFIX_TTL: CacheTTL = "1h"

_SCHEMA_TAG = "schema"
_RULES_CLOSE = "\n</quality_rules>"
_EXAMPLE_TAG = "extraction_example"
# A domain whose schema text does not parse gets its keys named without a shape.
_UNSLICED_LEAD = "Emit ONLY these keys (anything else is discarded): "
_UNSLICED_MORE = "Also emit (shapes as in <schema>): "
_LABEL_MAX_CHARS = 80
# One worked example per (field, primary domain) in extraction_group_rules.txt;
# the attributes come in this order, the body may span lines.
_ARGUMENT_EXAMPLE_RE = re.compile(
    r'<argument_example field="(?P<field>[^"]+)" primary="(?P<primary>[^"]+)">'
    r".*?</argument_example>\n*",
    re.DOTALL,
)


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
    content_format: str | None = None  # the probe's: a talk's asks line names the key ideas


# ─── Block 1 + 2 ───


def _element(text: str, tag: str) -> str:
    """The whole ``<tag …>…</tag>`` element of ``text`` ("" when it has none)."""
    start = text.find(f"<{tag}")
    end = text.rfind(f"</{tag}>")
    if start == -1 or end < start:
        return ""
    return text[start : end + len(tag) + 3]


def completeness_rule(rules: str) -> str:
    """Block 1's share of the group rules: the text without its argument examples."""
    return _ARGUMENT_EXAMPLE_RE.sub("", rules).strip()


def argument_examples(rules: str) -> str:
    """Block 3's share: the rules' ``<argument_example>`` elements, one per paragraph."""
    return "\n\n".join(m.group(0).strip() for m in _ARGUMENT_EXAMPLE_RE.finditer(rules))


def with_group_rules(prompt: ExtractionPrompt) -> ExtractionPrompt:
    """``prompt`` whose rules end with the group calls' own and whose tail ends
    with the rules' argument examples — the file read once per run.

    Where 4.0d round 2 measured the completeness rule: ``<quality_rules>``'s
    last paragraph, after a blank line (p1.11 appends the argument rule to
    it; the examples stay out of block 1 — ``build_group_messages`` hands
    them from the tail to the job block). Without exactly one
    ``</quality_rules>`` the rule has no measured place: logged, and the
    run keeps today's rules.
    """
    text = load_prompt_text(GROUP_RULES_PATH)
    rules, examples = completeness_rule(text), argument_examples(text)
    if not rules:
        return prompt
    system = prompt.system
    if system.count(_RULES_CLOSE) != 1:
        logger.warning("extraction rules have no single </quality_rules>; group rules not added")
        return prompt
    at = system.index(_RULES_CLOSE)
    tail = f"{prompt.tail.rstrip()}\n\n{examples}\n" if examples else prompt.tail
    return replace(prompt, system=f"{system[:at]}\n\n{rules}{system[at:]}", tail=tail)


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


def tabs_to_serve(
    group: ExtractionGroup, job: GroupJob, share: float = 1.0, part: PartWindow | None = None
) -> str:
    """Full brief lines (what/where/expect/cap) for the tabs whose own field the group
    extracts; a bare ``supports:`` line, no count, for tabs it only backs up. ``share`` =
    the chunk's share of the video (``ExtractionChunk.share``): its part of each count;
    ``part`` = the chunk's window, inside which an argument list's windows are hints."""
    tabs = _group_tabs(group, job.tabs)
    owned = [tab for tab in tabs if _owns(group, tab)]
    lines = (
        [render_tabs_to_serve(owned, job.active, share, part, job.content_format)] if owned else []
    )
    lines += [_support_line(tab) for tab in tabs if not _owns(group, tab)]
    return "\n".join(lines) or render_tabs_to_serve([], job.active)


def load_job_template() -> str:
    """The group-job template, registry first — read once per run (``SplitRun``)."""
    return load_prompt_text(JOB_TEMPLATE_PATH)


def _taught_examples(examples: str, group: ExtractionGroup, job: GroupJob) -> str:
    """The examples that teach this group: its field requested, its primary the run's."""
    paths = {f.path for f in group.fields}

    def keep(match: re.Match[str]) -> str:
        applies = match["field"] in paths and match["primary"] == job.primary_tag
        return match.group(0) if applies else ""

    return _ARGUMENT_EXAMPLE_RE.sub(keep, examples).strip()


def job_block(
    group: ExtractionGroup,
    chunk: ExtractionChunk,
    job: GroupJob,
    template: str | None = None,
    examples: str = "",
) -> TextBlock:
    """Block 3: the group's job; only the visual group carries frame text, only
    the group extracting an argument example's field in a run of its primary
    domain carries that example, as the block's last element.

    ``template`` = the run's job template (None → read it now); ``examples`` =
    the run's argument examples (``argument_examples``), none by default.
    """
    values = {
        "batch_context": chunk.batch_context,
        "emit_only": emit_only(group),
        "tabs_to_serve": tabs_to_serve(
            group, job, chunk.share, part_window(chunk.start_s, chunk.end_s)
        ),
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
    raw = template if template is not None else load_job_template()
    text = _fill(_drop_absent_visual_blocks(raw, values), values).rstrip()
    taught = _taught_examples(examples, group, job)
    return text_block(f"{text}\n\n{taught}\n" if taught else f"{text}\n")


def build_group_messages(
    prompt: ExtractionPrompt,
    group: ExtractionGroup,
    chunk: ExtractionChunk,
    job: GroupJob,
    template: str | None = None,
) -> list[TextBlock]:
    """The user content of one group call: block 1 (1h), block 2 (5m), the job
    (with the argument example the ruled prompt's tail carries for this group)."""
    return [
        prefix_block(prompt),
        head_block(prompt, chunk),
        job_block(group, chunk, job, template, argument_examples(prompt.tail)),
    ]
