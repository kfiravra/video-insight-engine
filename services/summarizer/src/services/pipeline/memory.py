"""Video memory — one fast-model read of the full transcript, in parallel with the plan.

Produces the outline (real section boundaries), the Appendix C evidence booleans,
the hero tldr and the key takeaways (prompt: ``prompts/memory.txt``).

Never raises into the pipeline. ``None`` means the stage failed and every consumer
falls back: no outline (chapter_detect only when batching needs it), evidence = the
plan's only, tldr/takeaways from synthesis as today. A partial answer is repaired
field by field — an empty field in the returned ``MemoryResult`` means the same
per-field fallback.

Model: whatever ``LLM_EXTRACTION_MODEL`` resolves to (D5 — no setting of its own;
prod pins Haiku 4.5, blank falls through to the primary model).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from llm_common.context import llm_feature_var

from ...config import settings
from ...models.memory_types import MemoryInput, MemoryResult, OutlineSection
from ...shared_config.domain_config import EVIDENCE_KEYS
from ...utils.data_helpers import parse_timestamp_to_seconds
from ...utils.json_parsing import parse_json_response
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ...utils.llm_retry import call_llm_with_retry
from ..transcript.render import format_marker
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "memory.txt"

MEMORY_FEATURE = "summarize:memory"
MEMORY_STAGE = "memory"
MEMORY_MODEL_STAGE = "extraction"
MEMORY_MAX_TOKENS = 1200
MEMORY_TIMEOUT_S = 25.0
# One attempt: memory runs alongside the plan inside the phase-2 group, and a
# retry budget became primary-primary-fallback (up to 3 x 25 s) once the
# cross-provider fallback landed. Every reader has a fallback for a missing
# memory; a dropped connection is still resent once by call_llm_with_retry.
MEMORY_MAX_RETRIES = 0
MEMORY_TEMPERATURE = 0.0

MIN_SECTION_SECONDS = 60
# An outline whose sections stop this far short of the duration only covers
# the start of the video; stretching its last section over the rest would
# pass a 20-minute outline of a 90-minute video. max(share, floor) of it.
MAX_UNCOVERED_SHARE = 0.2
MAX_UNCOVERED_FLOOR_SECONDS = 300
MIN_SECTIONS = 4
MAX_SECTIONS = 12
OUTLINE_TITLE_MAX_CHARS = 60
TLDR_MAX_CHARS = 150
MIN_TAKEAWAYS = 3
MAX_TAKEAWAYS = 5

_WHITESPACE_RE = re.compile(r"\s+")
_BOOL_STRINGS: dict[str, bool] = {"true": True, "false": False}


# ─── Prompt ───


def _load_memory_prompt() -> str:
    """Registry-first memory prompt. Records version on the active trace."""
    return load_prompt_text(PROMPT_PATH)


def format_clock(seconds: int) -> str:
    """``m:ss`` / ``h:mm:ss`` — the transcript markers' clock, without brackets.

    Outline boundaries and evidence ranges must read exactly like the
    ``[m:ss]`` markers the readers anchor on, so the format is borrowed from
    the marker renderer instead of re-implemented.
    """
    return format_marker(seconds)[1:-1]


def build_memory_prompt(request: MemoryInput) -> str:
    """Render ``memory.txt`` for one video (D16: English output line first)."""
    template = _load_memory_prompt()
    transcript = request.transcript
    body = (
        template.replace("{title}", sanitize_for_prompt(request.title, max_len=200))
        .replace("{channel}", sanitize_for_prompt(request.channel or "Unknown", max_len=100))
        .replace("{duration}", format_clock(request.duration))
        .replace(
            "{description}",
            sanitize_for_prompt(request.description or "N/A", max_len=1000),
        )
        # Last, so text inside the transcript is never mistaken for a placeholder.
        .replace("{transcript}", sanitize_for_prompt(transcript, max_len=len(transcript)))
    )
    return f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{body}"


# ─── Repair ───


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def truncate_words(text: str, max_chars: int) -> str:
    """Cut at a word boundary so the result (with its ellipsis) fits ``max_chars``."""
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{cut.rstrip(' ,;:—-')}…"


def _to_seconds(raw: object) -> int | None:
    # bool is an int subclass — ``true`` is never a timestamp.
    if isinstance(raw, bool):
        return None
    return parse_timestamp_to_seconds(raw)


def _parse_section(item: object, duration: int) -> OutlineSection | None:
    """One raw section → clamped section, or None when it can't be read."""
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    start = _to_seconds(item.get("start"))
    end = _to_seconds(item.get("end"))
    if not isinstance(title, str) or not _collapse(title) or start is None or end is None:
        return None
    start, end = min(start, duration), min(end, duration)
    if end <= start:
        return None
    title = truncate_words(_collapse(title), OUTLINE_TITLE_MAX_CHARS)
    return OutlineSection(start=start, end=end, title=title)


def _make_contiguous(sections: list[OutlineSection], duration: int) -> list[OutlineSection]:
    """Order by start and close every gap/overlap: first at 0, each ends at the next start.

    The model anchors starts on ``[m:ss]`` markers where the topic shifts, so
    starts are trusted over ends; the last section ends at the duration.
    """
    unique: list[OutlineSection] = []
    for section in sorted(sections, key=lambda s: s.start):
        if not unique or section.start != unique[-1].start:
            unique.append(section)
    starts = [0, *(s.start for s in unique[1:])]
    ends = [*starts[1:], duration]
    return [
        OutlineSection(start=start, end=end, title=section.title)
        for section, start, end in zip(unique, starts, ends, strict=True)
    ]


