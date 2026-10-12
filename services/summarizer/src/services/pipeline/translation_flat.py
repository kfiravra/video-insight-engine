"""The ``translate_flat`` call: a flat list of strings in, the same list translated out.

Shared by the translation phase (the English output into the source language,
``pipeline.translation``) and the RAG transcript text (a non-English transcript
into English, ``transcript.english``), together with the batch sizing both use
(``char_batches``) and the echo check both apply to an answer (``is_mirror``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import settings
from ...utils.json_parsing import parse_json_response
from ...utils.language_utils import get_language_name
from ...utils.llm_retry import call_llm_with_retry
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

FLAT_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "translate_flat.txt"

# Keys JSON-mode small models wrap the answer array in.
_WRAPPER_KEYS = ("translations", "items", "strings", "translated", "output", "result", "data")


# ─── Batches ─────────────────────────────────────────────────────────────────


# Source characters per call, both directions, measured by ``answer_size``.
# gpt-4o-mini answers ≈ 0.32 tokens per alphabetic or abjad character either
# way at ≈ 88 tokens/s (EqaMrzd9nZU, 2026-10-12: 15,435 English chars → 4,996
# Hebrew tokens in 56.5 s; 8,139 Hebrew chars → ≈ 10.3k English chars, ≈ 2.6k
# tokens), so a full batch answers ≈ 800 tokens in ≈ 10 s and the callers run
# their batches in parallel. Fewer strings per call also miscount less. The
# prompt costs 280 input tokens per call.
TRANSLATION_BATCH_CHARS = 2500

# A Han character answers about one English word, ≈ 2.5 alphabetic
# characters' worth; kana and Hangul ≈ 1.5 (Japanese and Korean spell more
# per word). Escaped: a normalizing editor maps U+F900 ideographs to others.
_HAN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_KANA_HANGUL = re.compile(r"[\u3040-\u30FF\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]")

# Answer budget of one call: ≈ 1 token per weighted source character (3× the
# measured rate) on top of the JSON wrapper, so a full batch reserves ≈ 3.5k
# tokens — not a flat 16k — against the provider's tokens-per-minute limit.
_ANSWER_TOKENS_BASE = 1024
_ANSWER_TOKENS_MAX = 16384


def answer_size(text: str) -> float:
    """``text``'s length in alphabetic characters of answer: Han ×2.5, kana / Hangul ×1.5."""
    return len(text) + 1.5 * len(_HAN.findall(text)) + 0.5 * len(_KANA_HANGUL.findall(text))


def char_batches(
    texts: Sequence[str], max_chars: int, max_count: int | None = None
) -> list[list[str]]:
    """Consecutive runs of ``texts`` within ``max_chars`` (``answer_size``) and
    ``max_count`` strings when given; a longer string goes alone."""
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0.0
    for text in texts:
        text_size = answer_size(text)
        if current and (size + text_size > max_chars or len(current) == max_count):
            batches.append(current)
            current, size = [], 0.0
        current.append(text)
        size += text_size
    if current:
        batches.append(current)
    return batches


def answer_tokens(strings: Sequence[str]) -> int:
    """``max_tokens`` for translating ``strings``: sized to them, never above 16,384."""
    return min(_ANSWER_TOKENS_MAX, _ANSWER_TOKENS_BASE + math.ceil(sum(map(answer_size, strings))))


# ─── LLM call ────────────────────────────────────────────────────────────────


def _unwrap_list(parsed: Any) -> Any:
    """The array out of a single-key wrapper object: a known key name, else the only list value."""
    if not isinstance(parsed, dict):
        return parsed
    for key in _WRAPPER_KEYS:
        if isinstance(parsed.get(key), list):
            return parsed[key]
    list_values = [v for v in parsed.values() if isinstance(v, list)]
    return list_values[0] if len(list_values) == 1 else parsed


def _string_list(raw: str, expected: int) -> list[str] | None:
    """The answer as ``expected`` strings; None when it is anything else."""
    parsed = _unwrap_list(parse_json_response(raw))
    if not isinstance(parsed, list):
        logger.warning("Translation returned non-list (type=%s)", type(parsed).__name__)
        return None
    if not all(isinstance(s, str) for s in parsed):
        logger.warning("Translation contained non-string items")
        return None
    if len(parsed) != expected:
        logger.warning("Translation length mismatch: expected %d, got %d", expected, len(parsed))
        return None
    return parsed


