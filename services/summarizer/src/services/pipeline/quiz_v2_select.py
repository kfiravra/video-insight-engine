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
- **the spread** (G2-4, 4.0 b): gate 2 shipped S03's 8 questions all inside 0:00–21:37
  of a 57-minute lecture, gate 3 S15's 8 inside 0:00–27:40 of 71:22. The video is cut
  into parts: a chunked run's extraction chunks (:func:`chunk_windows`), else
  :data:`QUIZ_STRETCHES` stretches (thirds, :func:`stretch_bounds`); each part holding a
  target idea gets its own call, sized by :func:`stretch_quotas` (``quiz_v2_prompt``;
  without chunks only past the 3-question band), and :func:`select_quiz` ships the first
  clean question of each part, then the first clean one of each memory-outline section
  not yet tested (one per section before two in one: S14 shipped two metformin
  questions), then fills the count in rank order.

The shipped questions keep the model's importance order, not the video's: the prompt
asks for the most important first, and the late refill (``phases.enrichment.
settle_late_quiz``) appends spares at the end — an order only importance keeps true.
A question that refill drops (it restates a shipped takeaway) is replaced by the first
clean spare from its own part, else the next spare (:func:`spare_for`), so the drop
does not leave its part of the video untested; the spare's key moves to the dropped
question's position (:func:`in_slot_of`), so the shipped quiz keeps its even deal (the
spares' own deck put 2 of 3 keys on one position in half of the 3-question refills).
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from itertools import pairwise
from statistics import fmean
from typing import TYPE_CHECKING, Any

from ...models.pipeline_types import QuizQuestion

if TYPE_CHECKING:
    from .extraction_chunks import ExtractionChunk

logger = logging.getLogger(__name__)

# A part: ``(start, end)`` seconds of the video one call reads and one question covers.
Part = tuple[int, int]
# Thirds: the fewest stretches the shortest quiz (3 questions) still covers one by
# one, and the split gate 2's S03 check reads ("the last 36 minutes are untested").
QUIZ_STRETCHES = 3
# A run of this many extraction chunks or more is quizzed chunk by chunk; fewer keep the
# thirds (Kfir's call pending, design §6.1). 1 makes a one-chunk run one part: one call
# over the whole video, the failure mode the thirds fixed on S03.
QUIZ_PARTS_MIN_CHUNKS = 2
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


# ─── The spread (G2-4, 4.0 b) ───


def _span(duration_s: float | None) -> int | None:
    """The video's length in whole seconds; None when it is unknown, under a second or
    not finite."""
    if duration_s is None or not math.isfinite(duration_s) or duration_s < 1:
        return None
    return int(duration_s)


