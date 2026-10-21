"""Nothing names a tab that does not ship (output-quality 2.0 b, G1-2).

Gate 1 saw S09's hero promise "Visual Moments" and S11's "Vietnam Scenes" — filmstrip
tabs the web never showed, because ``OutputRouter`` hides a ``video_filmstrip`` once
a ``moment_track`` carries ``RICH_MOMENT_FRAMES`` frame-backed items (its grid
already IS a filmstrip with context). Two rules make the stored doc say what the page
shows:

- **the shadowed filmstrip does not ship** — the web's rule, run server-side with the
  same threshold and the same "frame-backed" reading (a ``thumbnailUrl`` string, or
  an ``s3Key`` the API signs into one — ``refreshFrameUrls``); the web keeps its copy
  for older rows;
- **no hero line, goal or link names a tab that does not ship** — a tab the plan
  named or assembly may add, missing from the final list: every sentence of the hero
  (meta ``tldr`` / ``masterSummary`` / ``keyTakeaways`` / ``seoDescription`` and the
  overview's copy) and of each tab's goal that names its label — emoji out,
  case-insensitive, whole words, not inside a shipped tab's longer label — is
  removed whole. A one-word label ("Moments", "Tips") names the tab only in a
  sentence that reads as a tab reference: it says "tab(s)" / "section(s)", or
  carries the label in its exact case as a capitalized word past the sentence's
  first word ("the key moments come late" stays). Other sentences are never
  rewritten; links to it are pruned.

``assemble_response`` drops the shadowed filmstrip before links and attachments and
scrubs once the tab list is final; the pipeline runs :func:`settle_shipped_tabs`
again after the late group (``phases.synthesis.settle_tab_references``), because
the moment fill frames the Moments and synthesis writes the hero after assembly.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.shared_config.assembly_policy import injected_tab

from .cross_tab import prune_links

logger = logging.getLogger(__name__)

FILMSTRIP_COMPONENT = "video_filmstrip"
MOMENT_COMPONENT = "moment_track"
OVERVIEW_COMPONENT = "overview"
FRAMES_GALLERY_ID = "frames-gallery"
# OutputRouter.tsx ``hasRichMomentTrack``: this many frame-backed moments shadow a filmstrip.
RICH_MOMENT_FRAMES = 8
# droppedTabs reasons.
FILMSTRIP_SHADOWED = "filmstrip_shadowed_by_moments"
EMPTY_PROPS = "empty_props"
# refreshFrameUrls (api) signs a thumbnailUrl for every object whose s3Key lies here.
_FRAME_KEY_PREFIX = "videos/"
_META_TEXT_KEYS = ("tldr", "masterSummary", "seoDescription")
_OVERVIEW_TEXT_KEYS = ("subtitle", "tldr", "masterSummary")
_TAKEAWAYS_KEY = "keyTakeaways"
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_BLANK_LINES = re.compile(r"\n{3,}")
# A sentence that speaks of tabs: a one-word label in it is a tab's name.
_TAB_WORD = re.compile(r"\b(?:tabs?|sections?)\b", re.IGNORECASE)
# Emoji and their glue: symbols, modifiers, joiners / marks, enclosing keycaps.
_EMOJI_CATEGORIES = frozenset({"So", "Sk", "Cf", "Me"})
_VARIATION_SELECTORS = ("︀", "️")


# ─── The filmstrip the framed Moments shadow ───


def _frame_backed(item: object) -> bool:
    """An item the web counts as framed once the API refreshed its URLs."""
    if not isinstance(item, Mapping):
        return False
    url = item.get("thumbnailUrl")
    if isinstance(url, str) and url:
        return True
    key = item.get("s3Key")
    if not isinstance(key, str) or not key.startswith(_FRAME_KEY_PREFIX):
        return False
    # The API refreshes a string thumbnailUrl, or adds one when no url key exists.
    return isinstance(url, str) or ("thumbnailUrl" not in item and "url" not in item)


def frame_backed_count(props: object) -> int:
    """``countFrameBackedItems`` (OutputRouter.tsx): the framed items of ``props.items``."""
    items = props.get("items") if isinstance(props, Mapping) else None
    return sum(1 for item in items if _frame_backed(item)) if isinstance(items, list) else 0


def drop_shadowed_filmstrips(tabs: list[dict]) -> list[dict]:
    """Remove every filmstrip tab when a Moments tab carries ``RICH_MOMENT_FRAMES`` framed
    items. Mutates ``tabs``; returns the removed tabs."""
    rich = any(
        tab.get("component") == MOMENT_COMPONENT
        and frame_backed_count(tab.get("props")) >= RICH_MOMENT_FRAMES
        for tab in tabs
    )
    removed = [tab for tab in tabs if rich and tab.get("component") == FILMSTRIP_COMPONENT]
    if removed:
        tabs[:] = [tab for tab in tabs if tab.get("component") != FILMSTRIP_COMPONENT]
        logger.info(
            "Assembly: filmstrip %s dropped — the Moments track already shows its frames",
            [tab.get("id") for tab in removed],
        )
    return removed


def planned_tab_sources(raw_tabs: Iterable[Any]) -> dict[str, str]:
    """Planned tab id → its dataSource (the droppedTabs record of a tab dropped late)."""
    return {
        str(tab["id"]): str(tab.get("dataSource", ""))
        for tab in raw_tabs
        if isinstance(tab, Mapping) and tab.get("id")
    }


def drop_records(
    removed: Iterable[Mapping[str, Any]], reason: str, planned: Mapping[str, str]
) -> list[dict[str, Any]]:
    """droppedTabs records for the removed tabs the plan designed (an added tab was never one)."""
    return [
        {
            "id": tab["id"],
            "component": tab.get("component"),
            "dataSource": planned[tab["id"]],
            "reason": reason,
        }
        for tab in removed
        if tab.get("id") in planned
    ]


def recount_overview(tabs: list[dict]) -> None:
    """The overview's content-tab count — ``data.itemCount`` and its "Items" stat chip —
    over ``tabs`` as they ship. No-op without an overview first or a content tab."""
    if not tabs or tabs[0].get("component") != OVERVIEW_COMPONENT or len(tabs) < 2:
        return
    props = tabs[0].get("props")
    data = props.setdefault("data", {}) if isinstance(props, dict) else None
    if not isinstance(data, dict):
        return
    item_count = len(tabs) - 1
    data["itemCount"] = item_count
    existing = data.get("stats")
    stats: list[Any] = existing if isinstance(existing, list) else []
    chip = next((s for s in stats if isinstance(s, dict) and s.get("label") == "Items"), None)
    if chip is None:
        stats.append({"label": "Items", "value": str(item_count), "emoji": "📚"})
    else:
        chip["value"] = str(item_count)
    data["stats"] = stats


# ─── A degraded tab's goal ───

# The plan writes a tab's goal before extraction; when assembly degrades the tab
# (2.0 c: a tier list whose tiers no one stated ships as a plain list), a goal
# clause naming the lost structure is a broken promise — S09's untiered "The
# Pulls" still said "…and why each sits in its tier".
_LOST_STRUCTURE_WORDS: dict[str, tuple[str, ...]] = {
    "tier_list": ("tier", "tiers", "tiered", "rank", "ranks", "ranked", "ranking", "rankings"),
}
_GOAL_CLAUSE_SPLIT = re.compile(r",\s+|\s+and\s+")


def goal_without_lost_structure(goal: str, degraded_from: str | None) -> str:
    """``goal`` minus each clause that names the structure a degraded tab lost.

    Unchanged when no clause names it, or when the opening clause does (dropping
    it would leave no goal to read).
    """
    words = _LOST_STRUCTURE_WORDS.get(degraded_from or "")
    if not words or not goal:
        return goal
    names = re.compile(rf"\b(?:{'|'.join(words)})\b", re.IGNORECASE)
    clauses = _GOAL_CLAUSE_SPLIT.split(goal.strip().rstrip("."))
    kept = [clause for clause in clauses if not names.search(clause)]
    if len(kept) == len(clauses) or names.search(clauses[0]):
        return goal
    return f"{' and '.join(kept)}."


# ─── References to tabs that do not ship ───


def bare_label(label: object) -> str:
    """A tab label as prose names it: emoji and their joiners out, spaces collapsed."""
    if not isinstance(label, str):
        return ""
    kept = "".join(
        char
        for char in label
        if unicodedata.category(char) not in _EMOJI_CATEGORIES
        and not _VARIATION_SELECTORS[0] <= char <= _VARIATION_SELECTORS[1]
    )
    return " ".join(kept.split())


def _label_pattern(label: str, *, exact_case: bool = False) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(word) for word in label.split())
    return re.compile(rf"(?<!\w){words}(?!\w)", 0 if exact_case else re.IGNORECASE)


@dataclass(frozen=True)
class _DroppedName:
    """A dropped tab's label as a sentence may name it; ``exact`` only for a one-word
    label that starts with a capital (its proper-noun spelling)."""

    pattern: re.Pattern[str]
    single_word: bool
    exact: re.Pattern[str] | None

    @classmethod
    def of(cls, label: str) -> _DroppedName:
        single = len(label.split()) == 1
        exact = _label_pattern(label, exact_case=True) if single and label[:1].isupper() else None
        return cls(_label_pattern(label), single, exact)

    def reads_as_tab(self, sentence: str, hits: list[tuple[int, int]]) -> bool:
        """A multi-word label always; a one-word one only in a sentence about tabs, or
        capitalized as the label is, past the sentence's first word."""
        if not self.single_word or _TAB_WORD.search(sentence):
            return True
        opening = len(sentence) - len(sentence.lstrip())
        exact = {m.span() for m in self.exact.finditer(sentence)} if self.exact else set()
        return any(span in exact and span[0] > opening for span in hits)


