"""English text for the RAG index from a non-English transcript — no audio involved.

The transcript points are embedded with ``all-MiniLM-L6-v2``, which is
English-only, so a non-English video needs English text to embed.
``english_blocks`` translates the transcript's own segments on the fast model
with the ``translate_flat`` prompt the translation phase already uses (same
Langfuse registry entry). Nothing is downloaded and Whisper is never called.

Consecutive segments are first merged into sentence-sized blocks (one string
each, keeping the first segment's start and the last one's end), so the
chunker's [m:ss] citations stay within a block of the speech. Raw caption
lines make poor units: a subtitle event often holds two lines, and the model
returns them as two entries — the count no longer matches and the whole call
is lost (gpt-4o-mini, 4 of 6 calls on a 31-minute Arabic track, 2026-10-12).

Blocks go out in batches of at most ``_BATCH_CHARS`` source characters,
``_CONCURRENCY`` calls at a time (retries included); a failed call is
retried once as two halves. A part that still fails (LLM error, count
mismatch) or echoes its input keeps its source text — a partly translated
transcript still retrieves better than an untranslated one — and ``None``
means nothing was translated.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.services.pipeline.pipeline_helpers import normalize_segments
from src.services.pipeline.translation_flat import is_mirror, translate_flat_list
from src.services.transcription.transcript import clean_transcript
from src.utils.language_utils import detect_language_by_script, detect_language_from_text

if TYPE_CHECKING:
    from src.services.llm import LLMService

logger = logging.getLogger(__name__)

# Span name of the translation calls (Langfuse, pipeline.timing) and their
# stage key: LLM_TRANSLATION_MODEL pins them (config ``_STAGE_TO_SETTING``),
# unset → the fast model.
RAG_TRANSLATION_STAGE = "rag_translation"

# A block closes at the first sentence end past _BLOCK_CHARS, or at
# _BLOCK_MAX_CHARS mid-sentence (unpunctuated ASR captions).
_BLOCK_CHARS = 200
_BLOCK_MAX_CHARS = 400
_SENTENCE_END = re.compile(r"[.!?…。！？؟]['\")\]»”]*$")
# Bidi controls YouTube wraps RTL subtitle lines in: presentation only.
# Escaped, never literal: a literal override character can hide code (Trojan Source).
_BIDI_CONTROLS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")

# Source characters per call: ~1.2k output tokens, ~10 s on gpt-4o-mini.
_BATCH_CHARS = 4000
# Calls in flight per video, retry halves included; the translation phase
# runs its own 4 at the same time.
_CONCURRENCY = 4

# Window judged at the start, the middle and the end of a transcript: an
# English intro on a non-English video must not pass the whole video as
# English. The script check reads 3,000 characters of what it is given.
_SAMPLE_CHARS = 3000


@dataclass(frozen=True)
class EnglishTranscript:
    """English text to embed and the timed blocks it was joined from."""

    text: str
    blocks: list[dict[str, Any]]  # {"text", "startMs", "endMs"}


@dataclass
class _OpenBlock:
    """The block being filled: its segments' texts and time span."""

    texts: list[str] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0

    @property
    def size(self) -> int:
        return sum(len(text) + 1 for text in self.texts)

    def add(self, text: str, start_ms: int, end_ms: int) -> None:
        if not self.texts:
            self.start_ms = start_ms
        self.texts.append(text)
        self.end_ms = max(self.end_ms, end_ms)

    def is_full(self) -> bool:
        size = self.size
        return size >= _BLOCK_MAX_CHARS or (
            size >= _BLOCK_CHARS and bool(_SENTENCE_END.search(self.texts[-1]))
        )

    def close(self) -> dict[str, Any]:
        return {"text": " ".join(self.texts), "startMs": self.start_ms, "endMs": self.end_ms}


def _samples(text: str) -> list[str]:
    """``text``'s start, middle and end windows (the whole text when it fits one)."""
    if len(text) <= _SAMPLE_CHARS:
        return [text]
    middle = (len(text) - _SAMPLE_CHARS) // 2
    return [text[:_SAMPLE_CHARS], text[middle : middle + _SAMPLE_CHARS], text[-_SAMPLE_CHARS:]]


