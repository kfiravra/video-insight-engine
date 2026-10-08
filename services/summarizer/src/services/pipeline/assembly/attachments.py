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

from .registry import ASSEMBLER_REGISTRY

logger = logging.getLogger(__name__)

# Tabs whose primary list is at or below this length are "sparse" — they have
# room for an enriching attachment without crowding.
_SPARSE_THRESHOLD = 4
# Tabs whose primary list is at or above this length are "dense" — a top
# summary header gives the reader an anchor before the long scroll.
_DENSE_THRESHOLD = 10

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

    A host needs a primary item list (attachments only go on list tabs) and must
    not be a quiz itself. Also the enrichment demand gate's plan-time view of
    "an allowed quick_quiz host" (``enrichment.needs_quiz``).
    """
    return component in _PRIMARY_LIST_KEY and component not in _NO_QUICK_QUIZ_COMPONENTS


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


def _summary_header_attachment(tab: dict) -> dict | None:
    """Build a top summary_header from the tab's goal (one-line orientation)."""
    goal = tab.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return None
    return _build_attachment(
        "summary_header",
        "top",
        {"summary": goal.strip(), "title": "In short", "emoji": "🧭"},
        size="banner",
    )


def attach_secondaries(
    tab: dict,
    extraction: dict | None,
    enrichment: dict | None,
    frames: list[dict] | None,
    domain: str,
    excluded_kinds: set[str] | frozenset[str] = frozenset(),
) -> list[dict]:
    """Decide and build secondary attachments for a single assembled tab.

    Sparse tab (≤ _SPARSE_THRESHOLD items): add ONE enriching bottom attachment,
    trying frame_strip → quick_quiz → tip_callout in priority order. Dense tab
    (≥ _DENSE_THRESHOLD items): add a top summary_header. Tabs in between get
    nothing. Returns a (possibly empty) list of attachment dicts.

    frame_strip/quick_quiz/tip_callout are built from response-global data, so
    repeating one across tabs shows the identical payload N times — the caller
    passes already-used kinds via `excluded_kinds` and each is skipped in favor
    of the next in the chain.
    """
    component = tab.get("component", "")
    props = tab.get("props")
    if not isinstance(props, dict):
        return []

    count = _primary_item_count(component, props)
    if count is None:
        return []

    attachments: list[dict] = []

    if count <= _SPARSE_THRESHOLD:
        attachment = None
        if (
            "frame_strip" not in excluded_kinds
            and component not in _NO_FRAME_STRIP_COMPONENTS
            and domain not in _NO_FRAME_STRIP_DOMAINS
        ):
            attachment = _frame_strip_attachment(frames)
        if (
            attachment is None
            and "quick_quiz" not in excluded_kinds
            and can_host_quick_quiz(component)
        ):
            attachment = _quick_quiz_attachment(enrichment)
        if attachment is None and "tip_callout" not in excluded_kinds:
            attachment = _tip_callout_attachment(extraction, domain)
        if attachment is not None:
            attachments.append(attachment)

    elif count >= _DENSE_THRESHOLD:
        header = _summary_header_attachment(tab)
        if header is not None:
            attachments.append(header)

    return attachments
