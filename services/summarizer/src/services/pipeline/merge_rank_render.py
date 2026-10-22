"""merge_rank's input (output-quality 3.1, L16 / L20) — every list a planned tab reads, under ids.

A long video's field groups run once per chunk and each chunk writes its own lists: a
point made across a chunk boundary comes back under another name (S01 "Pencils down" /
"Retiring from hand-written code"), and the same content lands in two tabs (Positions /
Questions Asked / Conversation Map). The merge_rank call reads every list at once, as
code renders it here:

    <outline>
    3 [5:10–7:40] $8M Omarchy Foundation and funding commitments — $8M is pledged …
    </outline>

    <lists>
    learning.keyIdeas — "Positions" (spot_explorer) [rank]
    K1 [2:28] (part 1) We are probably the only life … — Fridman opens by asking …

    podcast.qa — "Questions Asked" (moment_track)
    Q1 [2:28] (part 1) Do you think there's intelligent life … — Tegmark's guess is no …
    </lists>

- the outline: memory's sections numbered from 1 — how the video runs (code, not the call,
  sections the key ideas by it: ``merge_rank_apply``);
- one block per list a planned tab reads (its ``dataSource``, the first tab naming it),
  in plan order; the header names the tab and its component, and ``[rank]`` marks a
  rankable list (:data:`RANKED_FIELDS`, not a timeline) over the cap assembly ships it
  at (:func:`shipping_cap`);
- one line per item: its id (one letter per list + its 1-based position), its clock and
  the part (chunk) the clock falls in, its head and its body.

No item is ever left out — the dedupe must see every one: over
:data:`MERGE_RANK_INPUT_MAX_TOKENS` (≈ 3 characters a token on the Claude 5.5
tokenizer) the heads and bodies shorten level by level. Deterministic: the same lists
always give the same bytes. Each id keeps its item's place (path, index) and a
fingerprint (stamp + the head's first 40 characters), so the answer is applied only to
the item the call saw (``merge_rank_apply``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...models.memory_types import OutlineSection
from ...shared_config.assembly_policy import moment_caps_by_duration
from ...shared_config.domain_config import assembler_item_caps, density_gates
from .assembly.component_lists import COMPONENT_LIST_KEYS, SEQUENTIAL_COMPONENTS
from .assembly.sampling import key_idea_cap, moment_cap_for_duration
from .assembly.tab_references import bare_label
from .extraction_chunks import ExtractionChunk
from .memory import format_clock
from .pipeline_helpers import sanitize_for_prompt

# The rendered outline + lists stay under this (design §3.1; the rules add ≈ 0.5 K).
MERGE_RANK_INPUT_MAX_TOKENS = 10_000
# S01's memory call: 53.1 K characters billed 17,490 tokens (``hero_compact``'s measure).
CHARS_PER_TOKEN = 3.0
TIMELINE_COMPONENT = "moment_track"
KEY_IDEAS_PATH = "learning.keyIdeas"
# The lists the rank may cut (D-B): the argument's lists, never a timeline.
RANKED_FIELDS = frozenset({"learning.keyIdeas", "learning.keyPoints", "learning.takeaways"})
FINGERPRINT_HEAD_CHARS = 40
TITLE_MAX_CHARS = 200
# A stamp this far past the video's end is no second of it.
_END_SLACK_S = 5

_ID_LETTERS: dict[str, str] = {
    "keyIdeas": "K",
    "qa": "Q",
    "segments": "S",
    "timestamps": "M",
    "keyMoments": "M",
    "highlights": "M",
    "keyPoints": "P",
    "steps": "P",
    "takeaways": "T",
    "claims": "C",
    "concepts": "D",
}
# A list whose letter an earlier list took gets the first free one of these.
_SPARE_LETTERS = "ABEFGHJLNRUVWXYZ"
_STAMP_KEYS = ("seconds", "timestamp")
_HEAD_KEYS = ("claim", "question", "title", "label", "name", "term", "instruction", "item", "text")
_BODY_KEYS = ("why", "answer", "summary", "description", "definition", "detail", "explanation")
_DIGITS_RE = re.compile(r"\d+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Level:
    """Words a head / a body keeps at one budget level (0 = no body)."""

    head_words: int
    body_words: int


_LEVELS = (_Level(20, 15), _Level(16, 10), _Level(12, 6), _Level(10, 0), _Level(8, 0), _Level(6, 0))


@dataclass(frozen=True)
class RenderInput:
    """What the request is rendered from — all of it read at text-groups-done."""

    extraction: Mapping[str, Any]
    tabs: Sequence[Mapping[str, Any]]
    outline: Sequence[OutlineSection]
    chunks: Sequence[ExtractionChunk]
    duration: int | None
    title: str = ""


@dataclass(frozen=True)
class ListSpec:
    """One list the call reads: where it lives, the tab that shows it and its guards.

    ``floor`` = the fewest items the dedupe may leave (``densityGates`` min, a timeline's
    §9 band minimum); ``ships`` = the most items assembly ships of it (None = all,
    :func:`shipping_cap`); ``cap`` = the cap a ranked list over it is cut to (None = no
    rank); ``sectioned`` = code titles its items (``merge_rank_apply``: the outline
    section holding their stamps, else their own labels joined).
    """

    path: str
    label: str
    component: str
    letter: str
    floor: int
    ships: int | None = None
    cap: int | None = None
    sectioned: bool = False

    @property
    def timeline(self) -> bool:
        return self.component == TIMELINE_COMPONENT


@dataclass(frozen=True)
class RankedItem:
    """An id's item: its list, its place, its head (whole) and stamp, its fingerprint."""

    item_id: str
    path: str
    index: int
    head: str
    seconds: int | None
    fingerprint: str


