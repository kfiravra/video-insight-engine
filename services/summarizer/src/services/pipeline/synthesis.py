"""Synthesis — the closing masterSummary + seoDescription (pipeline-1min 1d.3).

Input: the run's ``<video_memory>`` block, a compact form of the FINAL
extraction and the labels of the tabs the viewer gets. The hero tldr + key
takeaways come from the memory stage; synthesis writes them too only when
memory left one of them empty (``hero_fallback``), and ``build_synthesis_dict``
prefers memory's per field, so the stored meta keeps what the hero showed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ...config import settings
from ...models.pipeline_types import SynthesisResult
from ...utils.json_parsing import parse_json_response
from ...utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE
from ...utils.llm_retry import call_llm_with_retry
from .memory import repair_takeaways, repair_tldr, truncate_words
from .pipeline_helpers import sanitize_for_prompt, truncate_json_safely
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from ...models.memory_types import MemoryResult
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "synthesis.txt"

SYNTHESIS_MAX_TOKENS = 1200
SYNTHESIS_TIMEOUT_S = 20.0
SYNTHESIS_MAX_RETRIES = 1
EXTRACTION_SUMMARY_MAX_CHARS = 6000
SEO_MAX_CHARS = 160
# Compact extraction: every list is cut to the first cap that lets the whole
# extraction fit, so each domain and field keeps at least one item instead of
# the head of the first domain filling the budget.
_LIST_CAPS = (8, 5, 3, 1)
_STRING_MAX_CHARS = 200
_NO_VIDEO_MEMORY = "<video_memory>\nNot available\n</video_memory>"
_NO_TABS = "(none)"

_HERO_FIELDS = (
    "- tldr: one sentence of at most 150 characters with the video's angle — what makes "
    "THIS video different — not its topic.\n"
    "- keyTakeaways: 3-5 items, each carrying a specific number, name or measurement "
    "from the video. No generic advice."
)
_KEYS = '"masterSummary" and "seoDescription"'
_KEYS_WITH_HERO = '"masterSummary", "seoDescription", "tldr" and "keyTakeaways"'

# The meta keys assembly writes from the synthesis dict (assembly/core.py), in
# the order today's dict has them.
SYNTHESIS_KEYS = ("tldr", "keyTakeaways", "masterSummary", "seoDescription")


class SynthesisInput(BaseModel):
    """Everything one synthesis prompt is rendered from."""

    model_config = {"frozen": True}

    title: str
    channel: str | None
    duration: int | None
    output_type: str
    extraction_summary: str
    video_context: str = ""
    tab_labels: tuple[str, ...] = ()
    hero_fallback: bool = False


# ─── Compact extraction ───


def _compact_string(text: str) -> str | None:
    collapsed = " ".join(text.split())
    return truncate_words(collapsed, _STRING_MAX_CHARS) if collapsed else None


def _compact_list(items: list[object], list_cap: int) -> list[object] | None:
    kept = [c for c in (_compact(item, list_cap) for item in items) if c is not None]
    if not kept:
        return None
    if len(kept) <= list_cap:
        return kept
    return [*kept[:list_cap], f"+{len(kept) - list_cap} more"]


def _compact(value: object, list_cap: int) -> object:
    """``value`` without empty parts, strings shortened, lists capped; ``None`` when empty."""
    if isinstance(value, str):
        return _compact_string(value)
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        pruned = {str(k): c for k, v in value.items() if (c := _compact(v, list_cap)) is not None}
        return pruned or None
    if isinstance(value, list):
        return _compact_list(value, list_cap)
    return None


def compact_extraction(
    extraction: Mapping[str, Any] | None, max_chars: int = EXTRACTION_SUMMARY_MAX_CHARS
) -> str:
    """The final extraction as compact JSON within ``max_chars`` (deterministic)."""
    if not extraction:
        return ""
    compacted: object = None
    for list_cap in _LIST_CAPS:
        compacted = _compact(extraction, list_cap)
        if compacted is None:
            return ""
        text = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            return text
    return truncate_json_safely(compacted, max_chars)


# ─── Prompt ───


def _load_synthesis_prompt() -> str:
    """Registry-first synthesis prompt. Records version on the active trace."""
    return load_prompt_text(PROMPT_PATH)


def format_tab_labels(labels: Sequence[str]) -> str:
    """One ``- label`` line per tab, or ``(none)``."""
    lines = [f"- {sanitize_for_prompt(label, max_len=80)}" for label in labels]
    return "\n".join(lines) or _NO_TABS


def _duration_minutes(duration: int | None) -> str:
    return str(round(duration / 60)) if duration is not None and duration > 0 else "unknown"


def build_synthesis_prompt(request: SynthesisInput) -> str:
    """Render ``synthesis.txt`` (English output line first, as every writer)."""
    body = (
        _load_synthesis_prompt()
        .replace("{hero_fields}", _HERO_FIELDS if request.hero_fallback else "")
        .replace("{output_keys}", _KEYS_WITH_HERO if request.hero_fallback else _KEYS)
        .replace("{title}", sanitize_for_prompt(request.title))
        .replace("{channel}", sanitize_for_prompt(request.channel or "Unknown"))
        .replace("{duration_minutes}", _duration_minutes(request.duration))
        .replace("{output_type}", request.output_type)
        .replace("{tab_labels}", format_tab_labels(request.tab_labels))
        .replace("{video_context}", request.video_context or _NO_VIDEO_MEMORY)
        # Last, so text inside the extraction is never mistaken for a placeholder.
        .replace("{extraction_summary}", request.extraction_summary[:EXTRACTION_SUMMARY_MAX_CHARS])
    )
    return f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{body}"


# ─── Validation ───


def _text(raw: object) -> str:
    return " ".join(raw.split()) if isinstance(raw, str) else ""


def parse_synthesis_output(data: object) -> SynthesisResult:
    """Validate + repair the model's JSON; ``ValueError`` when there is no masterSummary."""
    if not isinstance(data, dict) or not data:
        raise ValueError("Failed to parse synthesis response from LLM")
    master_summary = data.get("masterSummary")
    if not isinstance(master_summary, str) or not master_summary.strip():
        raise ValueError("Synthesis response has no masterSummary")
    return SynthesisResult.model_validate(
        {
            "masterSummary": master_summary.strip(),
            "seoDescription": truncate_words(_text(data.get("seoDescription")), SEO_MAX_CHARS),
            "tldr": repair_tldr(data.get("tldr")),
            "keyTakeaways": repair_takeaways(data.get("keyTakeaways")),
        }
    )


