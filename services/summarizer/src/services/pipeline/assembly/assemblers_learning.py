"""Assemblers — learning/media family.

exercise_tracker, quiz, flash_deck, scenario, verdict, budget, overview,
gallery, lyrics_player, display_section.

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

from typing import Any

from .derived_guard import stated_block_scalars, stated_number, stated_rows
from .normalizers import (
    _CATEGORY_FRONTS,
    _DOMAIN_EMOJI,
    _TIP_FIELDS,
    _normalize_exercise,
    _normalize_quiz_question,
    _normalize_scenario_item,
    _to_flash_card,
)


def assemble_exercise_tracker(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if isinstance(data, dict):
        raw_exercises = data.get("exercises") or []
        warmup = data.get("warmup") or []
        cooldown = data.get("cooldown") or []
        exercises = (
            [e for e in (_normalize_exercise(item) for item in raw_exercises) if e is not None]
            if isinstance(raw_exercises, list)
            else []
        )
        if not exercises and not warmup:
            return None
        props: dict[str, Any] = {"exercises": exercises}
        if warmup:
            props["warmup"] = warmup
        if cooldown:
            props["cooldown"] = cooldown
        return props
    if isinstance(data, list) and len(data) > 0:
        exercises = [e for e in (_normalize_exercise(item) for item in data) if e is not None]
        return {"exercises": exercises} if exercises else None
    return None


def assemble_quiz(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    questions = [q for q in (_normalize_quiz_question(item) for item in data) if q is not None]
    if not questions:
        return None
    return {"questions": questions}


def assemble_flash_deck(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    flat = []
    for item in data:
        if isinstance(item, dict) and "cards" in item and isinstance(item["cards"], list):
            flat.extend(item["cards"])
        else:
            flat.append(item)
    if not flat:
        return None
    cards = [_to_flash_card(item) if isinstance(item, dict) else item for item in flat]
    for card in cards:
        if isinstance(card, dict) and "front" in card and "back" in card:
            front = str(card["front"]).strip().lower().replace(" ", "_")
            if front in _CATEGORY_FRONTS or front.endswith("_tip") or front.endswith("_note"):
                back = str(card.get("back", ""))
                sentences = back.split(". ")
                if sentences and sentences[0]:
                    new_front = sentences[0].rstrip(".")
                    if len(new_front) > 60:
                        words = new_front.split()[:8]
                        new_front = " ".join(words)
                    card["front"] = new_front
    return {"cards": cards}


def assemble_scenario(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    scenarios = [s for s in (_normalize_scenario_item(item) for item in data) if s is not None]
    if not scenarios:
        return None
    return {"scenarios": scenarios}


def assemble_verdict(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Legacy verdict assembler — now emits ComparisonInteractive-shaped props.

    The standalone VerdictInteractive React component was retired in the
    video-to-action overhaul. Cached `assembledTabs` rows still reference the
    "verdict" component key, so we route them to ComparisonInteractive with no
    rows and the verdict folded into the `verdict` prop. The frontend
    `verdict:` registry entry forwards to ComparisonInteractive accordingly.
    """
    if not isinstance(data, dict):
        return None
    verdict_props: dict[str, Any] = {
        "badge": data.get("badge", "neutral"),
        "bottomLine": data.get("bottomLine", ""),
        "bestFor": data.get("bestFor") or [],
        "notFor": data.get("notFor") or [],
    }
    # Scores are facts only when anchored in the video (D13) — same guard as
    # the comparison assembler's review rating.
    sub_scores = stated_rows(data.get("subScores"), "score")
    if sub_scores:
        verdict_props["subScores"] = sub_scores
    score = stated_number(data, "score")
    if score is not None:
        verdict_props["score"] = score
        max_score = stated_number(data, "maxScore")
        if max_score is not None and max_score > 0:
            verdict_props["maxScore"] = max_score
    return {
        "pros": [],
        "cons": [],
        "comparisons": [],
        "leftLabel": "",
        "rightLabel": "",
        "verdict": verdict_props,
    }