async def translate_flat_list(
    llm_service: LLMService,
    strings: list[str],
    source_language: str,
    target_language: str,
    stage_name: str = "translation_full",
) -> list[str] | None:
    """Translate a flat list of strings from ``source_language`` to
    ``target_language`` in one fast-model call.

    Returns None on any failure (LLM error, JSON parse, non-string items,
    length mismatch). Caller decides how to recover.
    """
    if not strings:
        return []

    prompt = (
        load_prompt_text(FLAT_PROMPT_PATH)
        .replace("{source_language}", get_language_name(source_language))
        .replace("{target_language}", get_language_name(target_language))
        .replace("{strings_json}", json.dumps(strings, ensure_ascii=False))
    )
    try:
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            max_tokens=answer_tokens(strings),
            timeout=90.0,
            max_retries=2,
            stage_name=stage_name,
            json_mode=True,
            use_fast_model=True,
            model_override=settings.get_stage_model(stage_name),
        )
        return _string_list(raw, len(strings)) if raw else None
    except (json.JSONDecodeError, ValueError, TypeError, asyncio.TimeoutError, OSError) as e:
        # Narrow boundary catch: JSON parse / shape / I/O / timeout errors
        # are the realistic failure modes for an LLM-mode JSON call. Other
        # exceptions (KeyError, AttributeError) are programming bugs and
        # should surface in tests rather than be silently downgraded to a
        # translation no-op.
        logger.error("Translation failed: %s", e)
        return None


# ─── Echo check ──────────────────────────────────────────────────────────────


# Fraction of overall strings that must mirror before we declare the LLM
# ignored the prompt. Set high enough that a legitimate translation keeping a
# few brand names / embedded English phrases verbatim doesn't trip the gate.
_MIRROR_OVERALL_THRESHOLD = 0.5

# Below this many collected strings the "3 longest" sample is too small to be
# reliable — fall back to a stricter overall-mirror check so small payloads
# still get gated. 10 was chosen because it's roughly the floor at which a
# real video carries enough prose (≥3 tabs × ~3 strings/tab) for the longest-
# sample heuristic to discriminate; below that, a model echoing 4 of 6 short
# strings would pass the longest-3 gate by coincidence.
_MIRROR_SMALL_PAYLOAD_FLOOR = 10
# Stricter threshold for small payloads: more than 1/3 mirrored is suspicious
# when only a handful of strings exist (proper nouns dominate small samples).
_MIRROR_SMALL_PAYLOAD_THRESHOLD = 0.33


def is_mirror(originals: list[str], translations: list[str]) -> bool:
    """True when the LLM appears to have echoed source-language strings back.

    Two-signal gate to avoid false-positives on legitimate translations that
    keep a few strings verbatim (brand names, code identifiers, quoted English
    phrases embedded in a non-English video):

      1. ALL of the 3 LONGEST strings are byte-identical between input and
         output. Long strings legitimately translating to the exact same
         bytes is near-impossible in practice — short tokens (proper nouns,
         ASCII) can collide but not full prose.
      2. AND > 50% of ALL strings are byte-identical. A real translation
         keeps maybe 5-15% verbatim (names, brands); >50% means the model
         echoed the input.

    Both conditions must hold. This protects against the audited "small model
    ignores the prompt and echoes input" failure mode while preserving good
    translations that happen to retain a few long brand strings verbatim.

    Small payloads (<10 strings) fall back to a single stricter overall-ratio
    check — the "3 longest" sample is unreliable when proper nouns and short
    ASCII tokens dominate a tiny sample.
    """
    if not originals or len(originals) != len(translations):
        return False
    overall_mirror_count = sum(1 for o, t in zip(originals, translations) if o == t)
    overall_ratio = overall_mirror_count / len(originals)

    if len(originals) < _MIRROR_SMALL_PAYLOAD_FLOOR:
        # Small payloads: a single threshold; >33% verbatim is the signal.
        return overall_ratio > _MIRROR_SMALL_PAYLOAD_THRESHOLD

    indices = sorted(range(len(originals)), key=lambda i: -len(originals[i]))[:3]
    longest_all_mirror = all(originals[i] == translations[i] for i in indices)
    if not longest_all_mirror:
        return False
    return overall_ratio > _MIRROR_OVERALL_THRESHOLD
