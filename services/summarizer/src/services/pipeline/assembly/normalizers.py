"""Shared normalizer helpers for the assembler family modules.

Pure item-shape coercions (dict soup -> component item contracts) used by
more than one assembler family. Family-local normalizers stay in their
family module; the moment block lives in ``moment_normalizers`` and is
re-exported here.
"""

from __future__ import annotations

from typing import Any

from src.models.domain_types import VERDICT_BADGES

from .derived_guard import anchor_seconds, stated_number, stated_rows, stated_scalars
from .moment_normalizers import _chapter_end as _chapter_end
from .moment_normalizers import _chapters_to_moments as _chapters_to_moments
from .moment_normalizers import _coerce_int as _coerce_int
from .moment_normalizers import _moment_text as _moment_text
from .moment_normalizers import _normalize_moment_item as _normalize_moment_item
from .moment_normalizers import _seconds_to_time_str as _seconds_to_time_str
from .moment_normalizers import normalize_moment_items as normalize_moment_items

# Category-like front values that indicate flash_deck front corruption
_CATEGORY_FRONTS = frozenset(
    {
        "chef_tip",
        "pro_tip",
        "technique",
        "tip",
        "fact",
        "trivia",
        "note",
        "insight",
        "key_point",
        "highlight",
        "warning",
        "method",
        "rule",
        "principle",
        "concept",
        "definition",
    }
)


# Domain emoji for the overview hero — matches domain identity at a glance.
_DOMAIN_EMOJI: dict[str, str] = {
    "learning": "🧠",
    "tech": "💻",
    "food": "🍳",
    "fitness": "💪",
    "music": "🎵",
    "travel": "✈️",
    "review": "⭐",
    "project": "🔨",
    "language": "🗣️",
    "science": "🔬",
    "narrative": "🎬",
    "finance": "💰",
}

# Fields across domains that can contribute to the overview "Tips" section.
_TIP_FIELDS: tuple[str, ...] = (
    "tips",
    "savingTips",
    "transportationTips",
    "accommodationTips",
    "formTips",
    "safetyTips",
    "proTips",
    "studyTips",
)


# ─────────────────────────────────────────────────────
# Normaliser helpers
# ─────────────────────────────────────────────────────


_SPOT_PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "emoji",
    "currency",
    "mapQuery",
    "bookingSearch",
    "tips",
    "specs",
    "thumbnailUrl",
    "s3Key",
    "pronunciation",
)
# Facts a spot states only when it is anchored in the video (D13). spot_explorer
# and info_grid are the demote rung of a refused tier list, comparison or claims
# tracker, so an unanchored cost, duration or rating would re-enter there.
_SPOT_STATED_FIELDS: tuple[str, ...] = ("cost", "duration", "rating")


def _spot_extras(item: dict) -> dict:
    """A spot's optional fields: the passthroughs, its seek point, the speaker's
    verbatim ``quote`` and the facts its anchor states.

    The seek point is the item's anchor as ``derived_guard`` reads it — whole
    seconds ≥ 0, else none: the FE schema refuses the whole tab over a negative
    or non-numeric stamp.
    """
    extras: dict = {
        field: item[field]
        for field in _SPOT_PASSTHROUGH_FIELDS
        if item.get(field) not in (None, "")
    }
    timestamp = anchor_seconds(item)
    if timestamp is not None:
        extras["timestamp"] = timestamp
    quote = item.get("quote")
    if isinstance(quote, str) and quote.strip():
        extras["quote"] = quote.strip()
    extras.update(stated_scalars(item, _SPOT_STATED_FIELDS))
    return extras


def _normalize_to_spot(item: dict) -> dict | None:
    """Normalize diverse dict shapes to spot format {name, description, emoji}.

    Returns None when the item lacks a non-empty name, or has only a name with
    no descriptive/passthrough content — such entries render as empty cards in
    the spot_explorer UI.
    """
    name_raw = (
        item.get("name")
        or item.get("title")
        or item.get("phrase")
        or item.get("word")
        or item.get("aspect")
        or item.get("label")
        or item.get("key")  # ReviewSpec {key, value} — mirror promotion.py
        or ""
    )
    name = str(name_raw).strip()
    if not name:
        return None

    desc_raw = (
        item.get("description")
        or item.get("detail")
        or item.get("definition")
        or item.get("explanation")
        or item.get("translation")
        or item.get("context")
        or item.get("value")  # ReviewSpec {key, value}
        or ""
    )
    description = str(desc_raw).strip()

    extras = _spot_extras(item)
    if not description and not extras:
        return None
    return {"name": name, "description": description, **extras}


