"""Which checked quiz v2 questions ship, and where each answer sits (output-quality 3.0).

Runs after the quote check (``quiz_v2.keep_checked``), over the checked questions in
the model's order — most important key idea first:

- **the shuffle** (G2-2): gate 2 keyed option 0 on 22 of 25 questions, so a reader
  could score without watching. :func:`shuffle_quiz` deals the shipped questions' key
  positions from a shuffled deck holding each of the :data:`QUIZ_OPTIONS` positions
  equally often — in a quiz of k questions no position keys more than ⌈k / 4⌉ (a
  3-question quiz: three different positions) — and shuffles the wrong options around
  each key. The generator is seeded with a SHA-256 digest of the questions' texts, so
  the same questions get the same order on every run and in every process (never
  Python's salted ``hash()``); ``correctIndex`` moves with its option, so
  ``quiz_v2.keyed_option`` reads the same answer before and after. The spares are
  dealt as a quiz of their own. (One seed per question text left 14 of gate 2's 25 on
  one position, and gives a 3-question quiz two keys on one position 5 times in 8.)
- **the length tell** (G2-5): a keyed option longer than :data:`LENGTH_TELL_RATIO`
  times the mean wrong option gives the answer away (gate 2: the key was the longest
  option on S01, S03, S08). :func:`length_tell` flags it; a flagged question ranks
  after every clean one, so the spares replace it and it ships only when nothing clean
  is left;
- **the spread** (G2-4): gate 2 shipped S03's 8 questions all inside 0:00–21:37 of a
  57-minute lecture. The video is cut into :data:`QUIZ_STRETCHES` stretches (thirds,
  :func:`stretch_bounds`); past the 3-question band each stretch holding a key idea gets
  its own call, sized by :func:`stretch_quotas` (``quiz_v2_prompt``), and
  :func:`select_quiz` ships the first clean question of each stretch, then fills the
  count in rank order.

The shipped questions keep the model's importance order, not the video's: the prompt
asks for the most important first, and the late refill (``phases.enrichment.
settle_late_quiz``) appends spares at the end — an order only importance keeps true.
A question that refill drops (it restates a shipped takeaway) is replaced by the first
clean spare from its own stretch, else the next spare (:func:`spare_for`), so the drop
does not leave its third of the video untested; the spare's key moves to the dropped
question's position (:func:`in_slot_of`), so the shipped quiz keeps its even deal (the
spares' own deck put 2 of 3 keys on one position in half of the 3-question refills).
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections.abc import Mapping, Sequence
from itertools import pairwise
from statistics import fmean
from typing import Any

from ...models.pipeline_types import QuizQuestion

logger = logging.getLogger(__name__)

# Thirds: the fewest stretches the shortest quiz (3 questions) still covers one by
# one, and the split gate 2's S03 check reads ("the last 36 minutes are untested").
QUIZ_STRETCHES = 3
# A key this many times the mean wrong option's length (characters) is the tell the
# gate-2 reviewers named; the prompt asks for every wrong option within 2 words of the
# key's length.
LENGTH_TELL_RATIO = 1.5
# The prompt's four options: the key positions a quiz deals out evenly (a question
# with fewer options takes its slot modulo its own count).
QUIZ_OPTIONS = 4


# ─── The shuffle (G2-2) ───


def _seeded(questions: Sequence[QuizQuestion]) -> random.Random:
    """A generator fixed by the questions' texts, in order (module docstring)."""
    texts = "\x00".join(question.question for question in questions)
    return random.Random(int.from_bytes(hashlib.sha256(texts.encode()).digest(), "big"))