# ─── Call ───


async def synthesize(
    llm_service: LLMService,
    title: str,
    channel: str | None,
    duration: int | None,
    output_type: str,
    extraction_summary: str,
    video_context: str = "",
    *,
    tab_labels: Sequence[str] = (),
    hero_fallback: bool = False,
) -> SynthesisResult:
    """Write masterSummary + seoDescription (+ tldr/keyTakeaways with ``hero_fallback``)."""
    prompt = build_synthesis_prompt(
        SynthesisInput(
            title=title,
            channel=channel,
            duration=duration,
            output_type=output_type,
            extraction_summary=extraction_summary,
            video_context=video_context,
            tab_labels=tuple(tab_labels),
            hero_fallback=hero_fallback,
        )
    )
    raw = await call_llm_with_retry(
        llm_service,
        prompt,
        max_tokens=SYNTHESIS_MAX_TOKENS,
        timeout=SYNTHESIS_TIMEOUT_S,
        max_retries=SYNTHESIS_MAX_RETRIES,
        stage_name="synthesis",
        json_mode=True,
        use_fast_model=True,
        model_override=settings.get_stage_model("synthesis"),
    )
    if not raw:
        raise ValueError("Synthesis LLM call failed after retries")
    logger.debug("Synthesis raw response: %.500s", raw)
    return parse_synthesis_output(parse_json_response(raw))


# ─── Merge with memory ───


def _hero(memory: MemoryResult | None, result: SynthesisResult | None) -> tuple[str, list[str]]:
    """Memory's tldr / takeaways per field, else the synthesis fallback's."""
    memory_tldr = memory.tldr if memory else ""
    memory_takeaways = list(memory.takeaways) if memory else []
    tldr = memory_tldr or (result.tldr if result else "")
    takeaways = memory_takeaways or (list(result.key_takeaways) if result else [])
    return tldr, takeaways


def build_synthesis_dict(
    memory: MemoryResult | None, result: SynthesisResult | None
) -> dict[str, Any]:
    """The meta-shaped synthesis dict: memory's tldr/takeaways first, synthesis's text.

    ``build_synthesis_dict(memory, None)`` is the pre-assembly seed (the hero
    into meta + overview before synthesis lands). ``{}`` when every field is
    empty — the value a failed synthesis left before memory existed.
    """
    tldr, takeaways = _hero(memory, result)
    merged: dict[str, Any] = {
        "tldr": tldr,
        "keyTakeaways": takeaways,
        "masterSummary": result.master_summary if result else "",
        "seoDescription": result.seo_description if result else "",
    }
    return merged if any(merged.values()) else {}
