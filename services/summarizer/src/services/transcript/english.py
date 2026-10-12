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

Blocks go out in batches of at most ``TRANSLATION_BATCH_CHARS`` source
characters (the output translation's size), ``_CONCURRENCY`` calls at a time
(retries included); a failed call is retried once as two halves. As soon
as a batch returns, each of its blocks still untranslated — no answer (LLM
error, count mismatch), its source text echoed, or a Hebrew / Arabic / CJK
character left in its English — is sent once more on its own: one string per
call has no count to miss. At most half the blocks (at least 8) are retried
per transcript, and none when no batch answered at all (provider down). What
is still untranslated after that keeps what it had (a partial answer, else
its source text — a partly translated transcript still retrieves better than
an untranslated one) and is counted in one log line; ``None`` means nothing
was translated.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.services.pipeline.pipeline_helpers import normalize_segments
from src.services.pipeline.translation_flat import (
    TRANSLATION_BATCH_CHARS,
    char_batches,
    is_mirror,
    translate_flat_list,
)
from src.services.transcription.transcript import clean_transcript
from src.utils.language_utils import (
    detect_language_by_script,
    detect_language_from_text,
    has_hebrew_arabic_or_cjk,
)

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

# Calls in flight per video, retries included; the translation phase runs
# its own 8 at the same time. A 10-minute transcript's batches (4 on
# EqaMrzd9nZU) start at once; a longer one queues, off the path to ``done``.
_CONCURRENCY = 4
# Single-block retries per transcript: half its blocks, at least 8 — room
# for a long ASR track's miscounted batches, bounded when the provider fails
# call after call.
_RETRY_MIN_BLOCKS = 8

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


@dataclass
class _Retries:
    """The single-block retries a transcript's batches share.

    A batch retries its untranslated blocks as soon as it returns, so a long
    transcript is not held to its slowest batch. A batch without any answer
    waits until some batch has one: when none has (provider down, a 429
    storm), nothing is retried. At most ``cap`` blocks per transcript.
    """

    cap: int
    batches_left: int
    answered: bool = False
    retried: int = 0
    skipped: int = 0
    decided: asyncio.Event = field(default_factory=asyncio.Event)

    def batch_returned(self, answered: bool) -> None:
        self.batches_left -= 1
        self.answered = self.answered or answered
        if self.answered or self.batches_left == 0:
            self.decided.set()

    async def grant(self, wanted: int) -> int:
        """How many of ``wanted`` blocks may be retried: 0 when no batch answered."""
        await self.decided.wait()
        if not self.answered:
            return 0
        granted = min(wanted, self.cap - self.retried)
        self.retried += granted
        self.skipped += wanted - granted
        return granted


async def _batch_answers(
    llm_service: LLMService, batch: list[str], source_language: str, slots: asyncio.Semaphore
) -> list[str | None]:
    """Each block's answer from its batch call, a failed call retried once as two
    halves; None where a block has no answer or got itself back."""
    result = await _call(llm_service, batch, source_language, slots)
    answers: list[str | None] = [*(result or [None] * len(batch))]
    if result is None and len(batch) > 1:
        mid = len(batch) // 2
        first, second = await asyncio.gather(
            _call(llm_service, batch[:mid], source_language, slots),
            _call(llm_service, batch[mid:], source_language, slots),
        )
        answers = [*(first or [None] * mid), *(second or [None] * (len(batch) - mid))]
    # An answer equal to its block is the source text kept, which the script
    # check cannot see for a Latin-script source.
    return [None if answer == text else answer for answer, text in zip(answers, batch)]


def _untranslated(answer: str | None) -> bool:
    """No English yet: no answer, or a Hebrew / Arabic / CJK character left in it."""
    return answer is None or has_hebrew_arabic_or_cjk(answer)


async def _translate_batch(
    llm_service: LLMService,
    batch: list[str],
    source_language: str,
    slots: asyncio.Semaphore,
    retries: _Retries,
) -> list[str | None]:
    """``batch`` in English, then each block still untranslated sent once more on
    its own (one string per call: no count to miss); an English answer replaces it."""
    answers = await _batch_answers(llm_service, batch, source_language, slots)
    retries.batch_returned(any(answer is not None for answer in answers))
    pending = [i for i, answer in enumerate(answers) if _untranslated(answer)]
    if not pending:
        return answers
    pending = pending[: await retries.grant(len(pending))]
    results = await asyncio.gather(
        *(_call(llm_service, [batch[i]], source_language, slots) for i in pending)
    )
    for i, result in zip(pending, results):
        if result is not None and not _untranslated(result[0]):
            answers[i] = result[0]
    return answers


def _log_retries(retries: _Retries, answers: list[str | None]) -> None:
    """The one line counting the single-block retries and what is still untranslated."""
    if not retries.retried and not retries.skipped:
        return
    left = sum(1 for answer in answers if _untranslated(answer))
    logger.log(
        logging.WARNING if left else logging.INFO,
        "RAG translation retried %d/%d blocks one by one (%d past the cap of %d not retried): "
        "%d still untranslated, kept as they were",
        retries.retried,
        len(answers),
        retries.skipped,
        retries.cap,
        left,
    )


async def _translate_texts(
    llm_service: LLMService, texts: list[str], source_language: str
) -> list[str] | None:
    """``texts`` in English, same length and order; a block without an answer keeps its text."""
    batches = char_batches(texts, TRANSLATION_BATCH_CHARS)
    # Each call takes a slot, not each batch: a batch retrying as two halves or
    # block by block holds none while it waits, so retries never push past
    # _CONCURRENCY.
    slots = asyncio.Semaphore(_CONCURRENCY)
    retries = _Retries(cap=max(_RETRY_MIN_BLOCKS, len(texts) // 2), batches_left=len(batches))
    results = await asyncio.gather(
        *(_translate_batch(llm_service, b, source_language, slots, retries) for b in batches)
    )
    answers = [answer for result in results for answer in result]
    if not retries.answered:
        logger.warning("RAG translation: no batch answered; %d blocks not retried", len(texts))
        return None
    _log_retries(retries, answers)
    return [text if answer is None else answer for text, answer in zip(texts, answers)]


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
