"""Model A/B scorers per call type (pipeline-1min 4.0d) — the pipeline's own parsers.

``score_answer(call_type, raw, context)`` reads one answer the way the
pipeline would (tier probe ``parse_tier_probe``; plan ``_plan_from_response``
+ the domain requirements; memory ``parse_memory_output``; description
``_parse_analysis``; quiz ``parse_quiz``; translation ``_string_list`` + the
echo checks; the judge ``_parse_judge_response``; extraction and vision in
``_model_ab_scores_items``). ``compare_to_a`` scores an answer against arm A's
answer to the same request (and the tier probe against the golden labels).
Metrics are flat: bool / int / float are averaged per arm in the report.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from _model_ab_scores_items import (
    Score,
    compare_extraction,
    compare_vision,
    jaccard,
    score_extraction,
    score_vision,
)
from pydantic import ValidationError

from src.models.pipeline_types import PlanResult
from src.services.pipeline import enrichment, plan
from src.services.pipeline.assembly import infer_component
from src.services.pipeline.assembly.core import _REQUIREMENT_EQUIVALENTS
from src.services.pipeline.faithfulness import _parse_judge_response
from src.services.pipeline.memory import parse_memory_output
from src.services.pipeline.plan_prompt import PlanVideo
from src.services.pipeline.reconcile_pass import _equivalents
from src.services.pipeline.tier_probe import parse_tier_probe
from src.services.pipeline.translation_flat import _string_list, is_batch_echo, is_mirror
from src.services.transcript.english import _untranslated
from src.services.video.description_analyzer import _parse_analysis
from src.shared_config.domain_config import effective_requirements, ruled_out_requirements
from src.utils.json_parsing import parse_json_response

PROBE_FIELDS: tuple[str, ...] = ("domain", "format", "has_visual_demo", "transcript_usable")
# Description fields → the key two answers are matched on.
_DESCRIPTION_KEYS: dict[str, str] = {
    "links": "url",
    "resources": "url",
    "relatedVideos": "url",
    "socialLinks": "url",
    "timestamps": "seconds",
}
_HEBREW = re.compile(r"[֐-׿]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")

# The metrics each call type's report table shows (the JSON keeps them all).
REPORT_METRICS: dict[str, tuple[str, ...]] = {
    "tier_probe": ("parsed", *(f"golden_{f}" for f in PROBE_FIELDS), "vsA_domain"),
    "description": ("parsed", "has_content", "vsA_fields_agree"),
    "plan": (
        "parsed",
        "fallback",
        "tabs",
        "distinct_sources",
        "duplicate_sources",
        "required_present",
        "vsA_tabs_jaccard",
        "vsA_evidence_agree",
        "vsA_evidence_flips",
    ),
    "memory": (
        "parsed",
        "outline_valid",
        "outline_sections",
        "evidence_keys",
        "vsA_evidence_agree",
    ),
    "extraction": (
        "parsed",
        "dropped",
        "defaulted",
        "filled_share",
        "answer_lost",
        "items",
        "vsA_items_ratio",
    ),
    "quiz": ("parsed", "questions", "dropped", "usable"),
    "vision": (
        "parsed",
        "described_share",
        "kept_count",
        "cards_read",
        "vsA_selection_jaccard",
        "vsA_scene_agree",
    ),
    "translation_output": ("parsed", "echo", "left_share", "length_ratio"),
    "translation_rag": ("parsed", "echo", "left_share", "length_ratio"),
    "faithfulness": ("parsed", "grounded", "vsA_agree"),
}


# ─── Tier probe ──────────────────────────────────────────────────────────────


def _score_tier_probe(raw: str, context: Mapping[str, Any]) -> Score:
    probe = parse_tier_probe(raw)
    score: Score = {"parsed": probe is not None}
    if probe is not None:
        score.update({field: getattr(probe, field) for field in PROBE_FIELDS})
    for field, label in (context.get("labels") or {}).items():
        if label is not None:
            score[f"golden_{field}"] = probe is not None and score[field] == label
    return score


def _compare_tier_probe(score: Score, baseline: Score) -> Score:
    if not (score["parsed"] and baseline["parsed"]):
        return {}
    return {f"vsA_{field}": score[field] == baseline[field] for field in PROBE_FIELDS}


# ─── Plan ────────────────────────────────────────────────────────────────────


def _plan_video(context: Mapping[str, Any]) -> PlanVideo:
    """The plan's normalisation inputs (``_plan_from_response`` reads only these)."""
    return PlanVideo(
        title="",
        channel="",
        description="",
        duration=int(context.get("duration") or 0),
        category_hint=context.get("category_hint"),
        content_format=context.get("content_format"),
        transcript="",
    )


