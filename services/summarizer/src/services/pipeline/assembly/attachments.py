"""Secondary-tier attachment builder (interactive-overhaul-v2 P2).

A tab is a single PRIMARY interactive plus optional SECONDARY attachments that
enrich a sparse tab (a frame strip, a quick quiz, a tip) or break up a dense one
(a summary header on top). This module owns that decision: it is authoritative,
data-driven, and runs in ``assemble_response`` after each tab is built. The plan
prompt only *hints* (an ``enrich`` flag); the assembler decides.

Secondary attachments are never standalone tabs — they always hang off a primary
tab. Each attachment is shaped ``{slot, component, props, size?}`` and is passed
through the same assembler + validation path as a primary so a malformed
attachment is dropped rather than shipped empty.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.shared_config.domain_config import quiz_policy

from .derived_guard import anchor_seconds
from .registry import ASSEMBLER_REGISTRY
from .text_utils import truncate_words

logger = logging.getLogger(__name__)

# Tabs whose primary list is at or below this length are "sparse" — they have
# room for an enriching attachment without crowding.
SPARSE_THRESHOLD = 4
# Tabs whose primary list is at or above this length are "dense" — a top
# summary header gives the reader an anchor before the long scroll.
_DENSE_THRESHOLD = 10
# A summary_header is one orientation line, never a paragraph.
_HEADER_MAX_CHARS = 220
# An item's headline / body fields, in priority order (mirrors the FE's
# text-key probe so "the first item" reads the same server- and client-side).
_ITEM_HEADLINE_KEYS = (
    "label",
    "title",
    "name",
    "term",
    "question",
    "claim",
    "text",
    "front",
    "instruction",
)
_ITEM_BODY_KEYS = ("description", "detail", "definition", "summary", "explanation", "back")
# Attachments built from response-global data: ONE of each per response, or
# every tab repeats the identical payload.
ONCE_PER_RESPONSE = frozenset({"frame_strip", "quick_quiz", "tip_callout"})

# stat_banner (C8): the answer tab carries a numbers strip when the key points
# hold at least this many figures with a timestamp; at most _STAT_BANNER_MAX
# tiles so it stays a strip, not a wall.
_STAT_BANNER_MIN = 5
_STAT_BANNER_MAX = 8
# The tile label is the FE's small-caps line above the value; the full meaning
# travels alongside it in ``meaning``.
_STAT_LABEL_MAX_CHARS = 56
# A figure as a speaker says one: "150,000", "$20 million", "99%", "1,000x",
# "0.5 MB", "3:33", "20 months". A letter or digit glued to the end ("1950s",
# "v2.5", "x86") is not a figure.
_NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<figure>[$€£]?\d[\d,]*(?:\.\d+)?(?::\d{2})?"
    r"(?:\s?(?:%|x|×|percent|thousand|million|billion|trillion)"
    r"|\s(?:MB|GB|KB|TB|ms|seconds?|minutes?|hours?|days?|weeks?|months?|years?|lines?|apps?))?)"
    r"(?![\w])",
    re.IGNORECASE,
)

# Component → the props key holding its primary item list. Mirrors the frontend
# COUNT_PROP_BY_COMPONENT so "how many items does this tab show" is consistent.
_PRIMARY_LIST_KEY: dict[str, str] = {
    "spot_explorer": "spots",
    "moment_track": "items",
    "info_grid": "items",
    "checklist": "items",
    "step_player": "steps",
    "step_flow_canvas": "steps",
    "flash_deck": "cards",
    "comparison": "comparisons",
    "comparison_radar": "comparisons",
    "code_playground": "snippets",
    "concept_canvas": "concepts",
    "workout_room": "exercises",
    "packing_mission": "items",
}

# Components that already carry their own frames/quiz/visual hero — attaching a
# frame_strip or quick_quiz would be redundant.
_NO_FRAME_STRIP_COMPONENTS = frozenset({"video_filmstrip", "frame_strip"})
_NO_QUICK_QUIZ_COMPONENTS = frozenset({"quiz_arena", "quick_quiz"})
# Domains where the filmstrip never adds value (talking-head footage).
_NO_FRAME_STRIP_DOMAINS = frozenset({"narrative", "music"})


def can_host_quick_quiz(component: str) -> bool:
    """True when a tab rendered as ``component`` may carry a quick_quiz strip.

    A host needs a primary item list (attachments only go on list tabs), must
    not be a quiz itself, and must not be a do-along surface that
    ``quizPolicy.attachmentHostsExclude`` lists (a quiz under a recipe's steps
    or a workout interrupts the task). Read per call: the registry is the
    source, never an import-time snapshot. Also the enrichment demand gate's
    plan-time view of "an allowed quick_quiz host" (``enrichment.needs_quiz``).
    """
    if component not in _PRIMARY_LIST_KEY or component in _NO_QUICK_QUIZ_COMPONENTS:
        return False
    return component not in quiz_policy()["attachmentHostsExclude"]


def _primary_item_count(component: str, props: dict) -> int | None:
    """Length of the tab's primary list, or None when it has no list."""
    key = _PRIMARY_LIST_KEY.get(component)
    if key is None:
        return None
    value = props.get(key)
    return len(value) if isinstance(value, list) else None


