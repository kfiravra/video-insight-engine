"""Assemblers — Video-to-Action overhaul family.

concept_canvas, connect_canvas, step_flow_canvas, comparison_radar,
code_playground, quiz_arena, packing_mission, workout_room,
lyrics_karaoke, video_filmstrip.

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

import logging
from typing import Any

from .assemblers_learning import (
    assemble_exercise_tracker,
    assemble_lyrics_player,
)
from .assemblers_primary import (
    assemble_code_explorer,
    assemble_comparison,
    assemble_step_player,
)
from .derived_guard import anchor_seconds
from .normalizers import (
    _normalize_quiz_question,
    _normalize_scenario_item,
)
from .sampling import concept_richness, rank_sample

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# Video-to-Action overhaul: new component assemblers
# ─────────────────────────────────────────────────────


# Relationship types the concept canvas knows how to render (line-style + arrowhead).
_CONCEPT_RELATIONS: frozenset[str] = frozenset(
    {"causes", "contrasts", "requires", "partOf", "relatesTo"}
)
_DEFAULT_RELATION = "relatesTo"
_MAX_CONNECTIONS_PER_CONCEPT = 3
_DEFAULT_GROUP = "Concepts"
_CONCEPT_CANVAS_MAX = 30


def _connection_target(conn: Any) -> str:
    """Extract the target concept name from a connection — accepts a bare string
    (legacy) or a typed `{to, type}` object (v6+)."""
    if isinstance(conn, dict):
        return str(conn.get("to") or conn.get("name") or conn.get("target") or "").strip()
    return str(conn or "").strip()


def _normalize_connections(raw: Any) -> list[dict]:
    """Coerce a concept's `connections` into typed `[{to, type}]` objects.

    A bare string becomes `{to, type: relatesTo}` (legacy data). A dict's `type`
    is validated against the relation enum, falling back to `relatesTo`. Empty
    targets are dropped; the list is clamped to keep the graph readable.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for conn in raw:
        to = _connection_target(conn)
        if not to or to.lower() in seen:
            continue
        rel = _DEFAULT_RELATION
        if isinstance(conn, dict):
            candidate = str(conn.get("type") or "").strip()
            if candidate in _CONCEPT_RELATIONS:
                rel = candidate
        out.append({"to": to, "type": rel})
        seen.add(to.lower())
        if len(out) >= _MAX_CONNECTIONS_PER_CONCEPT:
            break
    return out


def _normalize_concept(item: Any) -> dict | None:
    """Coerce a concept item to {name, emoji, definition, example?, analogy?,
    group?, timestamp?, connections:[{to,type}]}."""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or item.get("title") or "").strip()
    definition = str(
        item.get("definition") or item.get("description") or item.get("detail") or ""
    ).strip()
    if not name or not definition:
        return None
    result: dict[str, Any] = {
        "name": name,
        "emoji": str(item.get("emoji") or "💡"),
        "definition": definition,
        "connections": _normalize_connections(item.get("connections")),
    }
    if item.get("example"):
        result["example"] = str(item["example"])
    if item.get("analogy"):
        result["analogy"] = str(item["analogy"])
    group = str(item.get("group") or "").strip()
    if group:
        result["group"] = group
    # Preserve a timestamp so `inject_frame_thumbnails` can attach frame evidence.
    ts = item.get("timestamp") if item.get("timestamp") is not None else item.get("seconds")
    if isinstance(ts, (int, float)):
        result["timestamp"] = ts
    return result


def _cluster_groups(concepts: list[dict]) -> dict[str, str]:
    """Connected-components fallback: when no concept carries a `group`, derive
    clusters from the connection graph so the Groups view still has structure.

    Returns a map of lowercased concept name → synthesized group label
    ("Group 1", "Group 2", …). Concepts in the same component share a label;
    isolated concepts each form their own single-member group.
    """
    names_lower = {c["name"].strip().lower() for c in concepts}
    adjacency: dict[str, set[str]] = {n: set() for n in names_lower}
    for concept in concepts:
        src = concept["name"].strip().lower()
        for conn in concept.get("connections") or []:
            tgt = _connection_target(conn).lower()
            if tgt in names_lower and tgt != src:
                adjacency[src].add(tgt)
                adjacency[tgt].add(src)

    label_for: dict[str, str] = {}
    cluster_index = 0
    for concept in concepts:  # iterate in concept order for deterministic labels
        start = concept["name"].strip().lower()
        if start in label_for:
            continue
        cluster_index += 1
        label = f"Group {cluster_index}"
        stack = [start]
        while stack:
            node = stack.pop()
            if node in label_for:
                continue
            label_for[node] = label
            stack.extend(neighbor for neighbor in adjacency[node] if neighbor not in label_for)
    return label_for