def _normalize_comparison(item: dict) -> dict:
    """Normalize a comparison item to {feature, thisProduct, competitor}."""
    if "thisProduct" in item:
        return item  # already in correct shape
    return {
        "feature": item.get("feature", ""),
        "thisProduct": item.get("left", ""),
        "competitor": item.get("right", ""),
        "competitorName": item.get("competitorName", ""),
    }


def _normalize_verdict(verdict: dict, rating: Any) -> dict[str, Any]:
    """A review verdict as ReviewSummary renders it.

    The badge ships only as the reviewer stated it (absent, never a default) and
    only as one of the four verdicts the validator allows (``VERDICT_BADGES`` —
    a raw, unvalidated "RECOMMENDED" is no verdict); the sub-scores and the
    score / maxScore only as the extraction anchors them (D13 — S07 shipped a
    "9.0" and five sub-scores the reviewer never gave). ``rating`` holds the
    score: the review's rating block, or the verdict itself for a legacy
    verdict tab.
    """
    props: dict[str, Any] = {
        "bottomLine": verdict.get("bottomLine", ""),
        "bestFor": verdict.get("bestFor") or [],
        "notFor": verdict.get("notFor") or [],
    }
    badge = verdict.get("badge")
    if badge in VERDICT_BADGES:
        props["badge"] = badge
    sub_scores = stated_rows(verdict.get("subScores"), "score")
    if sub_scores:
        props["subScores"] = sub_scores
    score = stated_number(rating, "score")
    if score is not None:
        props["score"] = score
        max_score = stated_number(rating, "maxScore")
        if max_score is not None and max_score > 0:
            props["maxScore"] = max_score
    return props


def _normalize_step(item: dict, index: int) -> dict:
    """Normalize a step item, converting timestamp-shaped data if needed."""
    instruction = (
        item.get("instruction")
        or item.get("title")
        or item.get("label")
        or item.get("name")
        or item.get("text")
        or item.get("description")
        or item.get("detail")
        or ""
    )
    tips = item.get("tips")
    if not tips and item.get("detail") and item.get("detail") != instruction:
        tips = item.get("detail")
    # Emit `title` only when it isn't already serving as the instruction —
    # otherwise StepPlayer would render the same text twice.
    title = item.get("title")
    if title == instruction:
        title = None
    return {
        "number": item.get("number", index + 1),
        "instruction": instruction,
        # One anchor for the seek point and the duration guard (D13): a step
        # at 0:00 keeps both.
        "timestamp": anchor_seconds(item),
        "duration": stated_scalars(item, ("duration",)).get("duration"),
        "tips": tips,
        **({"title": title} if title else {}),
        **({"safetyNote": item["safetyNote"]} if item.get("safetyNote") else {}),
        **({"thumbnailUrl": item["thumbnailUrl"]} if item.get("thumbnailUrl") else {}),
        **({"s3Key": item["s3Key"]} if item.get("s3Key") else {}),
    }


def _to_flash_card(item: dict) -> dict:
    """Convert a domain object to {front, back, emoji} flash card format.

    Card backs concatenate raw values without English category prefixes
    ("Form: ", "Duration: ", "Level: ", "Modifications: ", "Example: ").
    The card's ``front`` already carries the term/name — the back just
    needs the explanation content, and prefix labels would leak English
    into non-English flashcards. Values are joined by " · " so the
    structure is still visible.
    """
    if "front" in item and "back" in item:
        return item
    if "name" in item and "definition" in item:
        return {"front": item["name"], "back": item["definition"], "emoji": item.get("emoji")}
    if "name" in item and ("formCues" in item or "duration" in item):
        back_parts: list[str] = []
        if item.get("formCues"):
            cues = item["formCues"]
            back_parts.append(", ".join(cues) if isinstance(cues, list) else str(cues))
        duration = stated_scalars(item, ("duration",)).get("duration")
        if duration:
            back_parts.append(str(duration))
        if item.get("difficulty"):
            back_parts.append(str(item["difficulty"]))
        if item.get("modifications"):
            mods = item["modifications"]
            if isinstance(mods, list) and mods:
                back_parts.append(", ".join(mods))
        return {
            "front": item["name"],
            "back": " · ".join(p for p in back_parts if p) or item.get("description", ""),
            "emoji": item.get("emoji"),
        }
    if "name" in item:
        return {
            "front": item["name"],
            "back": item.get("description") or item.get("text") or "",
            "emoji": item.get("emoji"),
        }
    if "word" in item:
        back_parts = [item.get("definition") or ""]
        if item.get("pronunciation"):
            back_parts.append(f"({item['pronunciation']})")
        if item.get("example"):
            back_parts.append(str(item["example"]))
        return {
            "front": item["word"],
            "back": " ".join(filter(None, back_parts)),
            "emoji": item.get("emoji"),
        }
    if "term" in item:
        return {
            "front": item["term"],
            "back": item.get("definition") or item.get("description") or "",
            "emoji": item.get("emoji"),
        }
    if "title" in item:
        return {
            "front": item["title"],
            "back": item.get("description") or item.get("text") or "",
            "emoji": item.get("emoji"),
        }
    if "text" in item and "front" not in item:
        return {"front": item.get("type", ""), "back": item["text"], "emoji": item.get("emoji")}
    if "aspect" in item and "detail" in item:
        return {"front": item["aspect"], "back": item["detail"], "emoji": item.get("emoji")}
    if "fact" in item:
        return {
            "front": item["fact"],
            "back": item.get("explanation") or item.get("detail") or "",
            "emoji": item.get("emoji"),
        }
    return item


