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
from typing import Any

from src.shared_config.assembly_policy import assembly_policy
from src.shared_config.domain_config import quiz_policy

from .registry import ASSEMBLER_REGISTRY
from .stat_banner import _CLAUSE_BREAK as _CLAUSE_BREAK
from .stat_banner import _CLAUSE_EDGE as _CLAUSE_EDGE
from .stat_banner import _LABEL_DANGLING as _LABEL_DANGLING
from .stat_banner import _NUMBER_RE as _NUMBER_RE
from .stat_banner import _STAT_BANNER_MAX as _STAT_BANNER_MAX
from .stat_banner import _STAT_BANNER_MIN as _STAT_BANNER_MIN
from .stat_banner import _STAT_LABEL_MAX_CHARS as _STAT_LABEL_MAX_CHARS
from .stat_banner import _STAT_LABEL_MAX_WORDS as _STAT_LABEL_MAX_WORDS
from .stat_banner import _YEAR as _YEAR
from .stat_banner import _is_figure as _is_figure
from .stat_banner import _key_points as _key_points
from .stat_banner import _phrase as _phrase
from .stat_banner import _stat_banner_attachment as _stat_banner_attachment
from .stat_banner import _stat_figure as _stat_figure
from .stat_banner import _stat_label as _stat_label
from .stat_banner import _stat_tile as _stat_tile
from .stat_banner import _stat_value as _stat_value
from .text_utils import truncate_words

logger = logging.getLogger(__name__)

# Tabs whose primary list is at or below this length are "sparse" — they have
# room for an enriching attachment without crowding; at or above the dense one a
# top "First up" header anchors the long scroll (domains.json ``assemblyPolicy``).
SPARSE_THRESHOLD = assembly_policy()["sparseItemsMax"]
_DENSE_THRESHOLD = assembly_policy()["denseItemsMin"]
# A summary_header is one orientation line, never a paragraph.
_HEADER_MAX_CHARS = 220
# An item's headline / body fields, in priority order (mirrors the FE's
# text-key probe — minus a flash card's front/back, see below — so "the first
# item" reads the same server- and client-side).
_ITEM_HEADLINE_KEYS = (
    "label",
    "title",
    "name",
    "term",
    "question",
    "claim",
    "text",
    "instruction",
    "key",
)
_ITEM_BODY_KEYS = ("description", "detail", "definition", "summary", "explanation", "value")
# A flash deck is a self-test: a header printing card 1 gives its answer away.
_NO_SUMMARY_HEADER_COMPONENTS = frozenset({"flash_deck"})
# Attachments built from response-global data: ONE of each per response, or
# every tab repeats the identical payload.
ONCE_PER_RESPONSE = frozenset({"frame_strip", "quick_quiz", "tip_callout"})

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
_NO_FRAME_STRIP_DOMAINS = frozenset(assembly_policy()["noFilmstripDomains"])


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


def _line_key(text: str) -> str:
    """A line as the "never the goal" guard compares it — inner whitespace
    collapsed, trailing ``.!?`` dropped, case folded (the FE's ComposableOutput
    hides a stored header equal to the goal by the same rule)."""
    return " ".join(text.split()).rstrip(".!?").rstrip().casefold()


def _same_line(a: str | None, b: str | None) -> bool:
    """Equality under ``_line_key`` for the "never the goal" guard."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return _line_key(a) == _line_key(b)


def _summary_header_attachment(
    tab: dict, extraction: dict | None, domain: str, is_answer_tab: bool, dense: bool
) -> dict | None:
    """Build a top summary_header with content (R4, 0b.6): the extraction's
    ``summary`` ("In short") on the answer tab whatever its size, else — on a
    dense tab only — the tab's first item ("First up"); never the goal, which
    the FE already prints as the tab's intro line. None for a flash deck
    (``_NO_SUMMARY_HEADER_COMPONENTS``)."""
    if tab.get("component") in _NO_SUMMARY_HEADER_COMPONENTS:
        return None
    summary = _extraction_summary(extraction, domain) if is_answer_tab else None
    title = "In short"
    # A summary that only repeats the goal is none: a dense tab still gets its first item.
    if summary is not None and _same_line(summary, tab.get("goal")):
        summary = None
    if summary is None and dense:
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

    The answer tab carries a top "In short" summary_header (the extraction
    summary) and, with enough figures in its key points, a bottom stat_banner
    (C8) — both whatever its size or shape. Another dense tab (≥
    _DENSE_THRESHOLD items), or a dense answer tab with no summary, gets a
    "First up" header (its first item). A sparse tab (≤ SPARSE_THRESHOLD items)
    gets ONE enriching bottom attachment: frame_strip → quick_quiz → tip_callout.

    Those three repeat a response-global payload, so the caller passes the kinds
    already used (`excluded_kinds`), each skipped for the next. ``is_answer_tab``
    marks the first content tab (proposal §2); the caller knows the strip order.
    """
    component = tab.get("component", "")
    props = tab.get("props")
    if not isinstance(props, dict):
        return []

    count = _primary_item_count(component, props)
    dense = count is not None and count >= _DENSE_THRESHOLD
    attachments: list[dict] = []

    if is_answer_tab or dense:
        header = _summary_header_attachment(tab, extraction, domain, is_answer_tab, dense)
        if header is not None:
            attachments.append(header)

    if count is not None and count <= SPARSE_THRESHOLD:
        attachment = _sparse_enrichment(
            component, extraction, enrichment, frames, domain, excluded_kinds
        )
        if attachment is not None:
            attachments.append(attachment)

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
    after it is the answer tab — unless it takes no attachment at all (a flash
    deck, or any tab when the extraction has no summary and too few figures for
    a numbers strip): then the answer, its "In short" header and numbers strip,
    moves on to the next content tab. ONCE_PER_RESPONSE kinds are budgeted to one
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
        if not secondaries:
            continue
        answer_pending = False
        # After a folded info_grid (R6) the tab may already carry an attachment.
        tab["attachments"] = [*secondaries, *(tab.get("attachments") or [])]
        used_kinds |= {a["component"] for a in secondaries if a["component"] in ONCE_PER_RESPONSE}