def _key_slots(count: int, rng: random.Random) -> list[int]:
    """``count`` key positions, each position dealt at most ⌈count / 4⌉ times."""
    deck = list(range(QUIZ_OPTIONS)) * -(-count // QUIZ_OPTIONS)
    rng.shuffle(deck)
    return deck[:count]


def _placed(question: QuizQuestion, slot: int, rng: random.Random) -> QuizQuestion:
    """The question with its keyed option at ``slot`` and the wrong ones shuffled around it."""
    slot %= len(question.options)
    key = question.options[question.correct_index]
    wrong = [option for i, option in enumerate(question.options) if i != question.correct_index]
    rng.shuffle(wrong)
    options = [*wrong[:slot], key, *wrong[slot:]]
    return question.model_copy(update={"options": options, "correct_index": slot})


def shuffle_quiz(questions: Sequence[QuizQuestion]) -> list[QuizQuestion]:
    """The questions, in order, each with its options reordered and ``correctIndex``
    moved with the keyed option — key positions dealt evenly (module docstring)."""
    rng = _seeded(questions)
    slots = _key_slots(len(questions), rng)
    return [_placed(question, slot, rng) for question, slot in zip(questions, slots, strict=True)]


# ─── The length tell (G2-5) ───


def _tells(options: Sequence[str], key: int) -> bool:
    wrong = [len(option) for i, option in enumerate(options) if i != key]
    return bool(wrong) and len(options[key]) > LENGTH_TELL_RATIO * fmean(wrong)


def length_tell(question: QuizQuestion) -> bool:
    """True when the keyed option is over :data:`LENGTH_TELL_RATIO` times the mean
    length of the wrong ones."""
    return _tells(question.options, question.correct_index)


# ─── The spread (G2-4) ───


def _span(duration_s: float | None) -> int | None:
    """The video's length in whole seconds; None when it is unknown, under a second or
    not finite."""
    if duration_s is None or not math.isfinite(duration_s) or duration_s < 1:
        return None
    return int(duration_s)


def stretch_bounds(duration_s: float | None) -> list[tuple[int, int]]:
    """``(start, end)`` seconds of each stretch, in order; ``[]`` for an unknown length."""
    span = _span(duration_s)
    if span is None:
        return []
    cuts = [-(-span * k // QUIZ_STRETCHES) for k in range(QUIZ_STRETCHES + 1)]
    return list(pairwise(cuts))


def stretch_of(stamp: int | None, duration_s: float | None) -> int | None:
    """The stretch a stamp falls in (one past the end counts as the last); None without
    a stamp or a known length."""
    span = _span(duration_s)
    if stamp is None or span is None:
        return None
    return min(max(stamp, 0) * QUIZ_STRETCHES // span, QUIZ_STRETCHES - 1)


def _held_per_stretch(stamps: Sequence[int], duration_s: float | None) -> list[int]:
    """How many of ``stamps`` fall in each stretch."""
    held = [0] * QUIZ_STRETCHES
    for stamp in stamps:
        stretch = stretch_of(stamp, duration_s)
        if stretch is not None:
            held[stretch] += 1
    return held


def stretch_quotas(stamps: Sequence[int], duration_s: float | None, asked: int) -> list[int]:
    """Questions each stretch's call asks for, ``asked`` in all: one for each stretch that
    holds a key idea, the rest in proportion to the key ideas each holds (largest
    remainder); ``[]`` without a known length or a stamped key idea — then one call
    writes the whole quiz (``quiz_v2_prompt``)."""
    held = _held_per_stretch(stamps, duration_s)
    total = sum(held)
    if not total:
        return []
    floor = [1 if n else 0 for n in held]
    shares = [max(asked - sum(floor), 0) * n / total for n in held]
    quotas = [base + int(share) for base, share in zip(floor, shares, strict=True)]
    by_remainder = sorted(range(QUIZ_STRETCHES), key=lambda i: int(shares[i]) - shares[i])
    for i in by_remainder[: max(asked - sum(quotas), 0)]:
        quotas[i] += 1
    return quotas


def _first_per_stretch(
    questions: Sequence[QuizQuestion], ranked: Sequence[int], duration_s: float | None
) -> list[int]:
    """The first ranked, clean question of each stretch, in rank order."""
    picked: list[int] = []
    covered: set[int] = set()
    for i in ranked:
        stretch = stretch_of(questions[i].timestamp, duration_s)
        if stretch is None or stretch in covered or length_tell(questions[i]):
            continue
        covered.add(stretch)
        picked.append(i)
    return picked


def _log_selection(
    questions: Sequence[QuizQuestion], shipped: Sequence[int], duration_s: float | None
) -> None:
    flagged = [i for i, question in enumerate(questions) if length_tell(question)]
    stretches = {stretch_of(questions[i].timestamp, duration_s) for i in shipped} - {None}
    logger.info(
        "Quiz v2: shipping %d of %d checked (length_tell=%d, %d of them shipped; "
        "stretches covered %d of %d)",
        len(shipped), len(questions), len(flagged), len(set(flagged) & set(shipped)),
        len(stretches), len(stretch_bounds(duration_s)),
    )  # fmt: skip


def select_quiz(
    questions: Sequence[QuizQuestion], count: int, duration_s: float | None
) -> tuple[list[QuizQuestion], list[QuizQuestion]]:
    """``(shipped, spares)``: ``count`` questions — one per stretch first, clean before
    flagged — shipped in the model's order; the rest are the spares, in rank order."""
    ranked = sorted(range(len(questions)), key=lambda i: length_tell(questions[i]))
    picked = _first_per_stretch(questions, ranked, duration_s)[:count]
    picked += [i for i in ranked if i not in picked][: count - len(picked)]
    _log_selection(questions, picked, duration_s)
    shipped = [questions[i] for i in sorted(picked)]
    return shipped, [questions[i] for i in ranked if i not in picked]


# ─── The late refill (``phases.enrichment.settle_late_quiz``) ───


def _stored_int(question: Mapping[str, Any], key: str) -> int | None:
    value = question.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stored_clean(question: Mapping[str, Any]) -> bool:
    """True when a stored question's key gives nothing away by its length."""
    options, key = question.get("options"), _stored_int(question, "correctIndex")
    if not isinstance(options, list) or key is None or not 0 <= key < len(options):
        return False
    return not _tells([str(option) for option in options], key)


def spare_for(
    dropped: Mapping[str, Any], spares: Sequence[Mapping[str, Any]], duration_s: float | None
) -> int:
    """Which of the stored ``spares`` replaces ``dropped``: the first clean one in its
    stretch of the video, else 0 — the next spare in rank order."""
    stretch = stretch_of(_stored_int(dropped, "timestamp"), duration_s)
    if stretch is None:
        return 0
    same = (
        i
        for i, spare in enumerate(spares)
        if _stored_clean(spare)
        and stretch_of(_stored_int(spare, "timestamp"), duration_s) == stretch
    )
    return next(same, 0)


def in_slot_of(spare: Mapping[str, Any], dropped: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of the stored ``spare`` with its keyed option at ``dropped``'s key position
    (modulo its own option count), the wrong options in their order; unchanged when
    either holds no key."""
    options, key = spare.get("options"), _stored_int(spare, "correctIndex")
    slot = _stored_int(dropped, "correctIndex")
    if slot is None or not isinstance(options, list) or key is None:
        return dict(spare)
    if not 0 <= key < len(options):
        return dict(spare)
    slot %= len(options)
    wrong = [option for i, option in enumerate(options) if i != key]
    return {**spare, "options": [*wrong[:slot], options[key], *wrong[slot:]], "correctIndex": slot}