def auto_tab_candidates() -> list[dict[str, str]]:
    """The tab assembly adds on its own that may not ship: the frames gallery (no frames,
    too few, or shadowed by the Moments)."""
    return [{"id": FRAMES_GALLERY_ID, "label": injected_tab("framesGallery")["label"]}]


def unshipped_labels(candidates: Iterable[Any], tabs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Bare labels of the candidate tabs whose id does not ship — minus a label a shipped
    tab also carries (that name still points at a real tab)."""
    shipped_ids = {tab.get("id") for tab in tabs}
    shipped = {bare_label(tab.get("label")).casefold() for tab in tabs}
    names: list[str] = []
    for tab in candidates:
        if not isinstance(tab, Mapping) or tab.get("id") in shipped_ids:
            continue
        name = bare_label(tab.get("label"))
        if name and name.casefold() not in shipped and name not in names:
            names.append(name)
    return names


@dataclass(frozen=True)
class TabNames:
    """The labels a sentence may not name, and the shipped labels that may contain them."""

    dropped: tuple[_DroppedName, ...]
    shipped: tuple[re.Pattern[str], ...]

    @classmethod
    def of(cls, dropped: Iterable[str], shipped: Iterable[str]) -> TabNames:
        return cls(
            tuple(_DroppedName.of(name) for name in dropped if name),
            tuple(_label_pattern(name) for name in shipped if name),
        )

    def named_in(self, sentence: str) -> bool:
        """Whether ``sentence`` names a dropped tab outside every shipped tab's longer label."""
        for name in self.dropped:
            hits = [match.span() for match in name.pattern.finditer(sentence)]
            if not hits:
                continue
            covers = [m.span() for shipped in self.shipped for m in shipped.finditer(sentence)]
            free = [hit for hit in hits if not _inside_longer(hit, covers)]
            if free and name.reads_as_tab(sentence, free):
                return True
        return False


def _inside_longer(span: tuple[int, int], covers: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(s <= start and end <= e and e - s > end - start for s, e in covers)


def _scrub_paragraph(paragraph: str, names: TabNames) -> str:
    sentences = _SENTENCE_BREAK.split(paragraph)
    kept = [sentence for sentence in sentences if not names.named_in(sentence)]
    return paragraph if len(kept) == len(sentences) else " ".join(kept)


def scrub_sentences(text: str, names: TabNames) -> str:
    """``text`` without the sentences that name a dropped tab; unchanged when none does."""
    if not text or not names.dropped:
        return text
    lines = text.split("\n")
    scrubbed = [_scrub_paragraph(line, names) for line in lines]
    if scrubbed == lines:
        return text
    return _BLANK_LINES.sub("\n\n", "\n".join(scrubbed)).strip()


def _scrub_fields(record: dict, text_keys: Sequence[str], names: TabNames) -> int:
    """Scrub ``record``'s hero text fields and its takeaways in place; the fields changed."""
    changed = 0
    for key in text_keys:
        value = record.get(key)
        if isinstance(value, str) and (scrubbed := scrub_sentences(value, names)) != value:
            record[key], changed = scrubbed, changed + 1
    takeaways = record.get(_TAKEAWAYS_KEY)
    if isinstance(takeaways, list):
        kept = [scrub_sentences(t, names) if isinstance(t, str) else t for t in takeaways]
        kept = [t for t in kept if t != ""]
        if kept != takeaways:
            record[_TAKEAWAYS_KEY], changed = kept, changed + 1
    return changed


def scrub_tab_references(meta: dict, tabs: list[dict], candidates: Iterable[Any]) -> int:
    """Remove every hero / overview / goal sentence that names a candidate tab not in
    ``tabs``, in place. Returns the number of fields changed."""
    dropped = unshipped_labels(candidates, tabs)
    if not dropped:
        return 0
    names = TabNames.of(dropped, (bare_label(tab.get("label")) for tab in tabs))
    changed = _scrub_fields(meta, _META_TEXT_KEYS, names)
    for tab in tabs:
        props = tab.get("props")
        data = props.get("data") if isinstance(props, dict) else None
        if tab.get("component") == OVERVIEW_COMPONENT and isinstance(data, dict):
            changed += _scrub_fields(data, _OVERVIEW_TEXT_KEYS, names)
        goal = tab.get("goal")
        if isinstance(goal, str) and (scrubbed := scrub_sentences(goal, names)) != goal:
            tab["goal"], changed = scrubbed, changed + 1
    if changed:
        logger.info(
            "Assembly: %d field(s) no longer name tabs that do not ship: %s", changed, dropped
        )
    return changed


# ─── The pipeline's last pass ───


def settle_shipped_tabs(
    meta: dict, tabs: list[dict], candidates: Sequence[Any], planned: Mapping[str, str]
) -> list[dict]:
    """Once the late group is done: a filmstrip the now-framed Moments shadow goes (its
    links too, the overview recounts) and nothing names a tab that does not ship.
    Returns droppedTabs records for the planned tabs it removed."""
    removed = drop_shadowed_filmstrips(tabs)
    if removed:
        prune_links(tabs)
        recount_overview(tabs)
    scrub_tab_references(meta, tabs, [*candidates, *removed, *auto_tab_candidates()])
    return drop_records(removed, FILMSTRIP_SHADOWED, planned)
