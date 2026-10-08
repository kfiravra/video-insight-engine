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
from typing import Any

from .assemblers_overhaul import assemble_concept_canvas
from .assemblers_primary import assemble_spot_explorer

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
_CONCEPT_DOMAINS: frozenset[str] = frozenset({"learning", "science"})


def _promote_comparison(props: dict) -> dict | None:
    """comparison -> comparison_radar when there are enough scoreable axes.

    `comparison_radar` shares the comparison data contract, so the props are
    forwarded unchanged; the renderer chooses the radar visualization.
    """
    rows = props.get("comparisons")
    if isinstance(rows, list) and len(rows) >= RADAR_AXIS_THRESHOLD:
        return props
    return None


def _promote_step_player(props: dict) -> dict | None:
    """step_player -> step_flow_canvas for long procedures.

    Same data contract as step_player; the canvas renderer lays the steps out
    as a connected flow once the list is long enough to benefit from it.
    """
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


def _promote_info_grid(
    props: dict,
    extraction: dict | None,
) -> dict | None:
    """info_grid -> spot_explorer when rows are description-heavy cards.

    A grid of short reference pairs stays a grid; a grid whose values are long
    descriptions is really a set of browsable cards, so we re-shape it through
    the spot_explorer assembler (key -> name, value -> description).
    """
    items = props.get("items")
    if not isinstance(items, list):
        return None
    long_items = [
        it
        for it in items
        if isinstance(it, dict) and len(str(it.get("value") or "")) > INFO_GRID_LONG_VALUE
    ]
    if len(long_items) < INFO_GRID_LONG_MIN:
        return None
    spot_input = [
        {
            "name": it.get("key", ""),
            "description": it.get("value", ""),
            **({"emoji": it["emoji"]} if it.get("emoji") else {}),
        }
        for it in items
        if isinstance(it, dict)
    ]
    return assemble_spot_explorer({}, spot_input, extraction or {}, None)


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
    new_props: dict | None = None
    if component == "comparison":
        new_props = _promote_comparison(props)
    elif component == "step_player":
        new_props = _promote_step_player(props)
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
) -> tuple[str, dict] | None:
    """Walk the demote ladder for a component whose assembler returned None.

    Returns ``(simpler_component, props)`` from the first rung whose assembler
    produces validating props, or None when every rung fails (which for the
    display_section terminal only happens on data that is truly None).
    """
    from .core import _validate_assembled_props  # local import avoids cycle
    from .density import enforce_density
    from .registry import ASSEMBLER_REGISTRY

    for target in _DEMOTE_LADDER.get(component, _DEMOTE_DEFAULT):
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
