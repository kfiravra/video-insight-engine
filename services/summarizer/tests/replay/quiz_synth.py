"""Quiz v2 calls answered from the recorded quiz (output-quality 2.1b; the group_synth idea).

The cassettes were recorded with the flag-off quiz call (entry Q, ``enrichment``
span) — no ``quote`` on any question. With ``QUIZ_V2_ENABLED`` the pipeline asks
under the ``quiz_v2`` span instead; that call is never recorded and is answered
from Q, so the replay exercises the real v2 parse and quote check:

* content = Q's quiz, the same questions, each with a ``timestamp`` and a ``quote``:
  the recorded stamp when Q has one, else the question's evenly spaced place in the
  video (Q predates stamps); the quote = the first ``QUOTE_WORDS`` words of the
  ``[m:ss]`` line of the request's ``<transcript>`` that holds that second, and the
  stamp = that line's marker. A recorded item that is not a question passes as is
  (the parser drops it as it did);
* a stretch call (output-quality 3.0 — its task names "this stretch of the video:
  a–b (the video runs 0:00–L)") answers only the questions whose place lies in its
  stretch, so every recorded question is answered by one call; the places are spread
  over L, the video's length;
* the entry answers on the model the recorded memory call ran on — quiz v2 runs on
  memory's model (``LLM_MEMORY_MODEL``, blank → memory's fallback);
* usage: input = request chars / 3.6, output = Q's tokens scaled by the answer's
  length, wall = Q's.

Q counts as used: the v2 call stands in for it one for one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from tests.replay.cassette import LLMEntry, LLMKey
from tests.replay.group_synth import CHARS_PER_TOKEN, recorded_answer

QUIZ_KEY = LLMKey(feature="summarize:enrichment", span="enrichment", ordinal=0)
MEMORY_KEY = LLMKey(feature="summarize:memory", span="memory", ordinal=0)
QUIZ_V2_SPAN = "quiz_v2"
QUOTE_WORDS = 20

_TRANSCRIPT_RE = re.compile(r"<transcript>\n(.*?)\n</transcript>", re.DOTALL)
_LINE_RE = re.compile(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})\] (.+)$")
_CLOCK = r"\d{1,2}(?::\d{2}){1,2}"
_STRETCH_RE = re.compile(
    rf"inside this stretch of the video: ({_CLOCK})–({_CLOCK}) \(the video runs 0:00–({_CLOCK})\)"
)


def is_quiz_v2_call(key: LLMKey) -> bool:
    return key.feature == QUIZ_KEY.feature and key.span == QUIZ_V2_SPAN


def request_text(messages: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"text": str(content or "")}]
        parts.extend(str(block.get("text", "")) for block in blocks)
    return "\n".join(parts)


def transcript_lines(request: str) -> list[tuple[int, str]]:
    """(seconds, text) of every ``[m:ss]`` line in the request's ``<transcript>``."""
    match = _TRANSCRIPT_RE.search(request)
    lines: list[tuple[int, str]] = []
    for line in match.group(1).splitlines() if match else []:
        parsed = _LINE_RE.match(line)
        if parsed:
            hours, minutes, seconds, text = parsed.groups()
            lines.append((int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds), text))
    return lines


def _seconds(clock: str) -> int:
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total


def stretch_of_request(request: str) -> tuple[int, int, int] | None:
    """``(start, end, video length)`` seconds of a stretch call; None for a whole-video call."""
    match = _STRETCH_RE.search(request)
    if match is None:
        return None
    start, end, length = (_seconds(clock) for clock in match.groups())
    return start, end, length


def _line_at(lines: Sequence[tuple[int, str]], seconds: int) -> tuple[int, str]:
    """The line holding ``seconds``: the last one starting at or before it."""
    held = [line for line in lines if line[0] <= seconds]
    return held[-1] if held else lines[0]


def _place(item: Mapping[str, Any], position: int, total: int, last: int) -> int:
    stamp = item.get("timestamp")
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        return stamp
    return round(last * (position + 0.5) / max(total, 1))


def _in_stretch(place: int, stretch: tuple[int, int, int] | None) -> bool:
    if stretch is None:
        return True
    start, end, length = stretch
    return start <= place < end or (end == length and place >= end)


def quiz_v2_answer(
    recorded: Mapping[str, Any],
    lines: Sequence[tuple[int, str]],
    stretch: tuple[int, int, int] | None = None,
) -> dict:
    """Q's quiz with a stamp and a quote per question — a stretch call's only those placed
    in its stretch (module docstring)."""
    quiz = recorded.get("quiz")
    items: list[Any] = quiz if isinstance(quiz, list) else []
    total = sum(1 for item in items if isinstance(item, dict))
    last = stretch[2] if stretch else (lines[-1][0] if lines else 0)
    answered: list[Any] = []
    for position, item in enumerate(item for item in items if isinstance(item, dict)):
        place = _place(item, position, total, last)
        if lines and _in_stretch(place, stretch):
            start, text = _line_at(lines, place)
            answered.append(
                {**item, "timestamp": start, "quote": " ".join(text.split()[:QUOTE_WORDS])}
            )
    answered.extend(item for item in items if not isinstance(item, dict))
    return {"quiz": answered}


def synthesize_quiz_v2(
    quiz: LLMEntry, memory: LLMEntry | None, key: LLMKey, messages: Sequence[Mapping[str, Any]]
) -> LLMEntry:
    """The v2 answer in the recorded-entry shape (module docstring)."""
    request = request_text(messages)
    answer = quiz_v2_answer(
        recorded_answer(quiz), transcript_lines(request), stretch_of_request(request)
    )
    output = json.dumps(answer)
    share = len(output) / max(len(quiz.output), 1)
    return LLMEntry(
        key=key,
        model=memory.model if memory is not None else quiz.model,
        latency_ms=quiz.latency_ms,
        output=output,
        finish_reason="stop",
        input_tokens=round(len(request) / CHARS_PER_TOKEN),
        output_tokens=max(1, round(quiz.output_tokens * share)),
    )
