"""The hero rewrite's request — what the one call reads (output-quality 2.1, D1).

The request reads the split extraction's cache — caches are per model, so the
call runs on the groups' model, with their JSON mode, effort and no system prompt:

1. block 1 (1 h) = ``extraction_group_prompt.prefix_block`` of the run's ruled
   prompt — the rules + schemas every group call sent, read at 0.1×;
2. block 2 (5 min) = ``head_block`` — ``<video>`` + the transcript +
   ``<video_memory>`` — only when the run is ONE chunk: a chunked (long) run has a
   head per chunk and one request can read one prefix; reading the first chunk's
   would show the model the first half verbatim and the second half only as
   extraction (S01's brief lost the second half once) for +$0.002, so a chunked
   run reads block 1 alone and block 3 carries ``<video>`` + ``<video_memory>``;
3. the hero job (uncached): ``prompts/hero_rewrite.txt`` + the compacted merged
   extraction (``hero_compact``, ≤ 5 K tokens, stamped items under ids; a chunked
   run's scalars part by part, 3.2, in ≤ 5.5 K) + the tab labels.

Without group blocks (``EXTRACTION_PARALLEL=false``) the request is block 3
alone: no group call wrote blocks 1 / 2, so sending them would WRITE them (block
1 at 2× for an hour it never serves) for rules the hero does not need.

A takeaway takes the stamp of the id it cites; with the run's quote index (2.0) a
quoted item's stamp is first moved to where its quote is said (``stamps_at_quotes``),
and a quote the transcript never says is out of ``<extracted>`` and the evidence.
Only a call that reads the transcript (block 2) is offered a typed ``m:ss``; any
other is told to always cite an id — ``hero_validate`` holds a typed stamp there to
a clock the call read (review p2 B3).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ..llm_messages import TextBlock
from .assembly.tab_references import bare_label
from .extraction_group_prompt import head_block, prefix_block
from .hero_compact import ChunkScalars, CompactExtraction, stamps_at_quotes
from .hero_validate import HeroEvidence, clock_seconds
from .memory import format_clock, takeaway_count
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import PROMPTS_DIR, load_prompt_text
from .quote_verify import QuoteIndex
from .synthesis import format_tab_labels

if TYPE_CHECKING:
    from .extraction_chunks import ExtractionChunk
    from .extraction_prompt import ExtractionPrompt

logger = logging.getLogger(__name__)

PROMPT_PATH = PROMPTS_DIR / "hero_rewrite.txt"

_NO_VIDEO_MEMORY = "<video_memory>\nNot available\n</video_memory>"
_NOTHING_EXTRACTED = "(nothing was extracted)"
# How a takeaway is stamped: a typed m:ss only where the transcript is above (D13).
_STAMP_RULE_TRANSCRIPT = (
    'Only when no stamped item carries the claim, write "timestamp": "m:ss" — the '
    'transcript marker where it is said — instead of "ref".'
)
_STAMP_RULE_NO_TRANSCRIPT = (
    'Always cite a "ref": no transcript is above, so a stamp you type is a guess — '
    "leave out a claim no stamped item carries."
)


@dataclass(frozen=True)
class GroupBlocks:
    """The split run's cached prefix: its ruled prompt (``with_group_rules``) and chunks."""

    prompt: ExtractionPrompt
    chunks: Sequence[ExtractionChunk]


@dataclass(frozen=True)
class HeroRequest:
    """Everything one hero call is built from; ``blocks`` None = no group blocks to read;
    ``quote_index`` None = the takeaways keep the items' own stamps; ``chunk_scalars`` = a
    chunked run's scalars part by part (``group_merge.chunk_scalars``; empty = the merge's)."""

    title: str
    duration: int | None
    extraction: Mapping[str, Any]
    tab_labels: Sequence[str] = ()
    video_memory: str = ""
    blocks: GroupBlocks | None = None
    quote_index: QuoteIndex | None = None
    chunk_scalars: ChunkScalars = field(default_factory=dict)

    @property
    def reads_transcript(self) -> bool:
        """Block 2 is read: the split run is one chunk, so its head is the whole video."""
        return self.blocks is not None and len(self.blocks.chunks) == 1


# ─── Block 3 ───


def cached_blocks(request: HeroRequest) -> list[TextBlock]:
    """Blocks 1 (+ 2), byte-identical to the group calls'; ``[]`` without group blocks."""
    if request.blocks is None:
        return []
    prompt, chunks = request.blocks.prompt, request.blocks.chunks
    try:
        blocks = [prefix_block(prompt)]
        if request.reads_transcript:
            blocks.append(head_block(prompt, chunks[0]))
    except ValueError:
        logger.warning("Hero rewrite: the extraction template lost its sections — job only")
        return []
    return blocks


def _length(duration: int | None) -> str:
    return format_clock(duration) if duration else "unknown-length"


def _video_section(request: HeroRequest) -> str:
    """``<video>`` + ``<video_memory>`` for block 3 — "" when block 2 carries them."""
    if request.reads_transcript:
        return ""
    title = sanitize_for_prompt(request.title, max_len=200)
    video = f"<video>\nTitle: {title}\nLength: {_length(request.duration)}\n</video>"
    return f"{video}\n\n{request.video_memory or _NO_VIDEO_MEMORY}\n\n"


def _stamp_rule(request: HeroRequest) -> str:
    return _STAMP_RULE_TRANSCRIPT if request.reads_transcript else _STAMP_RULE_NO_TRANSCRIPT


def _problems_section(problems: Sequence[str]) -> str:
    lines = "\n".join(f"- {problem}" for problem in problems)
    return (
        "\n<previous_answer_problems>\n"
        "Your previous answer failed these checks. Answer again in full, fixing each one:\n"
        f"{lines}\n</previous_answer_problems>\n"
    )


def job_text(request: HeroRequest, compact: CompactExtraction, problems: Sequence[str] = ()) -> str:
    """Block 3: the hero job over this video (English output line first, as every writer)."""
    labels = [bare_label(label) for label in request.tab_labels]
    body = (
        load_prompt_text(PROMPT_PATH)
        .replace("{takeaway_count}", str(takeaway_count(request.duration)))
        .replace("{duration}", _length(request.duration))
        .replace("{tab_labels}", format_tab_labels([label for label in labels if label]))
        .replace("{stamp_rule}", _stamp_rule(request))
        .replace("{video}", _video_section(request))
        # Last, so text inside the extraction is never mistaken for a placeholder.
        .replace("{extracted}", compact.text or _NOTHING_EXTRACTED)
    )
    text = f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{body.rstrip()}\n"
    return f"{text}{_problems_section(problems)}" if problems else text


def hero_evidence(request: HeroRequest, compact: CompactExtraction) -> HeroEvidence:
    """What the call reads about the video — the reference every fact check uses; the
    clocks it read (``<extracted>`` stamps, ``<video_memory>``) anchor a typed stamp when
    no transcript is read."""
    parts = [request.title, request.video_memory, compact.evidence]
    if request.reads_transcript and request.blocks is not None:
        parts.append(request.blocks.chunks[0].transcript)
    stamps = stamps_at_quotes(compact, request.quote_index)
    shown = frozenset(compact.stamps.values()) | frozenset(stamps.values())
    return HeroEvidence(
        text="\n".join(parts),
        stamps=stamps,
        duration=request.duration,
        tab_labels=tuple(request.tab_labels),
        takeaway_count=takeaway_count(request.duration),
        reads_transcript=request.reads_transcript,
        clocks=shown | clock_seconds(request.video_memory),
        quote_index=request.quote_index,
    )
