"""Quiz v2's targets on the answer side (output-quality 4.0 b): which key idea each
question tests, and where a translated question sits.

Each call shows its target ideas with ids (``quiz_v2_ideas``) and asks every question to
name the one it tests in ``"idea"``. Code holds the questions to it:

- :func:`candidates` reads each reply item's ``idea`` before the parse drops the key; an
  id counts only when it is one of that call's targets;
- :func:`aimed` gives a question with no valid id the target stamped within
  ``KEY_IDEA_WINDOW_S`` (90 s) of its checked stamp; with none the question is dropped
  (``dropped_untargeted``) — a call shown no target (a run without key ideas) keeps
  every question, as before;
- :func:`ideas_first` ranks a second question on one idea behind every first one, and
  :func:`repeats` names it to ``quiz_v2_select.select_quiz``: it never takes a part's or
  a section's slot and ships only when the firsts run out, else it rides on as a spare
  (review p4 B-W2: a repeat that alone covered a part shipped while a first waited);
- :func:`translated_place`: a translated quote (a Hebrew talk's English rendering) is
  never found in the transcript, so its question used to keep the model's stamp — S15's
  third-stretch call copied the hero's 1:20 / 27:20 / 44:01 into its 47:35–1:11:22
  stretch (G3-3). Its stamp is now its idea's location: the idea's own quote where the
  index finds it (a Hebrew key idea quotes the Hebrew and verifies), else the idea's
  stamp; with neither, the model's stamp stands only inside the call's part
  (± ``STRETCH_MARGIN_S``), else the question is dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ...models.pipeline_types import QuizQuestion
from .assembly.quiz_stamps import END_SLACK_S
from .quiz_v2_ideas import TARGET_ID_PREFIX, QuizTarget
from .quiz_v2_prompt import KEY_IDEA_WINDOW_S, STRETCH_MARGIN_S
from .quiz_v2_select import Part
from .quote_verify import VERIFIED, QuoteIndex, verify_quote

IDEA_KEY = "idea"
_QUESTION_KEY = "question"


@dataclass(frozen=True)
class Candidate:
    """One parsed question with its call's part (None = the whole video) and targets, the
    target it tests, and whether its call named that target itself."""

    question: QuizQuestion
    part: Part | None = None
    targets: tuple[QuizTarget, ...] = ()
    target: QuizTarget | None = None
    named: bool = False


# ─── The reply's ids ───


def _idea_id(value: object) -> str:
    """``"K3"`` from ``"K3"`` / ``" k3 "`` / ``3``; ``""`` for anything else."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return ""
    text = str(value).strip().upper()
    return f"{TARGET_ID_PREFIX}{text}" if text.isdigit() else text


def named_ideas(items: Sequence[object]) -> dict[str, str]:
    """Each reply item's ``idea`` id by its question's text, casefolded."""
    named: dict[str, str] = {}
    for item in items:
        if isinstance(item, Mapping) and isinstance(item.get(_QUESTION_KEY), str):
            key = str(item[_QUESTION_KEY]).strip().casefold()
            named.setdefault(key, _idea_id(item.get(IDEA_KEY)))
    return named


def candidates(
    questions: Sequence[QuizQuestion],
    items: Sequence[object],
    part: Part | None,
    targets: Sequence[QuizTarget],
) -> list[Candidate]:
    """One call's parsed questions, each with the target it names when that is one of the
    call's own."""
    named = named_ideas(items)
    shown = tuple(targets)
    by_id = {target.id: target for target in shown}
    found = [by_id.get(named.get(question.question.casefold(), "")) for question in questions]
    return [
        Candidate(question, part, shown, target, named=target is not None)
        for question, target in zip(questions, found, strict=True)
    ]


# ─── Which idea, where ───


def nearest_target(stamp: int | None, targets: Sequence[QuizTarget]) -> QuizTarget | None:
    """The target stamped nearest ``stamp`` within :data:`KEY_IDEA_WINDOW_S`; None without."""
    if stamp is None:
        return None
    near = [
        t for t in targets if t.seconds is not None and abs(t.seconds - stamp) <= KEY_IDEA_WINDOW_S
    ]
    return min(near, key=lambda t: abs((t.seconds or 0) - stamp), default=None)


def inside_part(stamp: int | None, part: Part | None, duration_s: float | None) -> bool:
    """True when ``stamp`` lies in ``part`` ± :data:`STRETCH_MARGIN_S` — in the video
    (plus the end slack) for a whole-video call."""
    if stamp is None:
        return False
    if part is None:
        return not duration_s or duration_s <= 0 or stamp <= duration_s + END_SLACK_S
    return part[0] - STRETCH_MARGIN_S <= stamp <= part[1] + STRETCH_MARGIN_S


def idea_location(target: QuizTarget, index: QuoteIndex) -> int | None:
    """Where the idea is said: its own quote's place when the index finds it, else its stamp."""
    if target.quote:
        verdict = verify_quote(target.quote, index, near_seconds=target.seconds)
        if verdict.status == VERIFIED and verdict.seconds is not None:
            return verdict.seconds
    return target.seconds


def translated_place(
    candidate: Candidate, index: QuoteIndex, duration_s: float | None
) -> Candidate | None:
    """A translated question stamped at its idea's location; else at the model's stamp when
    that lies in its part; else None (module docstring)."""
    stamp = candidate.question.timestamp
    inside = inside_part(stamp, candidate.part, duration_s)
    target = candidate.target or (nearest_target(stamp, candidate.targets) if inside else None)
    location = idea_location(target, index) if target is not None else None
    if location is None and not inside:
        return None
    placed = stamp if location is None else location
    question = candidate.question.model_copy(update={"timestamp": placed})
    return replace(candidate, question=question, target=target)


def aimed(candidate: Candidate) -> Candidate | None:
    """The candidate with its target: its own, else the one stamped near its checked stamp;
    None when its call showed targets and none fits (module docstring)."""
    if candidate.target is not None or not candidate.targets:
        return candidate
    target = nearest_target(candidate.question.timestamp, candidate.targets)
    return None if target is None else replace(candidate, target=target)


def repeats(checked: Sequence[Candidate]) -> frozenset[int]:
    """The positions in ``checked`` of every second question on an idea (an earlier one
    tests the same target)."""
    tested: set[str] = set()
    second: set[int] = set()
    for position, candidate in enumerate(checked):
        idea = candidate.target.id if candidate.target is not None else ""
        if idea in tested:
            second.add(position)
        elif idea:
            tested.add(idea)
    return frozenset(second)


def ideas_first(checked: Sequence[Candidate]) -> list[Candidate]:
    """``checked`` in order, every second question on an idea moved behind every first."""
    second = repeats(checked)
    firsts = [candidate for position, candidate in enumerate(checked) if position not in second]
    return firsts + [checked[position] for position in sorted(second)]
