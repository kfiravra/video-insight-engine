"""Quiz answer times in seconds, and no bare second count in the prose (output-quality 2.0 a).

The quiz prompt asks for ``timestamp`` in seconds and for the explanation to cite
the same time once as ``(m:ss)``. S03's gate-1 quiz stored MINUTES (3, 20, 15 for
answers its explanations place at 3:22, 20:02, 15:01 — seeks to 0:03 / 0:20 / 0:15)
and printed one time as a bare "(1322)" (G1-1). The rules:

- a time cue in the explanation — ``(m:ss)``, ``[m:ss]`` or a bare ``(1322)`` — that
  lies inside the video is rendered as ``(m:ss)``; one past the end (when the
  length is known) is removed with its "at" and comma, so a bare second count
  never reaches the prose;
- a stamp that reads as a cited clock's minutes (``stamp == clock // 60``) is a unit
  error and takes that clock; a stamp past the video's end takes the first cited
  clock;
- any other stamp stands — also against a clock it disagrees with. Checked on the
  13 gate-1 docs against their transcripts: in all 11 such disagreements (S01 ×7,
  S02, S03, S05, S08) the stamp sat on the answer — or nearest it (S02: 77 s
  before, its clock 11 min off) — and the explanation's clock did not (S03's 382 =
  6:22, "what's going on in your body can dramatically influence … your brain",
  cited as "(3:22)");
- the prose follows the stamp: an explanation citing one clock more than
  ``CLOCK_AGREEMENT_S`` away from an in-video stamp cites the stamp's clock instead,
  so the seek chip and the sentence name the same time.

A stampless question stays stampless; an explanation without a clock leaves the
stamp alone. A stamp past the end with no clock is the caller's: ``parse_quiz``
drops the question (output-quality 1.13), assembly clears the stamp
(``item_timestamps``). Applying the rules twice changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.utils.data_helpers import parse_timestamp_to_seconds

from .derived_guard import anchor_seconds
from .moment_normalizers import _seconds_to_time_str

# A stamp this close to a cited clock points at the same sentence; one further off
# makes the prose cite the stamp's clock.
CLOCK_AGREEMENT_S = 10
# A time this far past the reported length is drift (rounded durations), not an
# invention — the slack parse_quiz and assembly give a stamp past the end.
END_SLACK_S = 10.0

# "(3:22)", "[1:02:05]" or a bare "(1322)" — the prompt's "(m:ss)" slot — with the
# "at" / "around" leading into it and the comma after it, which go when it goes. A
# bare number counts only in parentheses ("[3]" is a citation, not a time).
_CLOCK = r"\d{1,3}(?::[0-5]\d){1,2}"
_TIME_CUE_RE = re.compile(
    r"(?P<lead>\b(?:at|around|near|from|by)\s+)?"
    rf"(?:\(\s*(?P<paren>{_CLOCK}|\d{{1,5}})\s*\)|\[\s*(?P<bracket>{_CLOCK})\s*\])"
    r"(?P<comma>\s*,)?",
    re.IGNORECASE,
)
_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+(?=[,.;:!?])")


def _cue_seconds(cue: str) -> int | None:
    return int(cue) if cue.isdigit() else parse_timestamp_to_seconds(cue)


def _inside(seconds: int, duration_s: float | None) -> bool:
    return not duration_s or duration_s <= 0 or seconds <= duration_s + END_SLACK_S


def _tidy(text: str, removed_at_start: bool) -> str:
    """Whitespace and punctuation left by a removed cue; a capital where it opened the text."""
    text = _SPACE_BEFORE_PUNCT_RE.sub("", _SPACE_RUN_RE.sub(" ", text)).strip()
    return text[:1].upper() + text[1:] if removed_at_start else text


def render_time_cues(explanation: str, duration_s: float | None) -> tuple[str, list[int]]:
    """``explanation`` with every time cue as ``(m:ss)`` or removed, and the seconds it
    still cites."""
    clocks: list[int] = []
    removed_at_start = False

    def _render(match: re.Match[str]) -> str:
        nonlocal removed_at_start
        seconds = _cue_seconds(match.group("paren") or match.group("bracket"))
        if seconds is None or not _inside(seconds, duration_s):
            removed_at_start = removed_at_start or not explanation[: match.start()].strip()
            return " "
        clocks.append(seconds)
        lead, comma = match.group("lead") or "", match.group("comma") or ""
        return f"{lead}({_seconds_to_time_str(seconds)}){comma}"

    rendered = _TIME_CUE_RE.sub(_render, explanation)
    if rendered == explanation:
        return explanation, clocks
    tidy = _tidy(rendered, removed_at_start)
    # A cue that was all of the explanation leaves it as it was, never blank.
    return (tidy or explanation), clocks


def _stamp_for(stamp: int | None, clocks: list[int], duration_s: float | None) -> int | None:
    """The unit error (minutes for seconds) and the impossible stamp yield to a cited clock."""
    if stamp is None or not clocks:
        return stamp
    if any(abs(stamp - clock) <= CLOCK_AGREEMENT_S for clock in clocks):
        return stamp
    minutes = next((clock for clock in clocks if clock // 60 == stamp), None)
    if minutes is not None:
        return minutes
    return stamp if _inside(stamp, duration_s) else clocks[0]


def _cite_stamp(text: str, clocks: list[int], stamp: int | None, duration_s: float | None) -> str:
    """The explanation's one clock replaced by the stamp's when they name different times."""
    if stamp is None or len(clocks) != 1 or not _inside(stamp, duration_s):
        return text
    if abs(stamp - clocks[0]) <= CLOCK_AGREEMENT_S:
        return text
    cited = f"({_seconds_to_time_str(clocks[0])})"
    return text.replace(cited, f"({_seconds_to_time_str(stamp)})", 1)


def reconcile_quiz_time(
    stamp: int | None, explanation: str, duration_s: float | None = None
) -> tuple[int | None, str]:
    """The question's stamp checked against its explanation's clocks, and the explanation
    with every time cue rendered and citing the stamp — the module docstring's rules."""
    text, clocks = render_time_cues(explanation, duration_s)
    reconciled = _stamp_for(stamp, clocks, duration_s)
    return reconciled, _cite_stamp(text, clocks, reconciled, duration_s)


def reconcile_quiz_item(item: Any, duration_s: float | None) -> Any:
    """A stored quiz question with its stamp and explanation reconciled (a copy when either
    changed); anything that is not a question dict comes back as it was."""
    if not isinstance(item, Mapping) or not isinstance(item.get("explanation"), str):
        return item
    stamp = anchor_seconds(dict(item))
    new_stamp, text = reconcile_quiz_time(stamp, item["explanation"], duration_s)
    if new_stamp == stamp and text == item["explanation"]:
        return item
    return {**item, "timestamp": new_stamp, "explanation": text}


def with_reconciled_quiz(enrichment: Any, duration_s: float | None) -> Any:
    """``enrichment`` with its quiz questions reconciled against the video's length — a
    copy, so the stored enrichment record keeps what the model wrote."""
    quiz = enrichment.get("quiz") if isinstance(enrichment, Mapping) else None
    if not isinstance(quiz, list):
        return enrichment
    return {**enrichment, "quiz": [reconcile_quiz_item(item, duration_s) for item in quiz]}
