"""Reading quiz v2's reply (output-quality 3.0, G2-1) — a broken envelope costs no question.

Gate 2 lost S02's quiz: Haiku closed its ten valid questions with ``"57:40"}}]}`` — one
brace too many — so the envelope parse (``parse_json_response``) found no object and
the quiz went with it ("kept 0 of 0"). :func:`quiz_items` reads the reply both ways and
keeps the longer list:

- the envelope: ``{"quiz": [...]}`` parsed whole;
- the salvage (:func:`salvage_quiz_items`): from the ``quiz`` list's ``[`` (or a reply
  that is the bare list), one item
  object at a time (``json.JSONDecoder.raw_decode``), stepping over the commas,
  whitespace and stray ``}`` between them; a ``]`` there ends the list, and an item
  that does not decode (cut off, a quote unescaped) is skipped alone to the next ``{``.

Whatever the salvage finds still goes through ``enrichment.parse_quiz``, so a decoded
object that is no valid question is dropped there, as it is from a whole reply.
"""

from __future__ import annotations

import json
import logging
import re

from ...utils.json_parsing import parse_json_response, strip_markdown_fences

logger = logging.getLogger(__name__)

QUIZ_KEY = "quiz"
_QUIZ_LIST_RE = re.compile(r'"quiz"\s*:\s*\[')
# Between two items: the separator, and the stray closers a model adds (G2-1's "}}").
_BETWEEN_ITEMS = frozenset(" \t\r\n,}")
_LIST_END = "]"
_ITEM_START = "{"


# ─── Salvage ───


def _list_start(text: str) -> int:
    """Where the quiz list's items begin (just past its ``[``); -1 when there is none."""
    match = _QUIZ_LIST_RE.search(text)
    if match is not None:
        return match.end()
    return 1 if text.startswith("[") else -1


def _next_item(text: str, pos: int) -> int:
    """The next item's ``{`` at or after ``pos``; -1 at the list's ``]`` or the text's end."""
    while pos < len(text) and text[pos] in _BETWEEN_ITEMS:
        pos += 1
    if pos >= len(text) or text[pos] == _LIST_END:
        return -1
    return pos if text[pos] == _ITEM_START else text.find(_ITEM_START, pos)


def salvage_quiz_items(raw: str) -> list[object]:
    """Every item object of the reply's ``quiz`` list that decodes on its own, in order."""
    text = strip_markdown_fences(raw)
    decoder = json.JSONDecoder(strict=False)
    items: list[object] = []
    pos = _list_start(text)
    while pos >= 0 and (pos := _next_item(text, pos)) >= 0:
        try:
            item, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            pos = text.find(_ITEM_START, pos + 1)
            continue
        items.append(item)
    return items


# ─── The reply ───


def _envelope_items(raw: str) -> list[object]:
    """The ``quiz`` list of a reply that parses whole; ``[]`` when it does not."""
    items = parse_json_response(raw).get(QUIZ_KEY)
    return items if isinstance(items, list) else []


def quiz_items(raw: str) -> list[object]:
    """The reply's quiz items: the envelope's list, or the salvage when it reads more."""
    items = _envelope_items(raw)
    salvaged = salvage_quiz_items(raw)
    if len(salvaged) <= len(items):
        return items
    logger.warning(
        "Quiz v2: the reply's envelope gave %d item(s); salvaged %d one by one",
        len(items), len(salvaged),
    )  # fmt: skip
    return salvaged