def _normalize_code_snippet(item: Any) -> dict | None:
    """Normalize diverse code snippet shapes to {code, language, explanation}."""
    if isinstance(item, str):
        stripped = item.strip()
        return {"code": stripped, "language": "text", "explanation": ""} if stripped else None
    if not isinstance(item, dict):
        return None
    code = (
        item.get("code")
        or item.get("snippet")
        or item.get("example")
        or item.get("command")
        or item.get("cmd")
        or item.get("content")
        or ""
    )
    if not code:
        return None
    return {
        "code": code,
        "language": item.get("language") or item.get("lang") or "text",
        "explanation": item.get("explanation")
        or item.get("description")
        or item.get("detail")
        or "",
        "filename": item.get("filename") or None,
        "timestamp": item.get("timestamp"),
    }


def _coerce_float(value: Any) -> float | None:
    """Parse float or return None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_exercise(item: Any) -> dict | None:
    """Normalize exercise to {name, emoji, formCues[], ...}."""
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("title") or item.get("exercise") or "Exercise"
    form_cues = item.get("formCues") or item.get("form_cues") or []
    if isinstance(form_cues, str):
        form_cues = [form_cues] if form_cues.strip() else []
    modifications = item.get("modifications") or []
    if isinstance(modifications, str):
        modifications = [modifications] if modifications.strip() else []
    result: dict[str, Any] = {
        "name": str(name),
        "emoji": item.get("emoji") or "💪",
        "formCues": form_cues,
        "modifications": modifications,
    }
    for key in ("sets", "reps", "difficulty", "timestamp", "description"):
        if item.get(key) is not None:
            result[key] = item[key]
    # Work and rest times are facts only on an exercise anchored in the video (D13).
    result.update(stated_scalars(item, ("duration", "rest")))
    return result


def _normalize_quiz_question(item: Any) -> dict | None:
    """Normalize quiz question to {question, options[], correctIndex, explanation}."""
    if not isinstance(item, dict):
        return None
    question = item.get("question") or item.get("text") or ""
    if not question:
        return None
    options = item.get("options") or item.get("choices") or []
    if not isinstance(options, list):
        return None
    options = [str(o) for o in options if o is not None]
    if len(options) < 2:
        return None
    correct_index = (
        item.get("correctIndex")
        if item.get("correctIndex") is not None
        else item.get("correct_index")
    )
    if correct_index is None:
        correct_index = 0
    try:
        correct_index = int(correct_index)
    except (ValueError, TypeError):
        correct_index = 0
    correct_index = max(0, min(correct_index, len(options) - 1))
    return {
        "question": str(question),
        "options": options,
        "correctIndex": correct_index,
        "explanation": str(item.get("explanation") or ""),
    }


def _normalize_scenario_option(opt: Any) -> dict | None:
    """Normalize a single scenario option."""
    if isinstance(opt, str):
        return {"text": opt, "correct": False, "explanation": ""} if opt.strip() else None
    if isinstance(opt, dict):
        text = opt.get("text") or opt.get("label") or ""
        if not text:
            return None
        return {
            "text": str(text),
            "correct": bool(opt.get("correct", False)),
            "explanation": str(opt.get("explanation") or ""),
        }
    return None


def _normalize_scenario_item(item: Any) -> dict | None:
    """Normalize scenario to {question, options[]}."""
    if not isinstance(item, dict):
        return None
    question = item.get("question") or item.get("situation") or item.get("text") or ""
    if not question:
        return None
    raw_options = item.get("options") or item.get("choices") or []
    if not isinstance(raw_options, list):
        return None
    options = [o for o in (_normalize_scenario_option(opt) for opt in raw_options) if o is not None]
    if len(options) < 2:
        return None
    return {"question": str(question), "options": options}
