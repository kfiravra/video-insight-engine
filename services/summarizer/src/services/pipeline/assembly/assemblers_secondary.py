"""Assemblers — secondary-tier + signature components.

stat_banner, tip_callout, summary_header, diagram_card, frame_strip,
quick_quiz (interactive-overhaul-v2 P2) and the claims_tracker /
tier_list / formation_diagram signature components (P5b-d).

Signature: (tab, data, extraction, enrichment) -> props dict | None
Returning None means the tab should be dropped (no data).
"""

from __future__ import annotations

from typing import Any

from .assemblers_overhaul import (
    _connection_target,
    assemble_quiz_arena,
    assemble_video_filmstrip,
)
from .derived_guard import anchor_seconds, stated_scalars
from .normalizers import (
    _coerce_float,
    _coerce_int,
)
from .text_utils import truncate_words

# ─────────────────────────────────────────────────────
# Secondary-tier assemblers (interactive-overhaul-v2 P2)
# ─────────────────────────────────────────────────────
# Attachment-only components. The orchestrator's `attach_secondaries` builds
# their props directly and never routes them through the primary tab loop, but
# they are registered so the contract-parity test (every assembler maps to a
# known component) and any future direct-resolution path stay valid.


def assemble_stat_banner(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Equal-weight compact stats. Expects data as list[{label, value, emoji?}]."""
    raw = (
        data
        if isinstance(data, list)
        else (data or {}).get("stats")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(raw, list):
        return None
    stats: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            continue
        stat = {"label": label, "value": value}
        if item.get("emoji"):
            stat["emoji"] = str(item["emoji"])
        stats.append(stat)
    return {"stats": stats} if stats else None


def assemble_tip_callout(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Single highlighted aside. Expects a string or {text, style?, title?}."""
    if isinstance(data, str):
        text = data.strip()
        return {"text": text, "style": "tip"} if text else None
    if not isinstance(data, dict):
        return None
    text = str(data.get("text") or "").strip()
    if not text:
        return None
    result: dict[str, Any] = {"text": text}
    style = data.get("style")
    result["style"] = style if style in ("tip", "warning", "note") else "tip"
    if data.get("title"):
        result["title"] = str(data["title"])
    return result


def assemble_summary_header(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """One-line orientation for a dense tab. Expects string or {summary, ...}."""
    if isinstance(data, str):
        summary = data.strip()
        return {"summary": summary} if summary else None
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None
    result: dict[str, Any] = {"summary": summary}
    if data.get("title"):
        result["title"] = str(data["title"])
    if data.get("emoji"):
        result["emoji"] = str(data["emoji"])
    return result


_DIAGRAM_MAX_NODES = 8


def _build_diagram_edges_from_connections(
    raw_items: list,
    nodes: list[dict],
) -> list[dict]:
    """Derive index-addressed edges from each item's `connections[]` adjacency
    list (concept graph). Unmatched / self / out-of-range targets are dropped.

    The node list is the post-cap list, so we match connection names against the
    labels that actually survived. Returns [] when no item carried connections.
    """
    label_index: dict[str, int] = {}
    for i, node in enumerate(nodes):
        label = str(node.get("label") or "").strip().lower()
        if label:
            label_index.setdefault(label, i)
    edges: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for source_idx, item in enumerate(raw_items[: len(nodes)]):
        if not isinstance(item, dict):
            continue
        connections = item.get("connections")
        if not isinstance(connections, list):
            continue
        for conn in connections:
            target_idx = label_index.get(_connection_target(conn).lower())
            if target_idx is None or target_idx == source_idx:
                continue
            edge = (source_idx, target_idx)
            if edge in seen:
                continue
            seen.add(edge)
            edges.append({"source": source_idx, "target": target_idx})
    return edges


def assemble_diagram_card(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Read-only diagram. Builds nodes from list[{label|name, detail?, emoji?}]
    and edges either from concept `connections[]` (architecture / process graph)
    or, absent connections, leaves edges empty so the frontend renders a
    sequential chain (step ordering). Conservative: caps at 8 nodes."""
    raw = (
        data
        if isinstance(data, list)
        else (data or {}).get("nodes")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(raw, list):
        return None
    nodes: list[dict] = []
    source_items: list = []
    for item in raw:
        if isinstance(item, str):
            label = item.strip()
            if label:
                nodes.append({"label": label})
                source_items.append({})
            continue
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        if not label:
            continue
        node: dict[str, Any] = {"label": label}
        detail = item.get("detail") or item.get("definition") or item.get("description")
        if detail:
            node["detail"] = truncate_words(str(detail), 120)
        if item.get("emoji"):
            node["emoji"] = str(item["emoji"])
        nodes.append(node)
        source_items.append(item)
    if len(nodes) < 2:
        return None
    nodes = nodes[:_DIAGRAM_MAX_NODES]
    source_items = source_items[:_DIAGRAM_MAX_NODES]
    result: dict[str, Any] = {"nodes": nodes}
    edges = _build_diagram_edges_from_connections(source_items, nodes)
    if edges:
        result["edges"] = edges
    if isinstance(data, dict) and data.get("caption"):
        result["caption"] = str(data["caption"])
    return result


def assemble_frame_strip(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """frame_strip is a compact VideoFilmstrip — same {frames} contract."""
    return assemble_video_filmstrip(tab, data, extraction, enrichment)


def assemble_quick_quiz(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """quick_quiz is a single-question QuizArena — same {questions} contract."""
    return assemble_quiz_arena(tab, data, extraction, enrichment)


_CLAIM_STATUSES = frozenset({"verified", "disputed", "context"})
_CLAIMS_MIN = 2


def _normalize_claim(item: Any) -> dict | None:
    """Coerce a claim item to {claim, source, status, sourceCitation?, timestamp?}.

    Drops the row when there is no claim text. status defaults to "context"
    (the safe, non-asserting default) when the LLM omits or supplies an
    unknown value — we never silently upgrade an unverified claim to verified.
    ``source`` is who the video says made the claim; unattributed, it is ""
    (ClaimsTracker then prints no "Claimed by" line), never an invented
    "Reporter". The key stays because the FE schema requires a string.
    """
    if not isinstance(item, dict):
        return None
    claim = str(item.get("claim") or item.get("text") or "").strip()
    if not claim:
        return None
    # verified / disputed are assertions: they ship only when the claim is
    # anchored in the video (D13); otherwise the non-asserting "context".
    status = str(stated_scalars(item, ("status",)).get("status") or "").strip().lower()
    if status not in _CLAIM_STATUSES:
        status = "context"
    result: dict[str, Any] = {
        "claim": claim,
        "source": str(item.get("source") or item.get("speaker") or "").strip(),
        "status": status,
    }
    citation = item.get("sourceCitation") or item.get("citation")
    if citation:
        result["sourceCitation"] = str(citation).strip()
    # The anchor that let a status through is the seek point the reader checks it at.
    timestamp = anchor_seconds(item)
    if timestamp is not None:
        result["timestamp"] = timestamp
    return result


def assemble_claims_tracker(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build ClaimsTracker props from news.claims — the news signature surface.

    Each claim carries the assertion, who made it, a verified/disputed/context
    status, and an optional source citation. Needs ≥2 real claims to justify the
    tab; otherwise the data folds into info_grid / overview via the fallback layer.
    """
    if isinstance(data, dict):
        data = data.get("claims") or data.get("items") or []
    if not isinstance(data, list) or len(data) < _CLAIMS_MIN:
        return None
    claims = [c for c in (_normalize_claim(item) for item in data) if c is not None]
    if len(claims) < _CLAIMS_MIN:
        return None
    return {"claims": claims}


# ─────────────────────────────────────────────────────
# Gaming — tier_list (interactive-overhaul-v2 P5c)
# ─────────────────────────────────────────────────────

_TIER_VALUES = frozenset({"S", "A", "B", "C", "D"})
_TIER_LIST_MIN = 3


def _normalize_tier_item(item: Any) -> dict | None:
    """Coerce a ranking row to {item, tier?, reason?, emoji?, timestamp?}.

    Drops the row when there is no item label. `tier` is the creator's
    STATED placement (S/A/B/C/D): it ships only when the row is anchored in
    the video (D13) and valid, so the viewer places an unranked item
    themselves rather than us guessing a tier.
    """
    if not isinstance(item, dict):
        return None
    label = str(item.get("item") or item.get("name") or item.get("label") or "").strip()
    if not label:
        return None
    result: dict[str, Any] = {"item": truncate_words(label, 60)}
    tier = str(stated_scalars(item, ("tier",)).get("tier") or "").strip().upper()
    if tier in _TIER_VALUES:
        result["tier"] = tier
    timestamp = anchor_seconds(item)
    if timestamp is not None:
        result["timestamp"] = timestamp
    reason = item.get("reason") or item.get("note") or item.get("description")
    if reason:
        result["reason"] = truncate_words(str(reason), 120)
    if item.get("emoji"):
        result["emoji"] = str(item["emoji"])
    return result


def assemble_tier_list(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build TierList props from gaming.rankings — the gaming signature surface.

    Each item carries a label, an optional stated S/A/B/C/D tier (the answer
    key the user can override), and an optional reason. Needs ≥3 real items to
    justify the tab, and at least one stated tier — no stated rank → a list,
    not tiers (D13): the tab folds into info_grid / overview.
    """
    if isinstance(data, dict):
        data = data.get("rankings") or data.get("items") or []
    if not isinstance(data, list):
        return None
    items = [i for i in (_normalize_tier_item(it) for it in data) if i is not None]
    if len(items) < _TIER_LIST_MIN or not any("tier" in i for i in items):
        return None
    return {"items": items}


# ─────────────────────────────────────────────────────
# Sport — formation_diagram (interactive-overhaul-v2 P5d)
# ─────────────────────────────────────────────────────

_FORMATION_MIN = 3


def _normalize_position(item: Any) -> dict | None:
    """Coerce a position to {player, role?, x, y, number?}.

    Drops the row when there is no player name or the x/y coordinates are
    missing — a formation node must be placeable on the pitch.
    """
    if not isinstance(item, dict):
        return None
    player = str(item.get("player") or item.get("name") or "").strip()
    if not player:
        return None
    x = _coerce_float(item.get("x"))
    y = _coerce_float(item.get("y"))
    if x is None or y is None:
        return None
    result: dict[str, Any] = {
        "player": truncate_words(player, 40),
        "x": max(0.0, min(100.0, x)),
        "y": max(0.0, min(100.0, y)),
    }
    role = item.get("role") or item.get("position")
    if role:
        result["role"] = truncate_words(str(role), 24)
    number = _coerce_int(item.get("number"))
    if number is not None:
        result["number"] = number
    return result


def assemble_formation_diagram(
    tab: dict,
    data: Any,
    extraction: dict,
    enrichment: dict | None,
) -> dict | None:
    """Build FormationDiagram props from sport.formation — the sport signature.

    Players become nodes positioned on a pitch via 0-100 x/y percentages.
    Needs ≥3 placeable players to justify the tab; otherwise it folds away.
    """
    name: str | None = None
    team: str | None = None
    raw_positions: Any = data
    if isinstance(data, dict):
        name = str(data["name"]).strip() if data.get("name") else None
        team = str(data["team"]).strip() if data.get("team") else None
        raw_positions = data.get("positions") or data.get("players") or []
    if not isinstance(raw_positions, list):
        return None
    positions = [p for p in (_normalize_position(it) for it in raw_positions) if p is not None]
    if len(positions) < _FORMATION_MIN:
        return None
    result: dict[str, Any] = {"positions": positions}
    if name:
        result["name"] = name
    if team:
        result["team"] = team
    return result