def assemble_budget(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if isinstance(data, dict) and data:
        return {
            "total": data.get("total", 0),
            "currency": data.get("currency", "USD"),
            "breakdown": data.get("breakdown") or data.get("costs", []),
            "savingTips": data.get("savingTips") or data.get("saving_tips", []),
        }
    return None


def _extract_level(extraction: dict | None, primary_tag: str) -> str | None:
    """Pull a level/difficulty string from extraction (meta.difficulty, difficulty, level)."""
    if not extraction:
        return None
    domain = extraction.get(primary_tag)
    if not isinstance(domain, dict):
        return None
    meta = domain.get("meta") if isinstance(domain.get("meta"), dict) else None
    for source in (meta, domain):
        if not isinstance(source, dict):
            continue
        for key in ("difficulty", "level"):
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().title()
    return None


def _collect_tips(extraction: dict | None, primary_tag: str, cap: int = 5) -> list[str]:
    """Collect tip-shaped strings from the primary domain across known fields."""
    if not extraction:
        return []
    domain = extraction.get(primary_tag)
    if not isinstance(domain, dict):
        return []
    out: list[str] = []
    for field in _TIP_FIELDS:
        val = domain.get(field)
        if not isinstance(val, list):
            continue
        for item in val:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("tip") or item.get("text") or item.get("description")
                if isinstance(text, str) and text.strip():
                    out.append(text.strip())
            if len(out) >= cap:
                return out
    return out


def _domain_scalars(domain_data: dict) -> dict[str, Any]:
    """The primary block's scalars and its dict children's, the first key winning.

    Only what a reader may take as a fact (D13): a score, rating, tier, status,
    duration or date passes only when its block is anchored, and a ``rating``
    block passes nothing unanchored. S07's overview shipped the review's
    unanchored score 9 / maxScore 10 / "Great" through this spread.
    """
    top_level = stated_block_scalars(domain_data)
    scalars: dict[str, Any] = {}
    for key, value in domain_data.items():
        if key in top_level:
            scalars.setdefault(key, top_level[key])
        elif isinstance(value, dict):
            for child_key, child_value in stated_block_scalars(value, key).items():
                scalars.setdefault(child_key, child_value)
    return scalars


def assemble_overview(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build composite overview from synthesis + video metadata + domain data.

    Emits rich fields (title, emoji, subtitle, stats, tips, level) so
    OverviewInteractive renders the hero, stat pills, and collapsible tips —
    not just a bare summary.
    """
    synthesis = tab.get("_synthesis") or {}
    video_meta = tab.get("_video_meta") or {}
    primary_tag = tab.get("_primary_tag", "learning")

    result: dict[str, Any] = {}

    if video_meta.get("title"):
        result["title"] = video_meta["title"]

    result["emoji"] = _DOMAIN_EMOJI.get(primary_tag, "📋")

    if synthesis.get("tldr"):
        result["subtitle"] = synthesis["tldr"]
        result["tldr"] = synthesis["tldr"]

    if synthesis.get("masterSummary"):
        result["masterSummary"] = synthesis["masterSummary"]
    if synthesis.get("keyTakeaways"):
        result["keyTakeaways"] = synthesis["keyTakeaways"]

    stats: list[dict[str, str]] = []
    duration_seconds = video_meta.get("duration")
    if isinstance(duration_seconds, (int, float)) and duration_seconds > 0:
        duration_min = round(duration_seconds / 60)
        stats.append({"label": "Duration", "value": f"{duration_min} min", "emoji": "⏱️"})

    level = _extract_level(extraction, primary_tag)
    if level:
        stats.append({"label": "Level", "value": level, "emoji": "🎯"})
        result["level"] = level

    if stats:
        result["stats"] = stats

    if duration_seconds is not None:
        result["duration"] = duration_seconds
    if video_meta.get("channel"):
        result["channel"] = video_meta["channel"]

    tips = _collect_tips(extraction, primary_tag)
    if tips:
        result["tips"] = tips

    domain_data = extraction.get(primary_tag) if extraction else None
    if isinstance(domain_data, dict):
        for key, value in _domain_scalars(domain_data).items():
            result.setdefault(key, value)

    # Only the domain emoji was set — no real content to show.
    if set(result.keys()) <= {"emoji"}:
        return None

    return {"data": result}


def assemble_gallery(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    if not isinstance(data, list) or len(data) < 1:
        return None
    return {"images": data}


def assemble_lyrics_player(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build lyrics player props from music structure + lyrics."""
    music = data if isinstance(data, dict) and ("structure" in data or "lyrics" in data) else None
    if music is None:
        music = extraction.get("music", {}) if extraction else {}
    if not isinstance(music, dict):
        return None
    structure = music.get("structure", [])
    lyrics = music.get("lyrics", [])
    if not structure and not lyrics:
        return None
    sections: list[dict] = []
    lyric_lines = []
    if isinstance(lyrics, list):
        # Extraction emits {"line": ...}; the frontend LyricsKaraokeLine
        # contract wants {"text": ...} — translate here at assembly.
        lyric_lines = [
            {
                "text": item.get("line", item.get("text", str(item))),
                "timestamp": item.get("timestamp"),
            }
            if isinstance(item, dict)
            else {"text": str(item)}
            for item in lyrics
        ]

    if structure and isinstance(structure, list):
        for i, seg in enumerate(structure):
            if not isinstance(seg, dict):
                continue
            seg_start = int(seg.get("timestamp", 0) or 0)
            next_start = (
                int(structure[i + 1].get("timestamp", 999999))
                if i + 1 < len(structure) and isinstance(structure[i + 1], dict)
                else 999999
            )
            seg_lines = [
                line
                for line in lyric_lines
                if line.get("timestamp") is not None
                and seg_start <= int(line.get("timestamp") or 0) < next_start
            ]
            sections.append(
                {
                    "name": seg.get("name", f"Section {i + 1}"),
                    "timestamp": seg_start,
                    "lines": seg_lines if seg_lines else [{"text": seg.get("description", "")}],
                    "analysis": seg.get("description", ""),
                }
            )
    elif lyric_lines:
        sections.append({"name": "Lyrics", "timestamp": 0, "lines": lyric_lines})

    if not sections:
        return None
    props: dict[str, Any] = {"sections": sections}
    if music.get("artist"):
        props["artist"] = music["artist"]
    return props


def assemble_display_section(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Generic fallback for non-interactive tabs."""
    if data is None:
        return None
    return {"data": data, "tabId": tab.get("id", "")}
