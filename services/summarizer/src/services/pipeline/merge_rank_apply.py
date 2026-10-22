"""merge_rank's answer applied to the final merge (output-quality 3.1) — pure code.

``merge_rank`` reads the reply into a :class:`MergeRankAnswer` (ids only, each one
checked against the request); this module edits a deep copy of the extraction
``split_extraction._complete`` merged from every landed group:

1. **Duplicates.** Each group's drops leave their lists. An id is applied only while its
   item is still where the call saw it — same (path, index), same fingerprint (stamp +
   head start); a group whose keep moved is skipped. Four guards hold a drop back
   (recorded under ``held`` by name): ``share`` — a list never loses more than
   :data:`MAX_DROP_SHARE` of its items to the dedupe; ``minimum`` — a non-timeline list
   keeps its ``densityGates`` minimum, a timeline (``moment_track``) its §9 band minimum
   (``ListSpec.floor``); ``window`` — no drop empties a :data:`WINDOW_S` window the list
   covered, windows counted back from the video's end, so the last ten minutes are one
   window (the long-video rubric: the last 10 minutes in ≥ 2 tabs); :data:`KEEP_CUT` — a
   group drops only while its keep is sure to ship: its list is cut by rank below (a kept
   twin outranks every tier, while at most ``cap`` are kept), or it already fits the cap
   assembly ships it at (``ListSpec.ships``). A keep in a timeline over its §9 cap, or in
   a list left to assembly's richness cut, could be sampled out after its twins left,
   and the point would be on no tab (review p3 B1: S02, a Q kept over its K and S).
2. **Rank (D-B).** A ranked list (``learning.keyIdeas`` / ``keyPoints`` / ``takeaways``,
   not a timeline) still over its assembly cap is cut to it with ``rank_sample``: the
   call's rank in thirds → tiers 3 / 2 / 1, unranked 0, a kept twin above them all (its
   twin left another list for it); inside the tier that does not fit, even sampling keeps
   the tail. The window guard holds here too: a window the cut would empty gets its
   best-ranked item back in place of the lowest-ranked one of a window that keeps two.
   A list the call ranked none of is left to assembly. Timelines stay with
   ``sample_moments``.
3. **Sections** — code, not the call, so they apply whether or not the call answered.
   ``learning.keyIdeas[].section`` = the title of the outline section whose [start, end)
   holds the idea's stamp: asked for them (p3i3, S13), Haiku packed 33 ideas spanning 42
   minutes into sections 1–4 of 11 in list order, none where its stamp lies. At most
   :data:`MAX_SECTIONS` distinct: the smallest section joins its smaller neighbour (on a
   tie the earlier) until they fit, and a joined section takes the title of its member
   holding the most ideas — S02 shipped 24 one-item sections from the chunks' own labels.
   An idea without a stamp loses the chunk's label (it shows under "Other"). Without an
   outline (fewer than two sections) the chunks' own labels, in list order, are joined
   the same way.

The ``_mergeRank`` record: ``{status, ms, sectioned}`` (``sectioned`` = ``{items,
sections}``, ``{}`` when no idea was titled) and, when the call answered, ``dropped``
(``{path, id, head, keep}``), ``held`` (``{path, id, guard}``), ``ranked`` (``{path,
from, to}``) and ``moved`` (ids whose item moved).
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...models.memory_types import OutlineSection
from .assembly.sampling import rank_sample
from .merge_rank_render import (
    ListSpec,
    MergeRankRequest,
    RankedItem,
    fingerprint,
    item_seconds,
    value_at,
)

OK = "ok"
MAX_DROP_SHARE = (2, 5)  # 40 %, as a fraction: integer floors, no float edge
WINDOW_S = 600
MAX_SECTIONS = 8
# The guard that holds a group whose keep assembly could still cut (module docstring).
KEEP_CUT = "keep_cut"
# A kept twin outranks every rank tier (3 / 2 / 1 / 0) in the cut.
_KEEP_TIER = 4
_RECORD_HEAD_CHARS = 120


@dataclass(frozen=True)
class DuplicateGroup:
    """One answered group: the id that stays and the ids that leave for it."""

    keep: str
    drop: tuple[str, ...]


@dataclass(frozen=True)
class MergeRankAnswer:
    """The call's checked answer; ``status`` != ``ok``: no drop and no rank cut (the
    sections are code's and still apply).

    ``rank`` = ids most → least central.
    """

    status: str
    request: MergeRankRequest | None = None
    duplicates: tuple[DuplicateGroup, ...] = ()
    rank: tuple[str, ...] = ()
    ms: int = 0


# ─── The edit ───


@dataclass
class _Lane:
    """One list under edit: its items as merged, the indices that go, the dedupe's count."""

    spec: ListSpec
    items: list[Any]
    removed: set[int] = field(default_factory=set)
    drops: int = 0

    def kept(self) -> list[int]:
        return [index for index in range(len(self.items)) if index not in self.removed]


