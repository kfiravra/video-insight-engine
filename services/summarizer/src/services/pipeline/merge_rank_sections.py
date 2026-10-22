"""merge_rank's sections (output-quality 3.1): the key ideas' ``section`` is code's, not the call's.

``learning.keyIdeas[].section`` = the title of the outline section whose [start, end)
holds the idea's stamp (module docstring of ``merge_rank_apply``, item 3). Pure code: it
runs whether or not the call answered.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ...models.memory_types import OutlineSection
from .merge_rank_render import item_seconds

if TYPE_CHECKING:
    from .merge_rank_apply import _Edit, _Lane

MAX_SECTIONS = 8


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


def _title(section: OutlineSection) -> str:
    """The outline title as a section label: a lower-case first word capitalised ("intro"
    → "Intro"), a cased one kept ("macOS", "iPhone")."""
    text = " ".join(section.title.split())
    first = text.split(" ", 1)[0]
    return text[:1].upper() + text[1:] if first.islower() else text


def _joined_titles(counts: Counter[int], names: Sequence[str]) -> dict[int, str]:
    """Section index → the name its ideas ship under once the sections are joined."""
    titles: dict[int, str] = {}
    for group in _joined(counts):
        lead = max(group, key=lambda index: (counts[index], -index))
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


Places = tuple[dict[int, int], list[str]]


def _outline_places(edit: _Edit, lane: _Lane, outline: Sequence[OutlineSection]) -> Places:
    """Kept index → the outline section its stamp falls in, and the sections' titles."""
    placed: dict[int, int] = {}
    for index in lane.kept():
        section = stamp_section(item_seconds(lane.items[index], edit.duration), outline)
        if section is not None:
            placed[index] = section
    return placed, [_title(section) for section in outline]


def _label(idea: object) -> str:
    section = idea.get("section") if isinstance(idea, Mapping) else None
    return " ".join(section.split()) if isinstance(section, str) else ""


def _label_places(lane: _Lane) -> Places:
    """Without an outline: kept index → its own label, the labels numbered in list order."""
    labels = {index: _label(lane.items[index]) for index in lane.kept()}
    names = list(dict.fromkeys(label for label in labels.values() if label))
    number = {name: position for position, name in enumerate(names)}
    return {index: number[label] for index, label in labels.items() if label}, names


def write_sections(edit: _Edit, outline: Sequence[OutlineSection]) -> dict[str, int]:
    """The sectioned list's kept ideas under the titles of the sections their stamps fall
    in — without an outline, under their own labels joined (module docstring)."""
    lane = next((lane for lane in edit.lanes.values() if lane.spec.sectioned), None)
    if lane is None:
        return {}
    placed, names = _outline_places(edit, lane, outline) if outline else _label_places(lane)
    if not placed:
        return {}
    titles = _joined_titles(Counter(placed.values()), names)
    for index in lane.kept():
        idea = lane.items[index]
        if isinstance(idea, dict) and index in placed:
            idea["section"] = titles[placed[index]]
        elif isinstance(idea, dict):
            idea.pop("section", None)
    return {"items": len(placed), "sections": len({titles[s] for s in placed.values()})}
