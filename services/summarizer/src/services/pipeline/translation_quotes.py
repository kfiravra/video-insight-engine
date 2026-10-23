"""The English beneath a non-English run's verbatim lines (output-quality 4.6c, D12, G4-1).

On a non-English run the page keeps the speaker's own words: a ``quote`` the quote pass
verified (no ``quoteTranslated`` flag) and an interview ``question`` the question check
verified (``questionStatus: "verified"``) ship in the transcript's language — on key
ideas, spots, moments, claims, the verdict, quote cards and asks rows. The English page
shows each with its English beneath (``quoteEn`` / ``questionEn``). The extraction model
wrote that English on one call of S15's five (2 / 29, G4-1), so it is written here
instead: one source→English pass in the translation phase, over the lines as they ship.

The pass is the flat translation call in the reverse direction
(``translation.translate_in_batches``: the same batches, half retry, echo check and
``LLM_TRANSLATION_MODEL``). The lines are the ones the EN→source walk keeps as they
are (``translation.is_verbatim_line``), so a line never gets both treatments. A line
said twice (a key idea's quote that is also a moment's) is sent once. A line the pass
could not translate (salvaged, echoed, or English already) gets no English. An exchange
row whose ``label`` was cut from its question is re-titled from the English, as
assembly titles a row from ``questionEn``. The quiz's evidence quotes are left alone:
the quiz card has no English line.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .assembly.moment_normalizers import QUOTE_EN_KEY, _question_label
from .enrichment import QUIZ_COMPONENT
from .translation import is_verbatim_line, translate_in_batches

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

_QUOTE_KEY = "quote"
_QUESTION_KEY = "question"
_QUESTION_EN_KEY = "questionEn"
_LABEL_KEY = "label"
# The line keys, each with the key its English is written under.
_ENGLISH_KEY_OF: Mapping[str, str] = {_QUOTE_KEY: QUOTE_EN_KEY, _QUESTION_KEY: _QUESTION_EN_KEY}
# Components whose cards show a quote with no English line under it.
_NO_ENGLISH_LINE = frozenset({QUIZ_COMPONENT})
_MAX_DEPTH = 32


@dataclass(frozen=True)
class VerbatimLine:
    """One source-language line the page shows: the dict holding it and its key."""

    holder: dict[str, Any]
    key: str
    text: str


def _line_in(holder: dict[str, Any], key: str) -> VerbatimLine | None:
    value = holder.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    if not is_verbatim_line(holder, key, value):
        return None
    return VerbatimLine(holder, key, value.strip())


def _collect(obj: Any, lines: list[VerbatimLine], depth: int) -> None:
    if depth > _MAX_DEPTH:
        return
    if isinstance(obj, list):
        for item in obj:
            _collect(item, lines, depth + 1)
        return
    if not isinstance(obj, dict) or obj.get("component") in _NO_ENGLISH_LINE:
        return
    lines.extend(line for key in _ENGLISH_KEY_OF if (line := _line_in(obj, key)) is not None)
    for value in obj.values():
        _collect(value, lines, depth + 1)


def verbatim_lines(output: Mapping[str, Any]) -> list[VerbatimLine]:
    """Every verbatim line of the English ``output`` ({tabs, meta}) that shows an English line."""
    lines: list[VerbatimLine] = []
    _collect([output.get("tabs"), output.get("meta")], lines, 0)
    return lines


def _is_english_of(line: str, english: str) -> bool:
    """Whether ``english`` is worth showing beneath ``line``: not blank, not the line."""
    return bool(english) and english != line


async def english_of_lines(
    llm_service: LLMService,
    lines: list[VerbatimLine],
    source_language: str,
) -> dict[str, str]:
    """Each distinct line's English, from one batched source→English pass; {} when
    there is no line or nothing came back translated."""
    texts = list(dict.fromkeys(line.text for line in lines))
    if not texts:
        return {}
    batched = await translate_in_batches(llm_service, texts, source_language, "en")
    if batched is None:
        logger.warning("Verbatim lines: no English for %d lines (%s)", len(texts), source_language)
        return {}
    english, salvaged = batched
    return {
        text: rendered.strip()
        for text, rendered, lost in zip(texts, english, salvaged)
        if not lost and _is_english_of(text, rendered.strip())
    }


def _set_question_english(holder: dict[str, Any], question: str, english: str) -> None:
    if holder.get(_LABEL_KEY) == _question_label(question):
        holder[_LABEL_KEY] = _question_label(english)
    holder[_QUESTION_EN_KEY] = english


def apply_english(lines: list[VerbatimLine], english: Mapping[str, str]) -> int:
    """Write ``quoteEn`` / ``questionEn`` beside each line that has its English, in
    place; returns how many lines got one."""
    written = 0
    for line in lines:
        rendered = english.get(line.text)
        if rendered is None:
            continue
        if line.key == _QUESTION_KEY:
            _set_question_english(line.holder, line.text, rendered)
        else:
            line.holder[_ENGLISH_KEY_OF[line.key]] = rendered
        written += 1
    logger.info("Verbatim lines: English beneath %d of %d", written, len(lines))
    return written