@dataclass
class _Edit:
    """The deep-copied extraction, its lanes and the ids still where the call saw them."""

    extraction: dict[str, Any]
    lanes: dict[str, _Lane]
    resolved: dict[str, RankedItem]
    duration: int | None
    keeps: set[str] = field(default_factory=set)

    @classmethod
    def start(cls, extraction: Mapping[str, Any], request: MergeRankRequest) -> _Edit:
        work = copy.deepcopy(dict(extraction))
        lanes: dict[str, _Lane] = {}
        for spec in request.lists:
            items = value_at(work, spec.path)
            if isinstance(items, list):
                lanes[spec.path] = _Lane(spec, items)
        resolved = {
            item_id: item
            for item_id, item in request.items.items()
            if _still_there(lanes.get(item.path), item, request.duration)
        }
        return cls(work, lanes, resolved, request.duration)

    def window(self, lane: _Lane, index: int) -> int | None:
        return window_of(item_seconds(lane.items[index], self.duration), self.duration)

    def result(self) -> dict[str, Any]:
        """The extraction with every removed index out of its list."""
        for path, lane in self.lanes.items():
            domain, _, name = path.partition(".")
            self.extraction[domain][name] = [lane.items[index] for index in lane.kept()]
        return self.extraction


def _still_there(lane: _Lane | None, item: RankedItem, duration: int | None) -> bool:
    if lane is None or item.index >= len(lane.items):
        return False
    return fingerprint(lane.items[item.index], duration) == item.fingerprint


def window_of(seconds: int | None, duration: int | None) -> int | None:
    """The :data:`WINDOW_S` window a stamp falls in, counted back from the video's end."""
    if seconds is None:
        return None
    if duration and duration > 0:
        return max(0, duration - seconds) // WINDOW_S
    return seconds // WINDOW_S


# ─── 1. Duplicates ───


def _guard(edit: _Edit, lane: _Lane, index: int) -> str | None:
    """Why ``index`` may not leave ``lane`` (module docstring); None when it may."""
    total = len(lane.items)
    share, of = MAX_DROP_SHARE
    if lane.drops + 1 > total * share // of:
        return "share"
    if total - len(lane.removed) - 1 < lane.spec.floor:
        return "minimum"
    window = edit.window(lane, index)
    left = [i for i in lane.kept() if i != index and edit.window(lane, i) == window]
    return "window" if window is not None and not left else None


def _drop_row(item: RankedItem, keep: str) -> dict[str, Any]:
    head = item.head[:_RECORD_HEAD_CHARS]
    return {"path": item.path, "id": item.item_id, "head": head, "keep": keep}


def _keep_ships(edit: _Edit, keep: RankedItem, ranked: frozenset[str]) -> bool:
    """``keep`` is on the page whatever assembly's caps do (module docstring): its list is
    cut by rank (``ranked`` = the paths the rank names) with room for one more kept twin,
    or the whole list, before any drop, is within the cap assembly ships it at."""
    lane = edit.lanes[keep.path]
    spec = lane.spec
    if spec.cap is not None and spec.path in ranked:
        pinned = {k for k in edit.keeps if edit.resolved[k].path == spec.path}
        return keep.item_id in pinned or len(pinned) < spec.cap
    return spec.ships is None or len(lane.items) <= spec.ships


def _drop_group(
    edit: _Edit, group: DuplicateGroup, keep_ships: bool
) -> tuple[list[dict], list[dict]]:
    """One group's drops the guards allow — the dropped rows and the held ones."""
    dropped: list[dict] = []
    held: list[dict] = []
    for item in (edit.resolved[i] for i in group.drop if i in edit.resolved):
        lane = edit.lanes[item.path]
        guard = _guard(edit, lane, item.index) if keep_ships else KEEP_CUT
        if guard is not None:
            held.append({"path": item.path, "id": item.item_id, "guard": guard})
            continue
        lane.removed.add(item.index)
        lane.drops += 1
        edit.keeps.add(group.keep)
        dropped.append(_drop_row(item, group.keep))
    return dropped, held