def assemble_concept_canvas(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build ConceptCanvas props from learning/science `concepts` data.

    Normalizes typed connections, validates each `to` against a real concept
    name (drops hallucinated / self references), assigns/derives a group per
    concept, and returns `{concepts, groups}` where `groups` is the ordered,
    de-duped list of group labels. Caps at a generous 30 concepts — grouping,
    not truncation, is meant to absorb density.
    """
    if isinstance(data, dict):
        data = data.get("concepts") or data.get("items") or []
    if not isinstance(data, list) or len(data) < 2:
        return None
    concepts = [c for c in (_normalize_concept(item) for item in data) if c is not None]
    if len(concepts) < 2:
        return None

    if len(concepts) > _CONCEPT_CANVAS_MAX:
        # Rank-sampled by richness in the original order (R9): the closing
        # concepts of a long video survive, not just the first 30.
        logger.info("concept_canvas: capping %d concepts to %d", len(concepts), _CONCEPT_CANVAS_MAX)
        concepts = rank_sample(concepts, _CONCEPT_CANVAS_MAX, concept_richness)

    # Validate connections against the surviving concept names (case-insensitive),
    # canonicalize the `to` casing, and drop hallucinated / self references.
    names_lower = {c["name"].strip().lower(): c["name"] for c in concepts}
    for concept in concepts:
        src_lower = concept["name"].strip().lower()
        valid: list[dict] = []
        for conn in concept.get("connections") or []:
            canonical = names_lower.get(conn["to"].strip().lower())
            if canonical and canonical.strip().lower() != src_lower:
                valid.append({"to": canonical, "type": conn["type"]})
        concept["connections"] = valid

    # Grouping: honour explicit groups when any concept has one (defaulting the
    # rest to "Concepts"); otherwise synthesize clusters from the graph.
    has_explicit_group = any(c.get("group") for c in concepts)
    if not has_explicit_group:
        cluster_label = _cluster_groups(concepts)
        for concept in concepts:
            concept["group"] = cluster_label[concept["name"].strip().lower()]
    else:
        for concept in concepts:
            if not concept.get("group"):
                concept["group"] = _DEFAULT_GROUP

    groups: list[str] = []
    for concept in concepts:  # ordered by first appearance, de-duped
        group = concept["group"]
        if group not in groups:
            groups.append(group)

    return {"concepts": concepts, "groups": groups}


_CONNECT_MIN_PAIRS = 2
_CONNECT_MAX_PAIRS = 8


def assemble_connect_canvas(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Graded drag-to-connect quiz. The answer key is DERIVED in assembly from
    `concepts[].connections` (the known concept graph) — no enrichment LLM call.

    Each connected concept yields one matching pair: prompt = the concept name,
    match = the name of a concept it connects to (the first connection that
    resolves to a real, distinct concept). Concepts with no resolvable
    connection are skipped. Needs ≥2 pairs to be a quiz, caps at 8.
    """
    if isinstance(data, dict):
        data = data.get("concepts") or data.get("items") or []
    if not isinstance(data, list) or len(data) < _CONNECT_MIN_PAIRS:
        return None

    concepts = [c for c in (_normalize_concept(item) for item in data) if c is not None]
    if len(concepts) < _CONNECT_MIN_PAIRS:
        return None

    names_lower = {c["name"].strip().lower(): c["name"] for c in concepts}
    pairs: list[dict] = []
    used_matches: set[str] = set()
    for concept in concepts:
        prompt = concept["name"].strip()
        for conn in concept.get("connections") or []:
            target = names_lower.get(_connection_target(conn).lower())
            if not target or target.strip().lower() == prompt.lower():
                continue
            if target in used_matches:
                continue
            pairs.append({"prompt": prompt, "match": target})
            used_matches.add(target)
            break
        if len(pairs) >= _CONNECT_MAX_PAIRS:
            break

    if len(pairs) < _CONNECT_MIN_PAIRS:
        return None
    return {"pairs": pairs}


def assemble_step_flow_canvas(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Step flow canvas — same data contract as step_player. Frontend lazy-loads
    the canvas renderer; small step lists fall back to step_player via the
    component-routing layer."""
    return assemble_step_player(tab, data, extraction, enrichment)


def assemble_comparison_radar(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """ComparisonRadar shares the comparison data contract — it just chooses a
    radar visualization for ≥3 axes. The frontend falls back to the table when
    rows < 3, so the assembler stays identical."""
    return assemble_comparison(tab, data, extraction, enrichment)


def assemble_code_playground(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """CodePlayground replaces CodeExplorer — same data contract."""
    return assemble_code_explorer(tab, data, extraction, enrichment)


def assemble_quiz_arena(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """QuizArena absorbs Scenario data — same primary contract as quiz but
    accepts items with optional `context` field that flags scenario kind."""
    if not isinstance(data, list) or len(data) < 1:
        return None
    questions: list[dict] = []
    for item in data:
        normalized = _normalize_quiz_question(item)
        question = normalized if normalized is not None else _scenario_question(item)
        if question is not None:
            questions.append(question)
    if not questions:
        return None
    return {"questions": questions}


def _scenario_question(item: Any) -> dict | None:
    """A scenario item (options[].correct) as an arena question; it keeps the item's
    seek point like a standard question does (``anchor_seconds``)."""
    scenario = _normalize_scenario_item(item) if isinstance(item, dict) else None
    if scenario is None:
        return None
    opts = [opt for opt in scenario.get("options") or [] if isinstance(opt, dict)]
    if not opts:
        return None
    correct_idx = next((i for i, opt in enumerate(opts) if opt.get("correct")), 0)
    explanation = next(
        (str(opt["explanation"]) for opt in opts if opt.get("correct") and opt.get("explanation")),
        "",
    )
    question: dict[str, Any] = {
        "question": scenario.get("question", ""),
        "options": [str(opt.get("text") or "") for opt in opts],
        "correctIndex": correct_idx,
        "explanation": explanation,
        "kind": "scenario",
    }
    timestamp = anchor_seconds(item)
    if timestamp is not None:
        question["timestamp"] = timestamp
    return question


def _normalize_packing_item(item: Any) -> dict | None:
    """Coerce a packing list item to {item, category?, essential?, weight?, emoji?}."""
    if isinstance(item, str):
        text = item.strip()
        return {"item": text} if text else None
    if not isinstance(item, dict):
        return None
    name = str(item.get("item") or item.get("name") or item.get("label") or "").strip()
    if not name:
        return None
    result: dict[str, Any] = {"item": name}
    for field in ("category", "emoji"):
        if item.get(field):
            result[field] = str(item[field])
    if isinstance(item.get("essential"), bool):
        result["essential"] = item["essential"]
    weight = item.get("weight")
    if isinstance(weight, (int, float)) and weight > 0:
        result["weight"] = weight
    return result


def assemble_packing_mission(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """PackingMission props — drag-and-drop packing list for travel domain."""
    raw_list = data
    if isinstance(data, dict):
        raw_list = data.get("packingList") or data.get("items") or []
    if not isinstance(raw_list, list) or len(raw_list) < 2:
        return None
    items = [p for p in (_normalize_packing_item(item) for item in raw_list) if p is not None]
    if len(items) < 2:
        return None
    return {"items": items}


def assemble_workout_room(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """WorkoutRoom replaces ExerciseInteractive — same data contract."""
    return assemble_exercise_tracker(tab, data, extraction, enrichment)


def assemble_lyrics_karaoke(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """LyricsKaraoke replaces LyricsPlayer — same data contract."""
    return assemble_lyrics_player(tab, data, extraction, enrichment)


def _normalize_filmstrip_frame(item: Any) -> dict | None:
    """Coerce a frame item to {thumbnailUrl, timestamp, caption?, ocr?, sceneType?}."""
    if not isinstance(item, dict):
        return None
    thumb = item.get("thumbnailUrl") or item.get("url") or item.get("s3_url")
    if not isinstance(thumb, str) or not thumb.strip():
        return None
    timestamp = item.get("timestamp") or item.get("seconds") or 0
    if not isinstance(timestamp, (int, float)):
        return None
    result: dict[str, Any] = {"thumbnailUrl": thumb, "timestamp": int(timestamp)}
    s3_key = item.get("s3Key") or item.get("s3_key")
    if isinstance(s3_key, str) and s3_key.strip():
        result["s3Key"] = s3_key.strip()
    caption = item.get("caption") or item.get("frameCaption") or item.get("description")
    if isinstance(caption, str) and caption.strip():
        result["caption"] = caption.strip()
    ocr = item.get("ocr") or item.get("frameOcr")
    if isinstance(ocr, str) and ocr.strip():
        result["ocr"] = ocr.strip()
    scene = item.get("sceneType") or item.get("frameSceneType")
    if isinstance(scene, str) and scene.strip():
        result["sceneType"] = scene.strip()
    return result


def assemble_video_filmstrip(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """VideoFilmstrip replaces standalone gallery — horizontal scrubber of frames."""
    raw_frames = data
    if isinstance(data, dict):
        raw_frames = data.get("frames") or data.get("images") or []
    if not isinstance(raw_frames, list) or len(raw_frames) < 3:
        return None
    frames = [f for f in (_normalize_filmstrip_frame(item) for item in raw_frames) if f is not None]
    if len(frames) < 3:
        return None
    return {"frames": frames}