def _build_attachment(
    component: str,
    slot: str,
    data: Any,
    size: str | None = None,
) -> dict | None:
    """Assemble + validate one secondary; return the attachment dict or None."""
    from .core import _validate_assembled_props  # local import avoids cycle

    assembler = ASSEMBLER_REGISTRY.get(component)
    if assembler is None:
        return None
    props = assembler({}, data, {}, None)
    if props is None or not _validate_assembled_props(component, props):
        return None
    attachment: dict[str, Any] = {"slot": slot, "component": component, "props": props}
    if size:
        attachment["size"] = size
    return attachment


def _frame_strip_attachment(frames: list[dict] | None) -> dict | None:
    """Build a bottom frame_strip from the same normalized frames a filmstrip
    tab would use. Returns None when there aren't enough frames."""
    if not frames or len(frames) < 3:
        return None
    return _build_attachment("frame_strip", "bottom", {"frames": frames}, size="strip")


def _quick_quiz_attachment(enrichment: dict | None) -> dict | None:
    """Build a bottom single-question quick_quiz from enrichment.quiz."""
    if not isinstance(enrichment, dict):
        return None
    quiz = enrichment.get("quiz")
    if not isinstance(quiz, list) or not quiz:
        return None
    return _build_attachment("quick_quiz", "bottom", quiz[:1], size="strip")


def _tip_callout_attachment(extraction: dict | None, domain: str) -> dict | None:
    """Build a bottom tip_callout from the first domain tip available."""
    if not isinstance(extraction, dict):
        return None
    domain_data = extraction.get(domain)
    if not isinstance(domain_data, dict):
        return None
    for field in ("tips", "savingTips", "proTips", "studyTips", "safetyWarnings"):
        tips = domain_data.get(field)
        if isinstance(tips, list) and tips:
            first = tips[0]
            text = (
                first
                if isinstance(first, str)
                else (first.get("text") if isinstance(first, dict) else None)
            )
            if isinstance(text, str) and text.strip():
                style = "warning" if field == "safetyWarnings" else "tip"
                return _build_attachment(
                    "tip_callout",
                    "bottom",
                    {"text": text.strip(), "style": style},
                )
    return None


def _first_text(item: dict, keys: tuple[str, ...]) -> str | None:
    """The first non-blank string under ``keys``, stripped."""
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_item_line(component: str, props: dict) -> str | None:
    """One line for the tab's first primary item: ``headline — body`` when the
    item carries both, else whichever it has. None for a textless item."""
    key = _PRIMARY_LIST_KEY.get(component)
    items = props.get(key) if key else None
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if isinstance(first, str):
        return first.strip() or None
    if not isinstance(first, dict):
        return None
    headline = _first_text(first, _ITEM_HEADLINE_KEYS)
    body = _first_text(first, _ITEM_BODY_KEYS)
    if headline and body:
        return f"{headline} — {body}"
    return headline or body


