"""Post-extraction component promotion.

The planner (stage 3) runs *before* extraction (stage 4), so it can only pick
components from the title / chapter structure — it never sees the extracted
item counts, concept-connection graph, or frame coverage that the richer
components require. This module runs inside the assembler (stage 8), where
those facts ARE known, and upgrades a basic component to a richer sibling when
the data supports it.

Each rule re-shapes props by calling the *target* assembler (never a bare
rename) so a promoted tab is always render-valid; ``assemble_response``
re-validates the result immediately afterward.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.shared_config.assembly_policy import assembly_policy

from .assemblers_overhaul import assemble_concept_canvas
from .assemblers_primary import assemble_spot_explorer
from .derived_guard import is_numeric_axis

logger = logging.getLogger(__name__)

# Promotion thresholds — each is a named constant with a dedicated unit test.
RADAR_AXIS_THRESHOLD = 4  # comparison  -> comparison_radar (scoreable rows)
CONCEPT_CONNECTION_MIN = 2  # flash_deck  -> concept_canvas (connected concepts)
STEP_FLOW_THRESHOLD = 8  # step_player -> step_flow_canvas (step count)
INFO_GRID_LONG_VALUE = 120  # info_grid   -> spot_explorer (value length, chars)
INFO_GRID_LONG_MIN = 2  # need this many long, card-worthy rows to promote

# Each promotable component and the richer sibling it becomes when its rule
# fires. Public because the golden eval (scripts/_eval_assertions.py) reads it
# to credit a planned component that renders as its promotion target.
COMPONENT_PROMOTIONS: dict[str, str] = {
    "comparison": "comparison_radar",
    "step_player": "step_flow_canvas",
    "flash_deck": "concept_canvas",
    "info_grid": "spot_explorer",
}

# Domains whose `concepts` extraction carries a connection graph worth rendering
# as a canvas rather than a flat flashcard deck.
_CONCEPT_DOMAINS: frozenset[str] = frozenset(assembly_policy()["conceptPromotionDomains"])

# Domains whose steps are a procedure to follow along (Cooking / Build mode
# depend on step_player): they never render as a canvas, however long (D8).
# domains.json ``assemblyPolicy``.
STEP_LIST_DOMAINS: frozenset[str] = frozenset(assembly_policy()["stepListDomains"])

# A rank ("1st", "22nd", "3RD") has the guard's number shape but is no
# measurement: scaled as one, the worse rank would win its spoke. Twin of
# ORDINAL_CELL in apps/web …/interactive/comparison/radar-scoring.ts.
_ORDINAL_CELL = re.compile(r"^\d[\d,]*(?:st|nd|rd|th)$", re.IGNORECASE)


def _is_ordinal(value: Any) -> bool:
    return isinstance(value, str) and _ORDINAL_CELL.match(value.strip()) is not None


def _is_radar_axis(row: Any) -> bool:
    """A numeric axis (`derived_guard.is_numeric_axis`, D13) with no ranked side."""
    return is_numeric_axis(row) and not (
        _is_ordinal(row.get("thisProduct")) or _is_ordinal(row.get("competitor"))
    )


def _promote_comparison(props: dict) -> dict | None:
    """comparison -> comparison_radar when enough rows are numeric axes.

    `comparison_radar` shares the comparison data contract, so the props are
    forwarded unchanged; the renderer chooses the radar visualization. An
    axis is a row whose both sides are measurements (`_is_radar_axis`).
    """
    rows = props.get("comparisons")
    if not isinstance(rows, list):
        return None
    if sum(1 for row in rows if _is_radar_axis(row)) >= RADAR_AXIS_THRESHOLD:
        return props
    return None


def _promote_step_player(props: dict, domain: str) -> dict | None:
    """step_player -> step_flow_canvas for long procedures.

    Same data contract as step_player; the canvas renderer lays the steps out
    as a connected flow once the list is long enough to benefit from it.
    Never for `STEP_LIST_DOMAINS`, whose steps are followed, not browsed.
    """
    if domain in STEP_LIST_DOMAINS:
        return None
    steps = props.get("steps")
    if isinstance(steps, list) and len(steps) >= STEP_FLOW_THRESHOLD:
        return props
    return None


def _resolve_concept_source(
    data: Any,
    extraction: dict | None,
    domain: str,
) -> list | None:
    """Find the concept list that backs a flash_deck tab.

    Prefers the tab's own resolved data when it already carries connections;
    otherwise falls back to the domain's `concepts` extraction block.
    """
    if isinstance(data, list) and any(isinstance(x, dict) and x.get("connections") for x in data):
        return data
    dom = extraction.get(domain) if isinstance(extraction, dict) else None
    if isinstance(dom, dict) and isinstance(dom.get("concepts"), list):
        return dom["concepts"]
    return None


def _promote_flash_deck(
    data: Any,
    extraction: dict | None,
    domain: str,
) -> dict | None:
    """flash_deck -> concept_canvas when the concepts form a connection graph."""
    if domain not in _CONCEPT_DOMAINS:
        return None
    concepts_src = _resolve_concept_source(data, extraction, domain)
    if concepts_src is None:
        return None
    props = assemble_concept_canvas({}, concepts_src, extraction or {}, None)
    if props is None:
        return None
    connected = sum(
        1 for c in props.get("concepts", []) if isinstance(c, dict) and c.get("connections")
    )
    if connected >= CONCEPT_CONNECTION_MIN:
        return props
    return None


def _info_row_to_spot(row: dict) -> dict:
    """info_grid row -> spot input: key -> name, value -> description.

    `emoji` and `timestamp` ride along — the spot normalizer passes them
    through, and the timestamp is the card's seek chip (R2).
    """
    spot = {"name": row.get("key", ""), "description": row.get("value", "")}
    for field in ("emoji", "timestamp"):
        if row.get(field) is not None and row.get(field) != "":
            spot[field] = row[field]
    return spot


def _promote_info_grid(
    props: dict,
    extraction: dict | None,
) -> dict | None:
    """info_grid -> spot_explorer when rows are description-heavy cards.

    A grid of short reference pairs stays a grid; a grid whose values are long
    descriptions is really a set of browsable cards, so we re-shape it through
    the spot_explorer assembler. Each spot keeps its row's `timestamp` and
    carries the row's `evidence` as the card's `quote` line and its `category`
    badge (R2, R5, R10). A row the spot normalizer would refuse (a headline
    chip with no body) keeps the whole tab a grid, where every row still
    renders — degrade, never drop.
    """
    items = props.get("items")
    if not isinstance(items, list):
        return None
    rows = [it for it in items if isinstance(it, dict)]
    long_rows = [r for r in rows if len(str(r.get("value") or "")) > INFO_GRID_LONG_VALUE]
    if len(long_rows) < INFO_GRID_LONG_MIN:
        return None
    spot_input = [_info_row_to_spot(r) for r in rows]
    spot_props = assemble_spot_explorer({}, spot_input, extraction or {}, None)
    # The assembler drops each row its normalizer refuses — one row short
    # means a refused row, which keeps the whole tab a grid.
    if spot_props is None or len(spot_props["spots"]) != len(rows):
        return None
    for spot, row in zip(spot_props["spots"], rows, strict=True):
        if row.get("evidence"):
            spot["quote"] = row["evidence"]
        if row.get("category"):
            spot["category"] = row["category"]
    return spot_props


def promote_component(
    component: str,
    props: dict,
    data: Any,
    extraction: dict | None,
    domain: str,
) -> tuple[str, dict]:
    """Promote a component to a richer sibling when the data supports it.

    Returns ``(component, props)`` unchanged when no rule fires. A fired rule
    always returns re-shaped, render-valid props for the new component, which
    is ``COMPONENT_PROMOTIONS[component]``.
    """
    if component == "step_flow_canvas" and domain in STEP_LIST_DOMAINS:
        # The planner may pick the canvas outright; the step list is the same
        # contract, so the tab ships as step_player (Cooking / Build mode).
        logger.info("[assembly] step_flow_canvas -> step_player: %s steps are followed", domain)
        return "step_player", props

    new_props: dict | None = None
    if component == "comparison":
        new_props = _promote_comparison(props)
    elif component == "step_player":
        new_props = _promote_step_player(props, domain)
    elif component == "flash_deck":
        new_props = _promote_flash_deck(data, extraction, domain)
    elif component == "info_grid":
        new_props = _promote_info_grid(props, extraction)

    if new_props is None:
        return component, props
    new_component = COMPONENT_PROMOTIONS[component]
    logger.info("[assembly] promoted %s -> %s", component, new_component)
    return new_component, new_props


# ─────────────────────────────────────────────────────
# Demotion — degrade, never drop
# ─────────────────────────────────────────────────────

# When a rich assembler can't build its component from non-empty data (shape
# mismatch, min-item gate), walk DOWN this ladder instead of dropping the tab.
# Each rung calls the target assembler (never a bare rename — the promotion
# contract) and the caller re-validates. display_section accepts anything
# non-None, so it is the terminal rung; only truly empty data still drops.
_DEMOTE_LADDER: dict[str, tuple[str, ...]] = {
    "step_player": ("checklist", "display_section"),
    "step_flow_canvas": ("checklist", "display_section"),
    "spot_explorer": ("info_grid", "checklist", "display_section"),
    "info_grid": ("checklist", "display_section"),
    "tier_list": ("info_grid", "display_section"),
    "comparison": ("info_grid", "display_section"),
    "comparison_radar": ("info_grid", "display_section"),
    "concept_canvas": ("flash_deck", "info_grid", "display_section"),
    "connect_canvas": ("flash_deck", "info_grid", "display_section"),
    "claims_tracker": ("info_grid", "display_section"),
    "packing_mission": ("checklist", "display_section"),
    "code_playground": ("display_section",),
    "formation_diagram": ("info_grid", "display_section"),
    "diagram_card": ("info_grid", "display_section"),
    "workout_room": ("checklist", "display_section"),
    # Deliberately non-demotable: their data shapes (quiz questions, frame
    # dicts, lyric lines) render as garbage in generic components — an
    # invalid quiz is better dropped than dumped.
    "quiz_arena": (),
    "video_filmstrip": (),
    "lyrics_karaoke": (),
    "frame_strip": (),
}
_DEMOTE_DEFAULT: tuple[str, ...] = ("display_section",)


def demote_component(
    component: str,
    tab: dict,
    data: Any,
    extraction: dict | None,
    enrichment: dict | None,
    blocked: frozenset[str] = frozenset(),
) -> tuple[str, dict] | None:
    """Walk the demote ladder for a component whose assembler returned None.

    Returns ``(simpler_component, props)`` from the first rung whose assembler
    produces validating props, or None when every rung fails (which for the
    display_section terminal only happens on data that is truly None).
    Rungs in ``blocked`` are skipped: components the plan's evidence ruled
    out (e.g. a food vlog's spot list must not degrade into a ``checklist``).
    """
    from .core import _validate_assembled_props  # local import avoids cycle
    from .density import enforce_density
    from .registry import ASSEMBLER_REGISTRY

    for target in _DEMOTE_LADDER.get(component, _DEMOTE_DEFAULT):
        if target in blocked:
            logger.info(
                "[assembly] demote %s -> %s skipped: ruled out by evidence", component, target
            )
            continue
        assembler = ASSEMBLER_REGISTRY.get(target)
        if assembler is None:
            continue
        try:
            props = assembler(tab, data, extraction or {}, enrichment)
        except Exception as e:  # noqa: BLE001 — a rung failure is not fatal
            # WARNING, not debug: a systematically broken rung assembler would
            # otherwise silently degrade every affected video to the terminal
            # rung with no prod-visible signal.
            logger.warning(
                "Demote rung %s -> %s raised: %s: %s", component, target, type(e).__name__, e
            )
            continue
        if props is None:
            continue
        # Same density pass the main path applies to planned tabs — a demoted
        # info_grid must obey the identical text caps and min-item folding.
        props = enforce_density(target, props)
        if props is None or not _validate_assembled_props(target, props):
            continue
        logger.info("[assembly] TAB DEGRADED: %s -> %s", component, target)
        return target, props
    return None
