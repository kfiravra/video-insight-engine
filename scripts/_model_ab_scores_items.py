"""Model A/B scorers for the item-level call types: extraction groups and vision (4.0d).

Each answer is read by the pipeline's own code — ``validate_group`` /
``requested_quality`` / ``answer_lost`` for a group, ``parse_vision_response``
and the frames phase's presenter rule for a vision batch — so a score says
what the pipeline would have kept. A scorer returns flat metrics (bool / int /
float are averaged in the report; lists and dicts stay in the JSON);
``compare_*`` scores an arm's answer against arm A's on the same request.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from _model_ab_capture import group_from_json

from src.services.media.frame_analyzer import _decode_items, parse_vision_response
from src.services.pipeline.extraction_groups import _empty_object
from src.services.pipeline.group_failures import answer_lost
from src.services.pipeline.group_merge import requested_quality, validate_group
from src.services.pipeline.phases.frames import _is_presenter_frame
from src.utils.json_parsing import parse_json_response, strip_markdown_fences

Score = dict[str, Any]

_FOLD = re.compile(r"[^0-9a-z]+")


def jaccard(left: set[Any], right: set[Any]) -> float:
    """|L ∩ R| / |L ∪ R|; 1.0 when both are empty (they agree on nothing to pick)."""
    union = left | right
    return len(left & right) / len(union) if union else 1.0


# ─── Extraction groups ───────────────────────────────────────────────────────


def _item_counts(validated: Mapping[str, Any]) -> dict[str, int]:
    """``domain.key`` → item count for every list the group kept."""
    counts: dict[str, int] = {}
    for domain, block in validated.items():
        if not isinstance(block, Mapping):
            continue
        for key, value in block.items():
            if isinstance(value, list):
                counts[f"{domain}.{key}"] = len(value)
    return counts


def _dropped(report: Mapping[str, Any]) -> int:
    drops = report.get("dropped") or {}
    return sum(sum(per_key.values()) for per_key in drops.values())


def score_extraction(raw: str, context: Mapping[str, Any]) -> Score:
    """One group answer as ``extraction_groups._read`` sees it: parse, validate, fill."""
    group = group_from_json(context["group"])
    text = strip_markdown_fences(raw)
    parsed = parse_json_response(text)
    if not parsed and not _empty_object(text):
        return {"parsed": False}
    tags, modifiers = context["content_tags"], context["modifiers"]
    validated, report = validate_group(parsed, group, tags, modifiers)
    quality = requested_quality(group.fields, validated)
    counts = _item_counts(validated)
    return {
        "parsed": True,
        "dropped": _dropped(report),
        "defaulted": int(report.get("defaulted", 0)),
        "requested": quality.total,
        "filled": quality.populated,
        "filled_share": round(quality.score, 3),
        "answer_lost": not validated and answer_lost(parsed, group, report),
        "items": sum(counts.values()),
        "item_counts": counts,
    }


def compare_extraction(score: Score, baseline: Score) -> Score:
    """Item count and filled keys against arm A's answer to the same group."""
    if not (score.get("parsed") and baseline.get("parsed")):
        return {}
    items_a = int(baseline["items"])
    compared: Score = {"vsA_filled_delta": int(score["filled"]) - int(baseline["filled"])}
    if items_a:
        compared["vsA_items_ratio"] = round(int(score["items"]) / items_a, 3)
    return compared


# ─── Vision ──────────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    return " ".join(_FOLD.split(text.casefold())).strip()


def reads_card(description: Mapping[str, Any] | None, tokens: list[str]) -> bool:
    """True when every label token appears in the frame's content or visible text."""
    if not description or not tokens:
        return False
    seen = f" {_fold(str(description.get('content', '')))} "
    seen += f" {_fold(str(description.get('text_visible', '')))} "
    return all(f" {_fold(token)} " in seen for token in tokens)


def _kept(descriptions: list[dict[str, Any]]) -> list[int]:
    """Frames the pipeline keeps: not presenter filler (``phases.frames`` rule)."""
    kept: list[int] = []
    for desc in descriptions:
        index = desc["frame_index"]
        if not _is_presenter_frame({"index": index}, {index: desc}):
            kept.append(int(desc["original_index"]))
    return sorted(kept)


def score_vision(raw: str, context: Mapping[str, Any]) -> Score:
    """One batch's descriptions: how many map back, which frames survive, card reading."""
    if _decode_items(raw) is None:
        return {"parsed": False, "cards_read": 0}
    descriptions = parse_vision_response(raw, context["metadata"])
    by_frame = {int(d["original_index"]): d for d in descriptions}
    cards: Mapping[str, list[str]] = context.get("cards") or {}
    read = sorted(i for i, tokens in cards.items() if reads_card(by_frame.get(int(i)), tokens))
    return {
        "parsed": True,
        "described": len(descriptions),
        "described_share": round(len(descriptions) / max(1, len(context["metadata"])), 3),
        "kept": _kept(descriptions),
        "kept_count": len(_kept(descriptions)),
        "scene_types": {str(i): str(d.get("scene_type")) for i, d in by_frame.items()},
        "cards_total": len(cards),
        "cards_read": len(read),
        "cards_read_ids": read,
    }


def compare_vision(score: Score, baseline: Score) -> Score:
    """Frame selection and scene types against arm A's answer to the same batch."""
    if not (score.get("parsed") and baseline.get("parsed")):
        return {}
    ours, theirs = score["scene_types"], baseline["scene_types"]
    shared = [i for i in ours if i in theirs]
    compared: Score = {
        "vsA_selection_jaccard": round(jaccard(set(score["kept"]), set(baseline["kept"])), 3)
    }
    if shared:
        same = sum(ours[i] == theirs[i] for i in shared)
        compared["vsA_scene_agree"] = round(same / len(shared), 3)
    return compared