def _extraction_summary(extraction: dict | None, domain: str) -> str | None:
    """The extraction's ``summary`` scalar: the primary domain's first, else the
    first other domain that wrote one (a tech video summarised under learning)."""
    if not isinstance(extraction, dict):
        return None
    ordered = [extraction.get(domain), *(v for k, v in extraction.items() if k != domain)]
    for domain_data in ordered:
        if not isinstance(domain_data, dict):
            continue
        summary = domain_data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return None


def _same_line(a: str | None, b: str | None) -> bool:
    """Case/punctuation-insensitive equality for the "never the goal" guard."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return a.strip().rstrip(".!").casefold() == b.strip().rstrip(".!").casefold()


def _stat_value(title: str) -> str | None:
    """The headline figure in a key-point title: a figure with a unit, suffix
    or currency beats a bare number, a multi-digit number beats a single digit,
    and the earliest wins a tie. None when the title holds no figure."""
    best: tuple[int, int, str] | None = None
    for index, match in enumerate(_NUMBER_RE.finditer(title)):
        figure = match.group("figure")
        digits = sum(ch.isdigit() for ch in figure)
        score = (2 if not figure.rstrip().isdigit() else 0) + (1 if digits >= 2 else 0)
        if best is None or score > best[0]:
            best = (score, index, figure)
    return best[2] if best else None


def _key_points(extraction: dict | None, domain: str) -> list[dict]:
    """The first ``keyPoints`` list in the extraction, primary domain first."""
    if not isinstance(extraction, dict):
        return []
    ordered = [extraction.get(domain), *(v for k, v in extraction.items() if k != domain)]
    for domain_data in ordered:
        points = domain_data.get("keyPoints") if isinstance(domain_data, dict) else None
        if isinstance(points, list) and points:
            return [kp for kp in points if isinstance(kp, dict)]
    return []


def _stat_banner_attachment(extraction: dict | None, domain: str) -> dict | None:
    """Build the answer tab's numbers strip (C8) from the numeric key points:
    ``{label, value, emoji?, meaning, timestamp}`` per tile, where ``label`` +
    ``value`` are what StatBanner renders and ``meaning`` + ``timestamp`` ride
    along (the FE schema passes extra keys through). Built here, not via the
    registered assembler, because that one keeps only label/value/emoji and
    the timestamp is the point (R2). None below _STAT_BANNER_MIN figures."""
    from .core import _validate_assembled_props  # local import avoids cycle

    stats: list[dict[str, Any]] = []
    for point in _key_points(extraction, domain):
        title = str(point.get("title") or "").strip()
        value = _stat_value(title) if title else None
        timestamp = anchor_seconds(point)
        if value is None or timestamp is None:
            continue
        stat: dict[str, Any] = {
            "label": truncate_words(title, _STAT_LABEL_MAX_CHARS),
            "value": value,
            "meaning": title,
            "timestamp": timestamp,
        }
        if isinstance(point.get("emoji"), str) and point["emoji"].strip():
            stat["emoji"] = point["emoji"].strip()
        stats.append(stat)
    if len(stats) < _STAT_BANNER_MIN:
        return None
    props = {"stats": stats[:_STAT_BANNER_MAX]}
    if not _validate_assembled_props("stat_banner", props):
        return None
    return {"slot": "bottom", "component": "stat_banner", "props": props, "size": "strip"}


def _summary_header_attachment(
    tab: dict, extraction: dict | None, domain: str, is_answer_tab: bool
) -> dict | None:
    """Build a top summary_header with content (R4, 0b.6): the extraction's
    ``summary`` on the answer tab, otherwise the tab's first item — never the
    goal, which the FE already prints as the tab's intro line."""
    summary = _extraction_summary(extraction, domain) if is_answer_tab else None
    title = "In short"
    if summary is None:
        summary = _first_item_line(tab.get("component", ""), tab.get("props") or {})
        title = "First up"
    if summary is None or _same_line(summary, tab.get("goal")):
        return None
    return _build_attachment(
        "summary_header",
        "top",
        {"summary": truncate_words(summary, _HEADER_MAX_CHARS), "title": title, "emoji": "🧭"},
        size="banner",
    )


