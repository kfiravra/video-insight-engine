"""merge_rank's sections (output-quality 3.1): the key ideas' ``section`` is code's, not the call's.

``learning.keyIdeas[].section`` = the title of the outline section whose [start, end)
holds the idea's stamp (module docstring of ``merge_rank_apply``, item 3). Pure code: it
runs whether or not the call answered.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple

from ...models.memory_types import OutlineSection
from .merge_rank_render import item_seconds
from .quiz_v2 import content_words

if TYPE_CHECKING:
    from .merge_rank_apply import _Edit, _Lane

MAX_SECTIONS = 8
# An idea this close to its section's start or end may belong to the neighbour across it
# (memory's boundaries sit minutes off; S03's Challenge 2 was 3 minutes into the next).
EDGE_S = 120
# ... when the neighbour matches its words by this many more than its own section does.
WORD_MARGIN = 2
_PHRASE_BREAKS = (", ", " and ", "; ")


def _joined(counts: Counter[int]) -> list[list[int]]:
    """The used outline sections in order, the smallest joined to its smaller neighbour
    (the earlier on a tie) until at most :data:`MAX_SECTIONS` remain."""
    groups = [[index] for index in sorted(counts)]
    while len(groups) > MAX_SECTIONS:
        sizes = [sum(counts[i] for i in group) for group in groups]
        small = min(range(len(groups)), key=lambda k: (sizes[k], k))
        if small == 0 or (small < len(groups) - 1 and sizes[small + 1] < sizes[small - 1]):
            neighbour = small + 1
        else:
            neighbour = small - 1
        low, high = sorted((small, neighbour))
        groups[low : high + 1] = [groups[low] + groups[high]]
    return groups


def _whole_phrase(text: str) -> str:
    """A title memory cut at 60 characters ("…") back to a whole phrase: before its first
    ":" (when two words stay), else before its last ", " / " and " / "; ", else without
    its trailing function words. A title that was not cut is returned as it is."""
    if not text.endswith("…"):
        return text
    body = text.removesuffix("…").rstrip()
    lead = body.partition(":")[0]
    if ":" in body and len(lead.split()) >= 2:
        return lead.strip()
    cuts = [body.rfind(separator) for separator in _PHRASE_BREAKS]
    if (cut := max(cuts)) > 0 and len(body[:cut].split()) >= 2:
        return body[:cut].strip(" ,;")
    words = body.split()
    while words and not content_words(words[-1]):
        words.pop()
    return " ".join(words) or body


def _title(section: OutlineSection) -> str:
    """The outline title as a section label: a lower-case first word capitalised ("intro"
    → "Intro"), a cased one kept ("macOS", "iPhone")."""
    text = _whole_phrase(" ".join(section.title.split()))
    first = text.split(" ", 1)[0]
    return text[:1].upper() + text[1:] if first.islower() else text


def _joined_titles(
    counts: Counter[int],
    names: Sequence[str],
    fits: Callable[[Sequence[int], int], int] | None = None,
) -> dict[int, str]:
    """Section index → the name its ideas ship under once the sections are joined: the
    member whose words fit the group's ideas best (``fits(group, member)``), then the one
    holding the most ideas, then the earliest."""
    titles: dict[int, str] = {}
    for group in _joined(counts):
        lead = max(
            group, key=lambda index: (fits(group, index) if fits else 0, counts[index], -index)
        )
        titles.update(dict.fromkeys(group, names[lead]))
    return titles


def section_titles(counts: Counter[int], outline: Sequence[OutlineSection]) -> dict[int, str]:
    """Outline index → the title its ideas ship under (module docstring)."""
    return _joined_titles(counts, [_title(section) for section in outline])


def stamp_section(seconds: int | None, outline: Sequence[OutlineSection]) -> int | None:
    """The 0-based outline section whose [start, end) holds ``seconds`` (the last one
    holds the video's end); None without a stamp or an outline."""
    if seconds is None or not outline:
        return None
    return next((i for i, section in enumerate(outline) if seconds < section.end), len(outline) - 1)


class Placement(NamedTuple):
    """Where each kept idea goes: its section number, the section names, and the words
    each section and each idea carries (for the neighbour test and the joined title)."""

    placed: dict[int, int]
    names: list[str]
    section_words: list[frozenset[str]]
    idea_words: dict[int, frozenset[str]]


def _label(idea: object) -> str:
    section = idea.get("section") if isinstance(idea, Mapping) else None
    return " ".join(section.split()) if isinstance(section, str) else ""


def idea_words(idea: object) -> frozenset[str]:
    """The content words of an idea's claim and its own extraction label."""
    claim = idea.get("claim") if isinstance(idea, Mapping) else None
    return content_words(f"{claim if isinstance(claim, str) else ''} {_label(idea)}")


def place_stamp(
    seconds: int | None, words: frozenset[str], outline: Sequence[OutlineSection]
) -> int | None:
    """The outline section for a stamp: the one holding it, unless the stamp lies within
    :data:`EDGE_S` of that section's edge and the neighbour across it shares at least
    :data:`WORD_MARGIN` more of ``words`` (a tie or a smaller margin keeps the stamp's)."""
    home = stamp_section(seconds, outline)
    if home is None or seconds is None:
        return home
    near = ((home - 1, seconds - outline[home].start), (home + 1, outline[home].end - seconds))

    def fit(index: int) -> int:
        return len(words & _section_words(outline[index]))

    best = home
    for neighbour, distance in near:
        if 0 <= neighbour < len(outline) and distance <= EDGE_S:
            if fit(neighbour) >= fit(home) + WORD_MARGIN and fit(neighbour) > fit(best):
                best = neighbour
    return best


def _section_words(section: OutlineSection) -> frozenset[str]:
    return content_words(f"{section.title} {section.establishes}")


def _outline_places(edit: _Edit, lane: _Lane, outline: Sequence[OutlineSection]) -> Placement:
    """Kept index → the outline section its stamp falls in, and the sections' titles."""
    placed: dict[int, int] = {}
    words = {index: idea_words(lane.items[index]) for index in lane.kept()}
    for index in lane.kept():
        seconds = item_seconds(lane.items[index], edit.duration)
        section = place_stamp(seconds, words[index], outline)
        if section is not None:
            placed[index] = section
    names = [_title(section) for section in outline]
    return Placement(placed, names, [_section_words(s) for s in outline], words)


def _label_places(lane: _Lane) -> Placement:
    """Without an outline: kept index → its own label, the labels numbered in list order."""
    labels = {index: _label(lane.items[index]) for index in lane.kept()}
    names = list(dict.fromkeys(label for label in labels.values() if label))
    number = {name: position for position, name in enumerate(names)}
    placed = {index: number[label] for index, label in labels.items() if label}
    words = {index: idea_words(lane.items[index]) for index in placed}
    return Placement(placed, names, [content_words(name) for name in names], words)


def _best_fit(plan: Placement) -> Callable[[Sequence[int], int], int]:
    """``fits(group, member)``: the words the member's title shares with the group's ideas."""

    def fits(group: Sequence[int], member: int) -> int:
        return sum(
            len(plan.idea_words[index] & plan.section_words[member])
            for index, section in plan.placed.items()
            if section in group
        )

    return fits


def _in_video_order(lane: _Lane, placed: Mapping[int, int], duration: int | None) -> None:
    """The kept ideas written section by section, each in stamp order; an unplaced idea
    keeps its place at the end. The removed items follow, still removed."""
    kept = lane.kept()
    dropped = [lane.items[index] for index in sorted(lane.removed)]

    def key(index: int) -> tuple[int, int]:
        seconds = item_seconds(lane.items[index], duration)
        return placed.get(index, len(lane.items)), seconds if seconds is not None else 0

    ordered = [lane.items[index] for index in sorted(kept, key=key)]
    lane.items[:] = ordered + dropped
    lane.removed = set(range(len(ordered), len(lane.items)))


def write_sections(edit: _Edit, outline: Sequence[OutlineSection]) -> dict[str, int]:
    """The sectioned list's kept ideas under the titles of the sections their stamps fall
    in — without an outline, under their own labels joined (module docstring)."""
    lane = next((lane for lane in edit.lanes.values() if lane.spec.sectioned), None)
    if lane is None:
        return {}
    plan = _outline_places(edit, lane, outline) if outline else _label_places(lane)
    if not plan.placed:
        return {}
    titles = _joined_titles(Counter(plan.placed.values()), plan.names, _best_fit(plan))
    for index in lane.kept():
        idea = lane.items[index]
        if isinstance(idea, dict) and index in plan.placed:
            idea["section"] = titles[plan.placed[index]]
        elif isinstance(idea, dict):
            idea.pop("section", None)
    _in_video_order(lane, plan.placed, edit.duration)
    return {"items": len(plan.placed), "sections": len({titles[s] for s in plan.placed.values()})}
