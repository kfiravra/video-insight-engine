"""Component → item-list key: the one table assembly reads a tab's items through.

Validation drops a tab whose list is empty (``core._COMPONENT_REQUIRED_LISTS``),
the item caps trim it (``core._COUNT_KEYS``), the label carries its count
(``labels._LABEL_COUNT_KEYS`` — the FE's ``COUNT_PROP_BY_COMPONENT``) and the
post-promotion dedupe compares it (``dedupe``). Three hand-kept copies of this
table used to live in ``core`` (output-quality 0b review); the others derive.
"""

from __future__ import annotations

# Live (v2 registry) components only — legacy v1 names were dropped alongside
# their ASSEMBLER_REGISTRY entries (cached rows fall through to display_section).
COMPONENT_LIST_KEYS: dict[str, str] = {
    # Kept across the overhaul
    "moment_track": "items",
    "spot_explorer": "spots",
    "info_grid": "items",
    "checklist": "items",
    "step_player": "steps",
    "flash_deck": "cards",
    "budget": "breakdown",
    "comparison": "comparisons",
    # Video-to-action overhaul: new components
    "code_playground": "snippets",
    "workout_room": "exercises",
    "quiz_arena": "questions",
    "lyrics_karaoke": "sections",
    "video_filmstrip": "frames",
    "concept_canvas": "concepts",
    "connect_canvas": "pairs",
    "step_flow_canvas": "steps",
    "comparison_radar": "comparisons",
    "packing_mission": "items",
    # interactive-overhaul-v2 P5b — news signature component
    "claims_tracker": "claims",
    # interactive-overhaul-v2 P5c/d — gaming + sport signature components
    "tier_list": "items",
    "formation_diagram": "positions",
    # Secondary-tier (attachment-only) — interactive-overhaul-v2 P2.
    # tip_callout/summary_header carry scalar text (no list), so they are not
    # listed here; they're validated by their assemblers returning None on empty.
    "stat_banner": "stats",
    "diagram_card": "nodes",
    "frame_strip": "frames",
    "quick_quiz": "questions",
}

# A comparison ships its rows, pros and cons together: the strip counts all three
# (``inferItemCount``) and the dedupe compares all three.
COMPARISON_COMPONENTS: frozenset[str] = frozenset({"comparison", "comparison_radar"})
COMPARISON_LIST_KEYS: tuple[str, ...] = ("comparisons", "pros", "cons")


def item_list_keys(component: str) -> tuple[str, ...]:
    """The props keys holding the items a ``component`` tab ships; () when unregistered."""
    if component in COMPARISON_COMPONENTS:
        return COMPARISON_LIST_KEYS
    key = COMPONENT_LIST_KEYS.get(component)
    return (key,) if key else ()
