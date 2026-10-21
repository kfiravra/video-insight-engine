"""Hero rewrite calls answered from the recorded memory + synthesis (output-quality 2.2).

The cassettes predate the hero rewrite: they hold memory's hero (entry M) and the
gpt-4o-mini synthesis (entry Y), never a ``summarize:hero_rewrite`` call. With
``HERO_REWRITE_ENABLED`` the pipeline asks for one (span ``hero_rewrite``) in place
of Y; it is answered from M + Y and the request itself, so the replay runs the real
parse, validation and wiring:

* tldr = M's, else Y's, else a plain line; brief = Y's masterSummary sentences, then
  M's takeaways as sentences, then plain lines — 3 to 5 of them; takeaways = M's,
  Y's, then the item lines of the request's ``<extracted>``, as many as the job asks for,
  each citing a real id of that block in ``ref`` (code copies the item's stamp; a
  block without ids gets an ``m:ss`` inside the video instead); worthItFor / skipIf =
  plain lines; seoDescription = Y's, else the tldr;
* recorded text is used only where the checks cannot fail it: no digit (the request
  holds the evidence only in part), no page word, no tab label from the job's list —
  the extracted lines' own numbers are the evidence, so they stay;
* the entry answers on the extraction entry's model (the hero runs on
  ``LLM_EXTRACTION_MODEL``); usage: input = request chars / 3.6, output = Y's tokens
  scaled by the answer's length, wall = Y's.

Y counts as used: the hero call stands in for it one for one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.services.pipeline.hero_text import HeroTabNames, sentences_of, tab_names
from src.services.pipeline.memory import format_clock
from src.utils.data_helpers import parse_timestamp_to_seconds
from tests.replay.cassette import LLMEntry, LLMKey
from tests.replay.group_synth import CHARS_PER_TOKEN, recorded_answer
from tests.replay.quiz_synth import request_text

SYNTHESIS_KEY = LLMKey(feature="summarize:synthesis", span="synthesis", ordinal=0)
HERO_FEATURE = "summarize:hero_rewrite"
HERO_SPAN = "hero_rewrite"
_TLDR_MAX_CHARS = 150
_BRIEF_SENTENCES = (3, 5)

_JOB_RE = re.compile(r"<hero_job>(.*)</hero_job>", re.DOTALL)
_COUNT_RE = re.compile(r"exactly (\d+) for this ([\d:]+|unknown-length) video")
_EXTRACTED_RE = re.compile(r"<extracted>\n(.*?)\n</extracted>", re.DOTALL)
# An item line: "K12 [1:00] text" (stamped, cited by id) or "- text".
_ITEM_LINE_RE = re.compile(r"^\s*(?:([A-Z]\d+) \[[\d:]+\]|-) (.+)$")
_TABS_RE = re.compile(r"never name them\):\n(.*?)\s*$", re.DOTALL)
_DIGIT_RE = re.compile(r"\d")
_PAGE_WORD_RE = re.compile(r"\b(?:tabs?|sections?|pages?|check|explore)\b", re.I)
_MARKER_RE = re.compile(r"\s*\[(?:\d+:)?\d{1,2}:\d{2}\]\s*$")

_PLAIN_TLDR = "The video makes its case in its own order, one point at a time."
_PLAIN_SENTENCES = (
    "It opens on the question the video sets out to answer.",
    "Each step of the argument rests on what the speaker shows.",
    "It closes on the point the video set out to make.",
)
_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh")
_WORTH_IT_FOR = (
    "Viewers who want the whole argument as the video makes it, start to finish.",
    "Anyone who wants the reasoning behind each claim, not only the conclusion.",
)
_SKIP_IF = (
    "You only want a quick answer, without the reasoning behind it.",
    "You came for a different subject than the one the video argues.",
)


@dataclass(frozen=True)
class HeroJob:
    """What the request's ``<hero_job>`` asks for and offers."""

    takeaways: int
    duration: int | None
    ids: tuple[str, ...]
    lines: tuple[str, ...]
    tabs: HeroTabNames


def is_hero_call(key: LLMKey) -> bool:
    return key.feature == HERO_FEATURE and key.span == HERO_SPAN


# ─── The request ───


def read_job(request: str) -> HeroJob:
    """The takeaway count, the video's length, the extracted ids + lines and the tab labels."""
    job_match = _JOB_RE.search(request)
    job = job_match.group(1) if job_match else ""
    count = _COUNT_RE.search(job)
    extracted = _EXTRACTED_RE.search(job)
    rows = [
        m
        for line in (extracted.group(1) if extracted else "").splitlines()
        if (m := _ITEM_LINE_RE.match(line))
    ]
    tabs = _TABS_RE.search(job)
    labels = [
        line[2:] for line in (tabs.group(1) if tabs else "").splitlines() if line.startswith("- ")
    ]
    return HeroJob(
        takeaways=int(count.group(1)) if count else len(_PLAIN_SENTENCES),
        duration=parse_timestamp_to_seconds(count.group(2)) if count else None,
        ids=tuple(m.group(1) for m in rows if m.group(1)),
        lines=tuple(m.group(2).strip() for m in rows),
        tabs=tab_names(labels),
    )