@dataclass(frozen=True)
class MergeRankRequest:
    """The rendered input (``text``) and everything the answer is checked and applied by."""

    text: str
    lists: tuple[ListSpec, ...]
    items: Mapping[str, RankedItem]
    outline: tuple[OutlineSection, ...]
    duration: int | None


# ─── Items ───


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _clean(text: str) -> str:
    """Prompt-safe: no braces, no tags (extraction text echoes the transcript)."""
    return _collapse(sanitize_for_prompt(text, max_len=len(text)))


def _words(text: str, limit: int) -> str:
    words = _clean(text).split(" ")
    return " ".join(words) if len(words) <= limit else f"{' '.join(words[:limit])}…"


def item_seconds(item: object, duration: int | None) -> int | None:
    """The item's whole-second stamp inside the video; None without one."""
    if not isinstance(item, Mapping):
        return None
    for key in _STAMP_KEYS:
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            inside = duration is None or value <= duration + _END_SLACK_S
            return value if inside else None
    return None


def item_head(item: object) -> str:
    """The item's main text: a string item, else the first head key it fills."""
    if isinstance(item, str):
        return _collapse(item)
    if not isinstance(item, Mapping):
        return ""
    head = next((item[k] for k in _HEAD_KEYS if isinstance(item.get(k), str) and item[k]), "")
    return _collapse(head)


def _item_body(item: object, head: str) -> str:
    if not isinstance(item, Mapping):
        return ""
    for key in _BODY_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip() and _collapse(value) != head:
            return _collapse(value)
    return ""


def fingerprint(item: object, duration: int | None) -> str:
    """What says an item is still the one the call saw: its stamp + its head's start."""
    seconds = item_seconds(item, duration)
    head = item_head(item)[:FINGERPRINT_HEAD_CHARS].casefold()
    return f"{'' if seconds is None else seconds}|{head}"


def _part(seconds: int | None, chunks: Sequence[ExtractionChunk]) -> int | None:
    """The 1-based chunk whose window holds ``seconds``."""
    if seconds is None or len(chunks) < 2:
        return None
    for chunk in chunks:
        after_start = chunk.start_s is None or seconds >= chunk.start_s
        if after_start and (chunk.end_s is None or seconds < chunk.end_s):
            return chunk.index + 1
    return None


# ─── Lists ───


def value_at(extraction: Mapping[str, Any], path: str) -> object:
    """The value at ``domain.field``; None when the path breaks."""
    domain, _, name = path.partition(".")
    block = extraction.get(domain)
    return block.get(name) if isinstance(block, Mapping) else None


def _letter(name: str, taken: set[str]) -> str:
    wanted = _ID_LETTERS.get(name)
    if wanted is None or wanted in taken:
        wanted = next(letter for letter in _SPARE_LETTERS if letter not in taken)
    taken.add(wanted)
    return wanted


def _band_minimum(duration: int | None) -> int | None:
    """content-model §9's Moments minimum for the video's length (None = length unknown)."""
    if not duration or duration <= 0:
        return None
    minutes = duration / 60
    for band in moment_caps_by_duration():
        if band["maxMinutes"] is None or minutes <= band["maxMinutes"]:
            return band["min"]
    return None


def _gate_minimum(component: str) -> int:
    """The leading number of the component's ``densityGates`` minimum ("3 rows" → 3)."""
    found = _DIGITS_RE.match(str(density_gates().get(component, {}).get("min", "")))
    return int(found.group()) if found else 1


def _floor(component: str, duration: int | None) -> int:
    band = _band_minimum(duration) if component == TIMELINE_COMPONENT else None
    return band if band is not None else _gate_minimum(component)


def shipping_cap(path: str, component: str, duration: int | None) -> int | None:
    """The most items of ``path`` a ``component`` tab ships — the cap assembly cuts it to
    (``core._cap_tab_items``: a key-ideas tab's ``key_idea_cap``, else the component's
    ``assemblerItemCaps``); None when it ships whole (no cap, or a procedure). A timeline
    gets the lower of its two §9 caps, the label-only track's: which one applies is read
    off the assembled moments, so a list within it ships whole either way."""
    caps = assembler_item_caps()
    cap = caps.get(component)
    if cap is None or component not in COMPONENT_LIST_KEYS or component in SEQUENTIAL_COMPONENTS:
        return None
    if path == KEY_IDEAS_PATH:
        return key_idea_cap(duration, caps.get("spot_explorer", 25))
    if component == TIMELINE_COMPONENT:
        return moment_cap_for_duration(duration, cap)
    return cap