def attach_secondaries(
    tab: dict,
    extraction: dict | None,
    enrichment: dict | None,
    frames: list[dict] | None,
    domain: str,
    excluded_kinds: set[str] | frozenset[str] = frozenset(),
    is_answer_tab: bool = False,
) -> list[dict]:
    """Decide and build secondary attachments for a single assembled tab.

    Sparse tab (≤ SPARSE_THRESHOLD items): add ONE enriching bottom attachment,
    trying frame_strip → quick_quiz → tip_callout in priority order. Dense tab
    (≥ _DENSE_THRESHOLD items): add a top summary_header carrying content (the
    extraction summary on the answer tab, else the first item). Tabs in
    between get nothing. The answer tab additionally carries a bottom
    stat_banner when the key points hold enough figures (C8), whatever its
    size. Returns a (possibly empty) list of attachment dicts.

    frame_strip/quick_quiz/tip_callout are built from response-global data, so
    repeating one across tabs shows the identical payload N times — the caller
    passes already-used kinds via `excluded_kinds` and each is skipped in favor
    of the next in the chain. ``is_answer_tab`` marks the first content tab
    (the one that answers the video's question — proposal §2); the caller knows
    the strip order, this function does not.
    """
    component = tab.get("component", "")
    props = tab.get("props")
    if not isinstance(props, dict):
        return []

    count = _primary_item_count(component, props)
    if count is None:
        return []

    attachments: list[dict] = []

    if count <= SPARSE_THRESHOLD:
        attachment = _sparse_enrichment(
            component, extraction, enrichment, frames, domain, excluded_kinds
        )
        if attachment is not None:
            attachments.append(attachment)

    elif count >= _DENSE_THRESHOLD:
        header = _summary_header_attachment(tab, extraction, domain, is_answer_tab)
        if header is not None:
            attachments.append(header)

    if is_answer_tab:
        banner = _stat_banner_attachment(extraction, domain)
        if banner is not None:
            attachments.append(banner)

    return attachments


def _sparse_enrichment(
    component: str,
    extraction: dict | None,
    enrichment: dict | None,
    frames: list[dict] | None,
    domain: str,
    excluded_kinds: set[str] | frozenset[str],
) -> dict | None:
    """ONE enriching bottom attachment for a sparse tab: frame_strip →
    quick_quiz → tip_callout, skipping kinds the response already used."""
    attachment = None
    if (
        "frame_strip" not in excluded_kinds
        and component not in _NO_FRAME_STRIP_COMPONENTS
        and domain not in _NO_FRAME_STRIP_DOMAINS
    ):
        attachment = _frame_strip_attachment(frames)
    if attachment is None and "quick_quiz" not in excluded_kinds and can_host_quick_quiz(component):
        attachment = _quick_quiz_attachment(enrichment)
    if attachment is None and "tip_callout" not in excluded_kinds:
        attachment = _tip_callout_attachment(extraction, domain)
    return attachment


def attach_response_secondaries(
    tabs: list[dict],
    extraction: dict | None,
    enrichment: dict | None,
    frames: list[dict] | None,
    domain: str,
) -> None:
    """Decorate every content tab of a response in place.

    The overview is its own hero and is never decorated; the first content tab
    after it is the answer tab. ONCE_PER_RESPONSE kinds are budgeted to one
    appearance, and a filmstrip tab (planned or auto-appended) pre-consumes the
    frame_strip budget because it already shows the gallery.
    """
    used_kinds: set[str] = (
        {"frame_strip"} if any(t.get("component") == "video_filmstrip" for t in tabs) else set()
    )
    answer_pending = True
    for tab in tabs:
        if tab.get("component") == "overview":
            continue
        secondaries = attach_secondaries(
            tab,
            extraction,
            enrichment,
            frames,
            domain,
            excluded_kinds=used_kinds,
            is_answer_tab=answer_pending,
        )
        answer_pending = False
        if secondaries:
            tab["attachments"] = secondaries
            used_kinds |= {
                a["component"] for a in secondaries if a["component"] in ONCE_PER_RESPONSE
            }