def _drop_duplicates(edit: _Edit, answer: MergeRankAnswer) -> tuple[list[dict], list[dict]]:
    """Every answered drop the guards allow — the dropped rows and the held ones."""
    ranked = frozenset(edit.resolved[i].path for i in answer.rank if i in edit.resolved)
    dropped: list[dict] = []
    held: list[dict] = []
    for group in (g for g in answer.duplicates if g.keep in edit.resolved):
        keep_ships = _keep_ships(edit, edit.resolved[group.keep], ranked)
        group_dropped, group_held = _drop_group(edit, group, keep_ships)
        dropped.extend(group_dropped)
        held.extend(group_held)
    return dropped, held


# ─── 2. Rank ───


def _tiers(edit: _Edit, lane: _Lane, rank: Sequence[str]) -> dict[int, int]:
    """Index → rank tier: the call's rank in thirds → 3 / 2 / 1; a kept twin above."""
    order = [
        edit.resolved[i].index
        for i in rank
        if i in edit.resolved and edit.resolved[i].path == lane.spec.path
    ]
    tiers = {index: 3 - (3 * position) // len(order) for position, index in enumerate(order)}
    for keep in (edit.resolved[k] for k in edit.keeps):
        if keep.path == lane.spec.path:
            tiers[keep.index] = _KEEP_TIER
    return tiers if order else {}


def _covers_twice(index: int, chosen: set[int], window: Callable[[int], int | None]) -> bool:
    """Taking ``index`` out of ``chosen`` empties no window."""
    own = window(index)
    return own is None or any(other != index and window(other) == own for other in chosen)


def refill_windows(
    kept: Sequence[int],
    chosen: Sequence[int],
    tiers: Mapping[int, int],
    window: Callable[[int], int | None],
) -> list[int]:
    """``chosen`` with every window ``kept`` covered back in: the window's best-ranked item
    for the lowest-ranked chosen one whose window keeps another (never a kept twin)."""
    picked = set(chosen)
    windows = sorted({w for w in map(window, kept) if w is not None})
    for empty in windows:
        if any(window(index) == empty for index in picked):
            continue
        donors = [
            i for i in picked if tiers.get(i, 0) < _KEEP_TIER and _covers_twice(i, picked, window)
        ]
        if not donors:
            break
        back = max((i for i in kept if window(i) == empty), key=lambda i: (tiers.get(i, 0), -i))
        picked.discard(min(donors, key=lambda i: (tiers.get(i, 0), -i)))
        picked.add(back)
    return sorted(picked)


def _rank_cut(edit: _Edit, answer: MergeRankAnswer) -> list[dict]:
    """Every ranked list over its cap, cut by rank tier (module docstring)."""
    ranked: list[dict] = []
    for lane in edit.lanes.values():
        kept, cap = lane.kept(), lane.spec.cap
        tiers = _tiers(edit, lane, answer.rank) if cap is not None else {}
        if cap is None or len(kept) <= cap or not tiers:
            continue
        chosen = rank_sample(kept, cap, lambda index: tiers.get(index, 0))
        chosen = refill_windows(kept, chosen, tiers, lambda index: edit.window(lane, index))
        lane.removed.update(set(kept) - set(chosen))
        ranked.append({"path": lane.spec.path, "from": len(kept), "to": len(chosen)})
    return ranked


# ─── 3. Sections ───


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


def _write_sections(edit: _Edit, outline: Sequence[OutlineSection]) -> dict[str, int]:
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


# ─── Entry point ───


def _record(answer: MergeRankAnswer, **outcome: Any) -> dict[str, Any]:
    return {"status": answer.status, "ms": answer.ms, **outcome}


def apply_merge_rank(
    extraction: dict[str, Any], answer: MergeRankAnswer
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The extraction with the answer applied and the ``_mergeRank`` record; without a
    request, the extraction itself; without an answer, the sections alone."""
    request = answer.request
    if request is None:
        return extraction, _record(answer)
    edit = _Edit.start(extraction, request)
    if answer.status != OK:
        return edit.result(), _record(answer, sectioned=_write_sections(edit, request.outline))
    dropped, held = _drop_duplicates(edit, answer)
    ranked = _rank_cut(edit, answer)
    sectioned = _write_sections(edit, request.outline)
    moved = len(request.items) - len(edit.resolved)
    return edit.result(), _record(
        answer, dropped=dropped, held=held, ranked=ranked, sectioned=sectioned, moved=moved
    )
