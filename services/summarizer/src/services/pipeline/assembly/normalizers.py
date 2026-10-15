"""Shared normalizer helpers for the assembler family modules.

Pure item-shape coercions (dict soup -> component item contracts) used by
more than one assembler family. Family-local normalizers stay in their
family module.
"""

from __future__ import annotations

import re
from typing import Any

from .text_utils import truncate_words

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
    "cost",
    "currency",
    "duration",
    "mapQuery",
    "bookingSearch",
    "tips",
    "specs",
    "rating",
    "thumbnailUrl",
    "s3Key",
    "pronunciation",
    "timestamp",
)


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

    result: dict = {"name": name, "description": description}
    has_passthrough = False
    for field in _SPOT_PASSTHROUGH_FIELDS:
        if field not in item:
            continue
        value = item[field]
        if value is None or value == "":
            continue
        result[field] = value
        has_passthrough = True

    if not description and not has_passthrough:
        return None
    return result


def _first_competitor_name(comparisons: list[dict]) -> str:
    """Extract the first non-empty competitorName from comparison rows."""
    for c in comparisons:
        name = c.get("competitorName", "")
        if name:
            return str(name)
    return ""


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
        "timestamp": item.get("timestamp") or item.get("seconds"),
        "duration": item.get("duration"),
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
        if item.get("duration"):
            back_parts.append(str(item["duration"]))
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


def _seconds_to_time_str(seconds: int) -> str:
    """Convert seconds to M:SS or H:MM:SS time string."""
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"


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


def _coerce_int(value: Any) -> int | None:
    """Parse int or return None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _coerce_float(value: Any) -> float | None:
    """Parse float or return None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


_MOMENT_LABEL_KEYS = ("label", "title", "name")
# Prose a moment source may carry under another name: PodcastSegment.summary,
# FoodStep.instruction, a chapter / outline digest.
_MOMENT_DESCRIPTION_KEYS = ("description", "summary", "instruction", "digest")
_MOMENT_START_KEYS = ("seconds", "startSeconds", "timestamp", "start_time", "startTime")
_MOMENT_END_KEYS = ("endSeconds", "end_seconds", "endTimestamp", "end_time", "endTime")
_MOMENT_PASSTHROUGH_KEYS = ("mood", "emoji", "speaker", "thumbnailUrl", "s3Key")
_MOMENT_LABEL_CAP = 60
# A placeholder label is no text: the track never ships "Moment 3" (R5). Exact form only —
# a lowercase "moment 3" is a source's own words, not the old positional default.
_MOMENT_PLACEHOLDER = re.compile(r"^Moment \d+$")


def _prose(value: Any) -> str | None:
    """A stripped non-empty string, else None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _first_prose(item: dict, keys: tuple[str, ...]) -> str | None:
    return next((text for key in keys if (text := _prose(item.get(key))) is not None), None)


def _first_int(item: dict, keys: tuple[str, ...]) -> int | None:
    return next((value for key in keys if (value := _coerce_int(item.get(key))) is not None), None)


def _moment_text(item: dict) -> tuple[str, str | None] | None:
    """(label, description) from whichever fields carry prose; None when none does.

    The label comes from label/title/name, else from the head of the
    description; the description is dropped when it only repeats the label.
    """
    label = _first_prose(item, _MOMENT_LABEL_KEYS)
    if label is not None and _MOMENT_PLACEHOLDER.match(label):
        label = None
    description = _first_prose(item, _MOMENT_DESCRIPTION_KEYS)
    if label is None and description is not None:
        label = truncate_words(description, _MOMENT_LABEL_CAP)
    if label is None:
        return None
    return label, (None if description == label else description)


def _normalize_moment_item(item: Any, index: int) -> dict | None:
    """Normalize a moment/clip item to {label, time, seconds, endSeconds?, description?, ...}.

    Unified shape for MomentTrack: points (no endSeconds) and spans (with endSeconds).
    Accepts the legacy field names from TimelineExplorer.entries and ClipPlayerInteractive.clips.
    An item without prose is refused (None) — a moment is never invented from
    its position; ``index`` stays for the call contract and names nothing.
    """
    if isinstance(item, str):
        stripped = item.strip()
        return {"label": stripped, "time": "0:00", "seconds": 0} if stripped else None
    if not isinstance(item, dict):
        return None
    text = _moment_text(item)
    if text is None:
        return None
    label, description = text
    seconds = _first_int(item, _MOMENT_START_KEYS) or 0
    end_seconds = _first_int(item, _MOMENT_END_KEYS)
    normalized: dict[str, Any] = {
        "label": label,
        "time": item.get("time") or _seconds_to_time_str(seconds),
        "seconds": seconds,
    }
    if end_seconds is not None and end_seconds > seconds + 1:
        normalized["endSeconds"] = end_seconds
    if description is not None:
        normalized["description"] = description
    for key in _MOMENT_PASSTHROUGH_KEYS:
        value = item.get(key)
        if value is not None and value != "":
            normalized[key] = value
    tags = item.get("tags")
    if isinstance(tags, list) and tags:
        normalized["tags"] = [str(t) for t in tags if t is not None]
    return normalized


def normalize_moment_items(data: Any) -> list[dict]:
    """Moment items a track can ship: prose-bearing, one per ``seconds`` (first wins).

    A second item at the same second cannot be seeked apart from the first and
    reads as padding (S11 shipped nine itinerary days at 0:00), so it is refused.
    """
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    seen: set[int] = set()
    for index, raw in enumerate(data):
        item = _normalize_moment_item(raw, index)
        if item is None or item["seconds"] in seen:
            continue
        seen.add(item["seconds"])
        items.append(item)
    return items


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
    for key in ("sets", "reps", "duration", "rest", "difficulty", "timestamp", "description"):
        if item.get(key) is not None:
            result[key] = item[key]
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


def _chapter_end(
    chapters: list[dict], index: int, start: int, video_duration: int | None
) -> int | None:
    """The span end: the next later chapter's start minus 1; the video end for the last one."""
    next_start = next(
        (
            later
            for ch in chapters[index + 1 :]
            if (later := _coerce_int(ch.get("start_time"))) is not None and later > start
        ),
        int(video_duration) if video_duration is not None else None,
    )
    return next_start - 1 if next_start is not None and next_start > start + 1 else None


def _chapter_moment(chapter: dict, start: int, end_seconds: int | None) -> dict | None:
    """A chapter span in MomentTrack item shape; None for a chapter without a title."""
    text = _moment_text(chapter)
    if text is None:
        return None
    label, description = text
    entry: dict[str, Any] = {
        "time": _seconds_to_time_str(start),
        "seconds": start,
        "label": label,
        "mood": "chapter",
    }
    if end_seconds is not None:
        entry["endSeconds"] = end_seconds
    if description is not None:
        entry["description"] = description
    return entry


def _chapters_to_moments(chapters: list[dict], video_duration: int | None = None) -> list[dict]:
    """Convert yt-dlp chapters (or outline / description entries in that shape) to spans.

    Each chapter ends where the next later chapter starts; the final one at
    video_duration - 1 when known. A chapter without a title, or at a start an
    earlier chapter already holds, is skipped; a ``summary`` / ``description``
    digest rides along as the moment's description.
    """
    items: list[dict] = []
    seen: set[int] = set()
    for i, ch in enumerate(chapters):
        start = _coerce_int(ch.get("start_time")) or 0
        if start in seen:
            continue
        entry = _chapter_moment(ch, start, _chapter_end(chapters, i, start, video_duration))
        if entry is None:
            continue
        seen.add(start)
        items.append(entry)
    return items