# ─── The answer ───


def _plain(text: str, job: HeroJob) -> bool:
    """Recorded text the checks cannot fail: no digit, no page word, no tab named."""
    return bool(text) and not (
        _DIGIT_RE.search(text) or _PAGE_WORD_RE.search(text) or job.tabs.named_in(text)
    )


def _strings(value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [_MARKER_RE.sub("", " ".join(v.split())) for v in items if isinstance(v, str)]


def _as_sentence(text: str) -> str:
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


def _brief(memory: Mapping[str, Any], synthesis: Mapping[str, Any], job: HeroJob) -> str:
    recorded = [s for text in _strings(synthesis.get("masterSummary")) for s in sentences_of(text)]
    recorded += [_as_sentence(t) for t in _strings(memory.get("takeaways"))]
    kept = [_as_sentence(s) for s in recorded if _plain(s, job)]
    low, high = _BRIEF_SENTENCES
    return " ".join([*kept, *_PLAIN_SENTENCES[: max(low - len(kept), 0)]][:high])


def _takeaway(index: int, text: str, job: HeroJob) -> dict[str, str]:
    if job.ids:
        return {"text": text, "ref": job.ids[index % len(job.ids)]}
    length = job.duration or 30 * (job.takeaways + 1)
    return {"text": text, "timestamp": format_clock(length * (index + 1) // (job.takeaways + 1))}


def _takeaways(
    memory: Mapping[str, Any], synthesis: Mapping[str, Any], job: HeroJob
) -> list[dict[str, str]]:
    recorded = _strings(memory.get("takeaways")) + _strings(synthesis.get("keyTakeaways"))
    fillers = [f"The {ordinal} point stands on what the video shows." for ordinal in _ORDINALS]
    texts: list[str] = []
    for text in [*(t for t in recorded if _plain(t, job)), *job.lines, *fillers]:
        if text.casefold() not in {t.casefold() for t in texts}:
            texts.append(text)
    return [_takeaway(i, text, job) for i, text in enumerate(texts[: job.takeaways])]


def _first_plain(candidates: Sequence[str], job: HeroJob, limit: int | None = None) -> str:
    fits = (c for c in candidates if _plain(c, job) and (limit is None or len(c) <= limit))
    return next(fits, "")


def hero_answer(memory: Mapping[str, Any], synthesis: Mapping[str, Any], job: HeroJob) -> dict:
    """The hero JSON for ``job`` from M + Y (module docstring)."""
    tldrs = [*_strings(memory.get("tldr")), *_strings(synthesis.get("tldr"))]
    tldr = _first_plain(tldrs, job, _TLDR_MAX_CHARS) or _PLAIN_TLDR
    seo = _first_plain(_strings(synthesis.get("seoDescription")), job) or tldr
    return {
        "tldr": tldr,
        "brief": _brief(memory, synthesis, job),
        "keyTakeaways": _takeaways(memory, synthesis, job),
        "worthItFor": _first_plain(_WORTH_IT_FOR, job),
        "skipIf": _first_plain(_SKIP_IF, job),
        "seoDescription": seo,
    }


# ─── The entry ───


def _answer_of(entry: LLMEntry | None) -> Mapping[str, Any]:
    return recorded_answer(entry) if entry is not None else {}


def synthesize_hero(
    entries: Mapping[LLMKey, LLMEntry],
    memory_key: LLMKey,
    extraction_key: LLMKey,
    key: LLMKey,
    messages: Sequence[Mapping[str, Any]],
) -> LLMEntry:
    """The hero answer in the recorded-entry shape (module docstring)."""
    request = request_text(messages)
    synthesis = entries.get(SYNTHESIS_KEY)
    answer = hero_answer(
        _answer_of(entries.get(memory_key)), _answer_of(synthesis), read_job(request)
    )
    output = json.dumps(answer)
    model_entry = entries.get(extraction_key) or synthesis
    recorded_out = synthesis.output if synthesis is not None else output
    share = len(output) / max(len(recorded_out), 1)
    return LLMEntry(
        key=key,
        model=model_entry.model if model_entry is not None else "",
        latency_ms=synthesis.latency_ms if synthesis is not None else 0,
        output=output,
        finish_reason="stop",
        input_tokens=round(len(request) / CHARS_PER_TOKEN),
        output_tokens=max(1, round((synthesis.output_tokens if synthesis else 1) * share)),
    )
