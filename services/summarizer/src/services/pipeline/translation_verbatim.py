"""Which leaves of the English output are a speaker's own lines (D12, output-quality 4.2).

A line already in the source language never goes EN→source — the model could rewrite
the speaker's words, and their echo counts toward the mirror check. On a non-English
run a ``quote`` without the ``quoteTranslated`` flag is the quote pass's verbatim find,
and so is any other leaf of its dict that restates it — a quote flash card's ``front``,
an asks row's “``note``” (C-W1); a ``question`` the question check verified is the
host's own words (4.0 c), and so is its exchange row's ``label`` unless the question's
English titles it.

A flagged line is English prose and IS translated — unless it is not in Latin script.
The checks flag every line they cannot find on a non-English run, and since 4.2 the
extraction writes lines in the source language, so a Hebrew line the check missed (a
paraphrase, a slightly wrong line) is the speaker's language, never English: it stays
as it is in the source view and gets its English beneath like a found one (pC review,
S12's one bare Hebrew quote). A Latin-script source cannot be told apart this way, so
a flagged Spanish line still reads as English prose.

Both translation passes read these rules: the EN→source walk keeps the lines as they
are (``translation``), the source→English pass writes their English (``quoteEn`` /
``questionEn``, ``translation_quotes``), and that English leaves the source-language
copy (``drop_english_only``). Split out of ``translation`` (pC review: the file was at
the 500-line rule).
"""

from __future__ import annotations

from typing import Any

from .assembly.moment_normalizers import QUOTE_EN_KEY
from .assembly.qa_questions import QUESTION_STATUS_KEY
from .assembly.text_utils import in_latin_script
from .quote_verify import QUOTE_TRANSLATED_KEY, TRANSLATED, VERIFIED

_QUOTE_KEY = "quote"
# The marks an assembler wraps a restated quote in (``talk_asks``: “line”).
_QUOTE_MARKS = '“”"'
_QUESTION_KEY = "question"
_LABEL_KEY = "label"
_QUESTION_EN_KEY = "questionEn"
# The English renderings beside those lines: English already, and the source view
# shows the verbatim line alone, so they leave its copy.
ENGLISH_ONLY_KEYS: frozenset[str] = frozenset({QUOTE_EN_KEY, _QUESTION_EN_KEY})
_MAX_DEPTH = 32


def _in_source_language(line: str, flagged: bool) -> bool:
    """Whether a checked ``line`` is the speaker's: found by its check, or flagged and
    not in Latin script (module docstring)."""
    return not flagged or not in_latin_script(line)


def _restates_quote(parent: dict[str, Any], value: str) -> bool:
    """Whether ``value`` is ``parent``'s verbatim quote again, bare or in quotation marks."""
    quote = parent.get(_QUOTE_KEY)
    if not isinstance(quote, str):
        return False
    if not _in_source_language(quote, parent.get(QUOTE_TRANSLATED_KEY) is True):
        return False
    line = quote.strip()
    return bool(line) and value.strip().strip(_QUOTE_MARKS).strip() == line


def _verbatim_question(parent: dict[str, Any]) -> bool:
    """Whether ``parent``'s interview question is the host's line (verified, or kept as
    translated and not in Latin script)."""
    status = parent.get(QUESTION_STATUS_KEY)
    if status == VERIFIED:
        return True
    question = parent.get(_QUESTION_KEY)
    return status == TRANSLATED and isinstance(question, str) and not in_latin_script(question)


def is_verbatim_line(parent: dict[str, Any], key: str, value: str) -> bool:
    """Whether ``parent[key]`` (``value``) is a speaker's line in the source language (D12)."""
    if key == _QUOTE_KEY:
        return _in_source_language(value, parent.get(QUOTE_TRANSLATED_KEY) is True)
    if _restates_quote(parent, value):
        return True
    if not _verbatim_question(parent):
        return False
    return key == _QUESTION_KEY or (key == _LABEL_KEY and not parent.get(_QUESTION_EN_KEY))


def drop_english_only(obj: Any, depth: int = 0) -> None:
    """Remove every ``ENGLISH_ONLY_KEYS`` leaf from the source-language copy, in place."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(obj, list):
        for item in obj:
            drop_english_only(item, depth + 1)
    elif isinstance(obj, dict):
        for key in ENGLISH_ONLY_KEYS & obj.keys():
            del obj[key]
        for value in obj.values():
            drop_english_only(value, depth + 1)