def _reads_english(sample: str) -> bool:
    return detect_language_by_script(sample) is None and detect_language_from_text(sample) == "en"


def is_english_text(text: str) -> bool:
    """True when ``text`` reads as English at its start, middle and end: no
    non-Latin script and langdetect says ``en`` in every window.

    Judges the transcript itself, not the video's language label: YouTube
    labels Hebrew ``iw``, so the caption picker never matches a detected
    ``he`` and takes an English track (an auto-dub or a machine translation).
    """
    return all(_reads_english(sample) for sample in _samples(text))


def _blocks(segments: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consecutive spoken segments merged into sentence-sized ``{text, startMs, endMs}`` blocks."""
    blocks: list[dict[str, Any]] = []
    block = _OpenBlock()
    for seg in normalize_segments(list(segments)):
        text = " ".join(_BIDI_CONTROLS.sub("", str(seg["text"])).split())
        if not text:
            continue
        block.add(text, seg["startMs"], seg["endMs"])
        if block.is_full():
            blocks.append(block.close())
            block = _OpenBlock()
    if block.texts:
        blocks.append(block.close())
    return blocks


def _batches(texts: Sequence[str]) -> list[list[str]]:
    """Consecutive runs of ``texts`` within the per-call character budget."""
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and size + len(text) > _BATCH_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        batches.append(current)
    return batches


async def _call(
    llm_service: LLMService, texts: list[str], source_language: str, slots: asyncio.Semaphore
) -> list[str] | None:
    """One translation call, in one of the video's ``slots``; None when it failed or echoed."""
    try:
        async with slots:
            result = await translate_flat_list(
                llm_service, texts, source_language, "en", stage_name=RAG_TRANSLATION_STAGE
            )
    except Exception as e:  # a provider error ends this call, never its siblings
        logger.warning("RAG translation call of %d blocks failed: %s", len(texts), e)
        return None
    if result is None or is_mirror(texts, result):
        return None
    return result


async def _translate_batch(
    llm_service: LLMService, batch: list[str], source_language: str, slots: asyncio.Semaphore
) -> list[str] | None:
    """``batch`` in English; a failed call is retried once as two halves."""
    result = await _call(llm_service, batch, source_language, slots)
    if result is not None or len(batch) < 2:
        return result
    mid = len(batch) // 2
    first, second = await asyncio.gather(
        _call(llm_service, batch[:mid], source_language, slots),
        _call(llm_service, batch[mid:], source_language, slots),
    )
    if first is None and second is None:
        return None
    if first is None or second is None:
        logger.warning("RAG translation kept %d blocks untranslated after the retry", mid)
    return (first or batch[:mid]) + (second or batch[mid:])


async def _translate_texts(
    llm_service: LLMService, texts: list[str], source_language: str
) -> list[str] | None:
    """``texts`` in English, same length and order; failed parts keep their source text."""
    batches = _batches(texts)
    # Each call takes a slot, not each batch: a batch retrying as two halves
    # holds none while it waits, so retries never push past _CONCURRENCY.
    slots = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(
        *(_translate_batch(llm_service, batch, source_language, slots) for batch in batches)
    )
    if all(result is None for result in results):
        return None
    untranslated = sum(len(b) for b, result in zip(batches, results) if result is None)
    if untranslated:
        logger.warning("RAG translation kept %d/%d blocks untranslated", untranslated, len(texts))
    return [
        text
        for batch, result in zip(batches, results)
        for text in (result if result is not None else batch)
    ]


async def english_blocks(
    llm_service: LLMService,
    segments: Sequence[dict[str, Any]],
    source_language: str,
) -> EnglishTranscript | None:
    """The transcript translated to English block by block, each block keeping its span.

    ``segments`` may be ``start``/``duration`` or ``startMs``/``endMs`` shaped.
    None when there is no speech or nothing could be translated.
    """
    blocks = _blocks(segments)
    if not blocks:
        return None
    translated = await _translate_texts(
        llm_service, [block["text"] for block in blocks], source_language
    )
    if translated is None:
        return None
    english = [{**block, "text": text} for block, text in zip(blocks, translated)]
    joined = " ".join(block["text"] for block in english)
    return EnglishTranscript(text=clean_transcript(joined, source_language="en"), blocks=english)