def stretch_bounds(duration_s: float | None) -> list[Part]:
    """``(start, end)`` seconds of each third, in order; ``[]`` for an unknown length."""
    span = _span(duration_s)
    if span is None:
        return []
    cuts = [-(-span * k // QUIZ_STRETCHES) for k in range(QUIZ_STRETCHES + 1)]
    return list(pairwise(cuts))


def chunk_windows(chunks: Sequence[ExtractionChunk], duration_s: float | None) -> tuple[Part, ...]:
    """Each extraction chunk's ``(start, end)`` seconds, the open last one ending at the
    video's length (S03's chapters end at 50:31 of 57:14); ``()`` for fewer than
    :data:`QUIZ_PARTS_MIN_CHUNKS` chunks or no known end."""
    if len(chunks) < QUIZ_PARTS_MIN_CHUNKS:
        return ()
    end = _span(duration_s) or _span(chunks[-1].video_end_s)
    if end is None:
        return ()
    return tuple(
        (int(chunk.start_s or 0), end if chunk.end_s is None else int(chunk.end_s))
        for chunk in chunks
    )


def quiz_parts(duration_s: float | None, windows: Sequence[Part] = ()) -> list[Part]:
    """The quiz's parts: the chunk windows, else the thirds."""
    return list(windows) or stretch_bounds(duration_s)


def stretch_of(stamp: int | None, parts: Sequence[Part]) -> int | None:
    """The part a stamp falls in (before the first counts as the first, past the end as
    the last); None without a stamp or parts."""
    if stamp is None or not parts:
        return None
    return max(bisect_right([start for start, _ in parts], stamp) - 1, 0)


def _held_per_stretch(stamps: Sequence[int], parts: Sequence[Part]) -> list[int]:
    """How many of ``stamps`` fall in each part."""
    held = [0] * len(parts)
    for stamp in stamps:
        part = stretch_of(stamp, parts)
        if part is not None:
            held[part] += 1
    return held


def stretch_quotas(stamps: Sequence[int], parts: Sequence[Part], asked: int) -> list[int]:
    """Questions each part's call asks for, ``asked`` in all: one for each part that
    holds a target idea, the rest in proportion to the targets each holds (largest
    remainder); ``[]`` without parts or a stamped target — then one call writes the
    whole quiz (``quiz_v2_prompt``)."""
    held = _held_per_stretch(stamps, parts)
    total = sum(held)
    if not total:
        return []
    floor = [1 if n else 0 for n in held]
    shares = [max(asked - sum(floor), 0) * n / total for n in held]
    quotas = [base + int(share) for base, share in zip(floor, shares, strict=True)]
    by_remainder = sorted(range(len(held)), key=lambda i: int(shares[i]) - shares[i])
    for i in by_remainder[: max(asked - sum(quotas), 0)]:
        quotas[i] += 1
    return quotas


def _section_of(stamp: int | None, outline: Sequence[int]) -> int | None:
    """The memory-outline section (by its start) a stamp falls in; None without either."""
    if stamp is None or not outline:
        return None
    return max(bisect_right(outline, stamp) - 1, 0)


def _first_per_stretch(
    questions: Sequence[QuizQuestion], ranked: Sequence[int], parts: Sequence[Part]
) -> list[int]:
    """The first ranked, clean question of each part, in rank order."""
    picked: list[int] = []
    covered: set[int] = set()
    for i in ranked:
        part = stretch_of(questions[i].timestamp, parts)
        if part is None or part in covered or length_tell(questions[i]):
            continue
        covered.add(part)
        picked.append(i)
    return picked


def _first_per_section(
    questions: Sequence[QuizQuestion],
    ranked: Sequence[int],
    picked: Sequence[int],
    outline: Sequence[int],
) -> list[int]:
    """The first ranked, clean question of each outline section ``picked`` leaves
    untested, in rank order."""
    tested = {_section_of(questions[i].timestamp, outline) for i in picked}
    added: list[int] = []
    for i in ranked:
        section = _section_of(questions[i].timestamp, outline)
        if section is None or section in tested or i in picked or length_tell(questions[i]):
            continue
        tested.add(section)
        added.append(i)
    return added


def _log_selection(
    questions: Sequence[QuizQuestion],
    shipped: Sequence[int],
    parts: Sequence[Part],
    outline: Sequence[int],
) -> None:
    flagged = [i for i, question in enumerate(questions) if length_tell(question)]
    covered = {stretch_of(questions[i].timestamp, parts) for i in shipped} - {None}
    sections = {_section_of(questions[i].timestamp, outline) for i in shipped} - {None}
    logger.info(
        "Quiz v2: shipping %d of %d checked (length_tell=%d, %d of them shipped; "
        "parts covered %d of %d; outline sections %d of %d)",
        len(shipped), len(questions), len(flagged), len(set(flagged) & set(shipped)),
        len(covered), len(parts), len(sections), len(outline),
    )  # fmt: skip


def select_quiz(
    questions: Sequence[QuizQuestion],
    count: int,
    parts: Sequence[Part],
    outline: Sequence[int] = (),
) -> tuple[list[QuizQuestion], list[QuizQuestion]]:
    """``(shipped, spares)``: ``count`` questions — one per part, then one per untested
    outline section (``outline`` = the sections' starts), then by rank, clean before
    flagged — shipped in the model's order; the rest are the spares, in rank order."""
    ranked = sorted(range(len(questions)), key=lambda i: length_tell(questions[i]))
    picked = _first_per_stretch(questions, ranked, parts)[:count]
    picked += _first_per_section(questions, ranked, picked, outline)[: count - len(picked)]
    picked += [i for i in ranked if i not in picked][: count - len(picked)]
    _log_selection(questions, picked, parts, outline)
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
    dropped: Mapping[str, Any], spares: Sequence[Mapping[str, Any]], parts: Sequence[Part]
) -> int:
    """Which of the stored ``spares`` replaces ``dropped``: the first clean one in its
    part of the video, else 0 — the next spare in rank order."""
    part = stretch_of(_stored_int(dropped, "timestamp"), parts)
    if part is None:
        return 0
    same = (
        i
        for i, spare in enumerate(spares)
        if _stored_clean(spare) and stretch_of(_stored_int(spare, "timestamp"), parts) == part
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