def _accepted(component: str) -> frozenset[str]:
    """What the pipeline takes for a required component: reconcile's promotions and
    assembly's equivalents (``step_flow_canvas`` meets ``step_player``)."""
    return _equivalents(component) | _REQUIREMENT_EQUIVALENTS.get(component, frozenset())


def _missing_required(result: PlanResult, content_format: str | None) -> list[str]:
    """Required components (minus those the plan's evidence rules out) no tab meets."""
    components = {t.get("component") or infer_component(t.get("id", "")) for t in result.tabs}
    required = effective_requirements(result.primary_tag, content_format)["required"]
    ruled_out = ruled_out_requirements(result.primary_tag, result.evidence)
    return [c for c in required if c not in ruled_out and not _accepted(c) & components]


def _score_plan(raw: str, context: Mapping[str, Any]) -> Score:
    if not parse_json_response(raw):
        return {"parsed": False}
    try:
        result = plan._plan_from_response(raw, _plan_video(context))
    except ValidationError as exc:
        return {"parsed": False, "error": str(exc)[:300]}
    missing = _missing_required(result, context.get("content_format"))
    sources = {str(t.get("dataSource") or t.get("id")) for t in result.tabs}
    return {
        "parsed": True,
        "fallback": result.confidence < plan.CONFIDENCE_THRESHOLD or result.plan_fallback,
        "confidence": result.confidence,
        "primary_tag": result.primary_tag,
        "tabs": len(result.tabs),
        "data_sources": sorted(sources),
        # Tabs sharing a dataSource with another tab show the same data twice.
        "distinct_sources": len(sources),
        "duplicate_sources": len(result.tabs) - len(sources),
        "required_present": not missing,
        "required_missing": missing,
        "evidence": dict(result.evidence),
    }


def evidence_agreement(ours: Mapping[str, bool], theirs: Mapping[str, bool]) -> float | None:
    """Share of the evidence keys both answered that they answered alike."""
    shared = [key for key in ours if key in theirs]
    if not shared:
        return None
    return round(sum(ours[k] == theirs[k] for k in shared) / len(shared), 3)


def evidence_flips(ours: Mapping[str, bool], theirs: Mapping[str, bool]) -> list[str]:
    """The evidence keys both answered and answered differently (``is_learnable`` …)."""
    return sorted(key for key in ours if key in theirs and ours[key] != theirs[key])


def _compare_plan(score: Score, baseline: Score) -> Score:
    if not (score["parsed"] and baseline["parsed"]):
        return {}
    sources = jaccard(set(score["data_sources"]), set(baseline["data_sources"]))
    compared: Score = {
        "vsA_tabs_jaccard": round(sources, 3),
        "vsA_primary_tag": score["primary_tag"] == baseline["primary_tag"],
    }
    agreement = evidence_agreement(score["evidence"], baseline["evidence"])
    if agreement is not None:
        compared["vsA_evidence_agree"] = agreement
    compared["vsA_evidence_flips"] = evidence_flips(score["evidence"], baseline["evidence"])
    return compared


# ─── Memory ──────────────────────────────────────────────────────────────────


def _score_memory(raw: str, context: Mapping[str, Any]) -> Score:
    data = parse_json_response(raw)
    result = parse_memory_output(data, int(context.get("duration") or 0)) if data else None
    if result is None:
        return {"parsed": False}
    return {
        "parsed": True,
        "outline_valid": bool(result.outline),
        "outline_sections": len(result.outline),
        "takeaways": len(result.takeaways),
        "tldr_chars": len(result.tldr),
        "evidence_keys": len(result.evidence),
        "evidence": dict(result.evidence),
    }


def _compare_memory(score: Score, baseline: Score) -> Score:
    if not (score["parsed"] and baseline["parsed"]):
        return {}
    compared: Score = {
        "vsA_outline_delta": abs(score["outline_sections"] - baseline["outline_sections"])
    }
    agreement = evidence_agreement(score["evidence"], baseline["evidence"])
    if agreement is not None:
        compared["vsA_evidence_agree"] = agreement
    return compared