def _rank_cap(path: str, component: str, count: int, ships: int | None) -> int | None:
    """The cap a rankable list over it is cut to by rank (None = no rank)."""
    if path not in RANKED_FIELDS or component == TIMELINE_COMPONENT:
        return None
    return ships if ships is not None and count > ships else None


def _spec(
    path: str, tab: Mapping[str, Any], count: int, source: RenderInput, taken: set[str]
) -> ListSpec:
    component = str(tab.get("component") or "")
    ships = shipping_cap(path, component, source.duration)
    return ListSpec(
        path=path,
        label=bare_label(tab.get("label")) or path,
        component=component,
        letter=_letter(path.partition(".")[2], taken),
        floor=_floor(component, source.duration),
        ships=ships,
        cap=_rank_cap(path, component, count, ships),
        sectioned=path == KEY_IDEAS_PATH,
    )


def list_specs(source: RenderInput) -> list[ListSpec]:
    """One spec per non-empty list a planned tab reads, in plan order (first tab wins)."""
    specs: list[ListSpec] = []
    taken: set[str] = set()
    for tab in source.tabs:
        path = tab.get("dataSource")
        if not isinstance(path, str) or any(spec.path == path for spec in specs):
            continue
        items = value_at(source.extraction, path)
        if isinstance(items, list) and items:
            specs.append(_spec(path, tab, len(items), source, taken))
    return specs


# ─── Rendering ───


@dataclass(frozen=True)
class _Row:
    """One item ready to print at any level."""

    item: RankedItem
    body: str
    part: int | None


def _rows(spec: ListSpec, source: RenderInput) -> list[_Row]:
    items = value_at(source.extraction, spec.path)
    rows: list[_Row] = []
    for index, value in enumerate(items if isinstance(items, list) else []):
        head, seconds = item_head(value), item_seconds(value, source.duration)
        item_id = f"{spec.letter}{index + 1}"
        mark = fingerprint(value, source.duration)
        ranked = RankedItem(item_id, spec.path, index, head, seconds, mark)
        rows.append(_Row(ranked, _item_body(value, head), _part(seconds, source.chunks)))
    return rows


def _video_block(source: RenderInput) -> str:
    length = format_clock(source.duration) if source.duration else "unknown"
    title = _clean(source.title)[:TITLE_MAX_CHARS] or "Untitled"
    return f"<video>\nTitle: {title}\nLength: {length}\n</video>"


def _outline_block(outline: Sequence[OutlineSection]) -> str:
    rows = []
    for number, section in enumerate(outline, start=1):
        span = f"{format_clock(section.start)}–{format_clock(section.end)}"
        row = f"{number} [{span}] {_clean(section.title)}"
        rows.append(f"{row} — {_clean(section.establishes)}" if section.establishes else row)
    return "<outline>\n" + "\n".join(rows) + "\n</outline>"


def _header(spec: ListSpec) -> str:
    asks = " [rank]" if spec.cap else ""
    return f'{spec.path} — "{_clean(spec.label)}" ({spec.component}){asks}'


def _line(row: _Row, level: _Level) -> str:
    item = row.item
    clock = f" [{format_clock(item.seconds)}]" if item.seconds is not None else ""
    where = f" (part {row.part})" if row.part is not None else ""
    text = _words(item.head, level.head_words) if item.head else "(no text)"
    if row.body and level.body_words:
        text = f"{text} — {_words(row.body, level.body_words)}"
    return f"{item.item_id}{clock}{where} {text}"


def _lists_block(blocks: Sequence[tuple[ListSpec, list[_Row]]], level: _Level) -> str:
    parts = [
        "\n".join([_header(spec), *(_line(row, level) for row in rows)]) for spec, rows in blocks
    ]
    return "<lists>\n" + "\n\n".join(parts) + "\n</lists>"


def estimate_tokens(text: str) -> int:
    """Input tokens ``text`` costs on the Claude 5.5 tokenizer (see ``CHARS_PER_TOKEN``)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _fitted(head: str, blocks: Sequence[tuple[ListSpec, list[_Row]]], budget: int) -> str:
    """The input at the first level under ``budget`` tokens — the shortest level when none
    is (an item is never dropped)."""
    text = ""
    for level in _LEVELS:
        text = f"{head}\n\n{_lists_block(blocks, level)}"
        if estimate_tokens(text) <= budget:
            return text
    return text


def build_request(
    source: RenderInput, budget: int = MERGE_RANK_INPUT_MAX_TOKENS
) -> MergeRankRequest | None:
    """The call's input over every planned list; None when no planned tab reads a list."""
    specs = list_specs(source)
    if not specs:
        return None
    outline = tuple(source.outline) if len(source.outline) >= 2 else ()
    blocks = [(spec, _rows(spec, source)) for spec in specs]
    head = "\n\n".join([_video_block(source), *([_outline_block(outline)] if outline else [])])
    text = _fitted(head, blocks, budget)
    items = {row.item.item_id: row.item for _, rows in blocks for row in rows}
    return MergeRankRequest(text, tuple(specs), items, outline, source.duration)