def _fold_short_sections(sections: list[OutlineSection]) -> list[OutlineSection]:
    """Fold sections under a minute into the previous section (the first into the next)."""
    result: list[OutlineSection] = []
    carried_start: int | None = None
    for section in sections:
        start = section.start if carried_start is None else carried_start
        if section.end - start >= MIN_SECTION_SECONDS:
            result.append(section.model_copy(update={"start": start}))
            carried_start = None
        elif result:
            result[-1] = result[-1].model_copy(update={"end": section.end})
        else:
            carried_start = start
    return result


def _covers_the_video(sections: list[OutlineSection], duration: int) -> bool:
    """False when the model's own last end stops well short of the duration."""
    uncovered = duration - max(s.end for s in sections)
    allowed = max(MAX_UNCOVERED_SHARE * duration, MAX_UNCOVERED_FLOOR_SECONDS)
    if uncovered <= allowed:
        return True
    logger.warning(
        "Memory outline rejected: sections end at %ds of a %ds video",
        duration - uncovered,
        duration,
    )
    return False


def _min_sections(duration: int) -> int:
    """4 sections, or one per full minute for videos under 4 minutes."""
    return max(1, min(MIN_SECTIONS, duration // MIN_SECTION_SECONDS))


def repair_outline(raw: object, duration: int) -> list[OutlineSection]:
    """Valid outline from the model's sections, or ``[]`` when it can't be made valid."""
    if duration < MIN_SECTION_SECONDS or not isinstance(raw, list):
        return []
    parsed = [s for s in (_parse_section(item, duration) for item in raw) if s is not None]
    if not parsed or not _covers_the_video(parsed, duration):
        return []
    outline = _fold_short_sections(_make_contiguous(parsed, duration))
    if not _min_sections(duration) <= len(outline) <= MAX_SECTIONS:
        logger.warning(
            "Memory outline rejected: %d sections after repair (allowed %d-%d)",
            len(outline),
            _min_sections(duration),
            MAX_SECTIONS,
        )
        return []
    return outline


def _as_bool(raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return _BOOL_STRINGS.get(raw.strip().lower())
    return None


def repair_evidence(raw: object) -> dict[str, bool]:
    """Known evidence keys with a readable boolean, in vocabulary order.

    An unknown key is dropped; a missing or unreadable one stays absent ("no
    opinion") rather than becoming ``False``.
    """
    if not isinstance(raw, dict):
        return {}
    evidence: dict[str, bool] = {}
    for key in EVIDENCE_KEYS:
        value = _as_bool(raw.get(key))
        if value is not None:
            evidence[key] = value
    return evidence


def repair_tldr(raw: object) -> str:
    """Whitespace-collapsed tldr, cut at a word boundary to 150 chars."""
    if not isinstance(raw, str):
        return ""
    return truncate_words(_collapse(raw), TLDR_MAX_CHARS)


def repair_takeaways(raw: object) -> list[str]:
    """3–5 distinct non-empty takeaways, else ``[]`` (synthesis's takeaways are used)."""
    if not isinstance(raw, list):
        return []
    takeaways: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _collapse(item) if isinstance(item, str) else ""
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            takeaways.append(text)
    takeaways = takeaways[:MAX_TAKEAWAYS]
    return takeaways if len(takeaways) >= MIN_TAKEAWAYS else []


def parse_memory_output(data: object, duration: int) -> MemoryResult | None:
    """Validate + repair the model's JSON; ``None`` when nothing usable survives."""
    if not isinstance(data, dict):
        return None
    result = MemoryResult(
        outline=repair_outline(data.get("outline"), duration),
        evidence=repair_evidence(data.get("evidence")),
        tldr=repair_tldr(data.get("tldr")),
        takeaways=repair_takeaways(data.get("takeaways")),
    )
    if result.is_empty():
        logger.warning("Memory response had no usable field; falling back")
        return None
    logger.info(
        "Memory: outline=%d sections, evidence=%d/%d keys, tldr=%s, takeaways=%d",
        len(result.outline),
        len(result.evidence),
        len(EVIDENCE_KEYS),
        "yes" if result.tldr else "no",
        len(result.takeaways),
    )
    return result


# ─── Stage ───


async def _run_memory(llm_service: LLMService, request: MemoryInput) -> MemoryResult | None:
    try:
        prompt = build_memory_prompt(request)
    except FileNotFoundError:
        logger.warning("Memory prompt not found at %s", PROMPT_PATH)
        return None

    raw = await call_llm_with_retry(
        llm_service,
        prompt,
        max_tokens=MEMORY_MAX_TOKENS,
        timeout=MEMORY_TIMEOUT_S,
        max_retries=MEMORY_MAX_RETRIES,
        stage_name=MEMORY_STAGE,
        json_mode=True,
        temperature=MEMORY_TEMPERATURE,
        model_override=settings.get_stage_model(MEMORY_MODEL_STAGE),
    )
    if not raw:
        logger.warning("Memory LLM call failed after retries; falling back")
        return None

    data = parse_json_response(raw)
    if not data:
        logger.warning("Memory returned unparseable JSON: %.200s", raw)
        return None
    return parse_memory_output(data, request.duration)


async def run_memory(llm_service: LLMService, request: MemoryInput) -> MemoryResult | None:
    """Run the memory call for one video; ``None`` on any failure (never raises).

    Tags its LLM call ``summarize:memory`` so cost attribution and the replay
    cassette keep it apart from the plan it runs alongside.
    """
    feature_token = llm_feature_var.set(MEMORY_FEATURE)
    try:
        return await _run_memory(llm_service, request)
    except Exception:  # noqa: BLE001 — memory is optional; a bug here must not fail the run
        logger.exception("Memory stage crashed; continuing without memory")
        return None
    finally:
        llm_feature_var.reset(feature_token)