# ─── Description analysis, quiz, judge ───────────────────────────────────────


def _score_description(raw: str, _context: Mapping[str, Any]) -> Score:
    data = parse_json_response(raw)
    if not data:
        return {"parsed": False}
    analysis = _parse_analysis(data).to_dict()
    keys = {
        field: sorted({str(item.get(key)) for item in analysis[field]})
        for field, key in _DESCRIPTION_KEYS.items()
    }
    return {"parsed": True, "has_content": any(keys.values()), "keys": keys}


def _compare_description(score: Score, baseline: Score) -> Score:
    if not (score["parsed"] and baseline["parsed"]):
        return {}
    ours, theirs = score["keys"], baseline["keys"]
    agreements = [jaccard(set(ours[f]), set(theirs[f])) for f in _DESCRIPTION_KEYS]
    return {"vsA_fields_agree": round(sum(agreements) / len(agreements), 3)}


def _score_quiz(raw: str, _context: Mapping[str, Any]) -> Score:
    data = parse_json_response(raw)
    items = enrichment._quiz_items(data)
    valid = enrichment.parse_quiz(data, len(items))
    questions = valid[: enrichment._quiz_cap()]
    return {
        "parsed": bool(data),
        "questions": len(questions),
        "dropped": len(items) - len(valid),
        "usable": len(questions) >= enrichment.MIN_QUIZ_QUESTIONS,
    }


def _score_faithfulness(raw: str, _context: Mapping[str, Any]) -> Score:
    verdict = _parse_judge_response(raw)
    return {"parsed": verdict is not None, "grounded": verdict}


def _compare_faithfulness(score: Score, baseline: Score) -> Score:
    if score["grounded"] is None or baseline["grounded"] is None:
        return {}
    return {"vsA_agree": score["grounded"] == baseline["grounded"]}


# ─── Translation ─────────────────────────────────────────────────────────────


def _left_in_source(original: str, translated: str, target: str) -> bool:
    """The string came back untranslated by the target's own rule."""
    if target == "en":
        return translated == original or _untranslated(translated)
    return bool(_LATIN_WORD.search(original)) and not _HEBREW.search(translated)


def _score_translation(raw: str, context: Mapping[str, Any]) -> Score:
    originals: list[str] = list(context["originals"])
    translations = _string_list(raw, len(originals))
    if translations is None:
        return {"parsed": False}
    target = str(context["target"])
    echo = is_mirror if target == "en" else is_batch_echo
    left = sum(_left_in_source(o, t, target) for o, t in zip(originals, translations, strict=True))
    chars_in = sum(map(len, originals))
    return {
        "parsed": True,
        "echo": echo(originals, translations),
        "left_in_source": left,
        "left_share": round(left / max(1, len(originals)), 3),
        "length_ratio": round(sum(map(len, translations)) / max(1, chars_in), 3),
    }


# ─── Dispatch ────────────────────────────────────────────────────────────────

Scorer = Callable[[str, Mapping[str, Any]], Score]
Comparer = Callable[[Score, Score], Score]

SCORERS: dict[str, Scorer] = {
    "tier_probe": _score_tier_probe,
    "description": _score_description,
    "plan": _score_plan,
    "memory": _score_memory,
    "extraction": score_extraction,
    "quiz": _score_quiz,
    "vision": score_vision,
    "translation_output": _score_translation,
    "translation_rag": _score_translation,
    "faithfulness": _score_faithfulness,
}
COMPARERS: dict[str, Comparer] = {
    "tier_probe": _compare_tier_probe,
    "description": _compare_description,
    "plan": _compare_plan,
    "memory": _compare_memory,
    "extraction": compare_extraction,
    "vision": compare_vision,
    "faithfulness": _compare_faithfulness,
}


def score_answer(call_type: str, raw: str, context: Mapping[str, Any]) -> Score:
    """One answer's metrics, read by the pipeline's own parser for its call type."""
    return SCORERS[call_type](raw, context)


def compare_to_a(call_type: str, score: Score, baseline: Score | None) -> Score:
    """Agreement with arm A's answer to the same request ({} without one)."""
    comparer = COMPARERS.get(call_type)
    if comparer is None or baseline is None:
        return {}
    return comparer(score, baseline)
