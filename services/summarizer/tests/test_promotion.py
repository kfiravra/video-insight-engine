"""Unit tests for post-extraction component promotion (interactive-overhaul-v2 1A)."""

from src.services.pipeline.assembly import (
    CONCEPT_CONNECTION_MIN,
    RADAR_AXIS_THRESHOLD,
    STEP_FLOW_THRESHOLD,
    promote_component,
)


def _comparison_rows(n: int) -> list[dict]:
    # Numeric on both sides — the radar scores only numeric axes (0b.9).
    return [
        {"feature": f"Spec {i}", "thisProduct": f"{i + 1} GB", "competitor": f"{i + 2} GB"}
        for i in range(n)
    ]


def _steps(n: int) -> list[dict]:
    return [{"number": i + 1, "instruction": f"Step {i}"} for i in range(n)]


def _concepts(n: int, connections: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        c: dict = {"name": f"Concept {i}", "definition": f"Definition of concept {i}"}
        if i < connections:
            c["connections"] = [f"Concept {(i + 1) % n}"]
        out.append(c)
    return out


# ─── comparison -> comparison_radar ───


class TestPromoteComparison:
    def test_promotes_at_threshold(self):
        props = {"comparisons": _comparison_rows(RADAR_AXIS_THRESHOLD), "pros": [], "cons": []}
        component, _ = promote_component("comparison", props, None, {}, "review")
        assert component == "comparison_radar"

    def test_no_promotion_below_threshold(self):
        props = {"comparisons": _comparison_rows(RADAR_AXIS_THRESHOLD - 1), "pros": [], "cons": []}
        component, _ = promote_component("comparison", props, None, {}, "review")
        assert component == "comparison"

    def test_props_forwarded_unchanged(self):
        props = {"comparisons": _comparison_rows(RADAR_AXIS_THRESHOLD), "pros": ["x"], "cons": []}
        _, out = promote_component("comparison", props, None, {}, "review")
        assert out is props


# ─── step_player -> step_flow_canvas ───


class TestPromoteStepPlayer:
    # Domain is "tech": food/project steps never become a canvas (0b.9, D8).
    def test_promotes_at_threshold(self):
        props = {"steps": _steps(STEP_FLOW_THRESHOLD)}
        component, _ = promote_component("step_player", props, None, {}, "tech")
        assert component == "step_flow_canvas"

    def test_no_promotion_for_short_procedure(self):
        props = {"steps": _steps(STEP_FLOW_THRESHOLD - 1)}
        component, _ = promote_component("step_player", props, None, {}, "tech")
        assert component == "step_player"


# ─── flash_deck -> concept_canvas ───


class TestPromoteFlashDeck:
    def test_promotes_when_concepts_connected(self):
        data = _concepts(3, connections=CONCEPT_CONNECTION_MIN)
        component, out = promote_component("flash_deck", {"cards": []}, data, {}, "learning")
        assert component == "concept_canvas"
        assert "concepts" in out

    def test_no_promotion_without_enough_connections(self):
        data = _concepts(3, connections=CONCEPT_CONNECTION_MIN - 1)
        component, _ = promote_component("flash_deck", {"cards": []}, data, {}, "learning")
        assert component == "flash_deck"

    def test_no_promotion_outside_concept_domains(self):
        data = _concepts(3, connections=CONCEPT_CONNECTION_MIN)
        component, _ = promote_component("flash_deck", {"cards": []}, data, {}, "food")
        assert component == "flash_deck"

    def test_falls_back_to_extraction_concepts(self):
        extraction = {"science": {"concepts": _concepts(3, connections=CONCEPT_CONNECTION_MIN)}}
        component, _ = promote_component("flash_deck", {"cards": []}, None, extraction, "science")
        assert component == "concept_canvas"


# ─── info_grid -> spot_explorer ───


class TestPromoteInfoGrid:
    def test_promotes_description_heavy_grid(self):
        long_value = "x" * 150
        props = {
            "items": [
                {"key": "First", "value": long_value},
                {"key": "Second", "value": long_value},
            ]
        }
        component, out = promote_component("info_grid", props, None, {}, "review")
        assert component == "spot_explorer"
        assert out["spots"][0]["name"] == "First"
        assert out["spots"][0]["description"] == long_value

    def test_no_promotion_for_short_pairs(self):
        props = {
            "items": [
                {"key": "Display", "value": "6.7 inch"},
                {"key": "Battery", "value": "5000mAh"},
            ]
        }
        component, _ = promote_component("info_grid", props, None, {}, "review")
        assert component == "info_grid"

    def test_no_promotion_with_single_long_value(self):
        props = {
            "items": [
                {"key": "Long", "value": "x" * 150},
                {"key": "Short", "value": "tiny"},
            ]
        }
        component, _ = promote_component("info_grid", props, None, {}, "review")
        assert component == "info_grid"


def test_unknown_component_passes_through():
    props = {"items": [1, 2, 3]}
    component, out = promote_component("moment_track", props, None, {}, "learning")
    assert component == "moment_track"
    assert out is props


class TestDemoteLadder:
    """Degrade-never-drop: failing rich assemblers walk down to simpler ones."""

    def test_string_commands_no_longer_need_demotion(self):
        # Regression target: tech.setup.commands is list[str]; step_player now
        # coerces strings directly (7a) so the ladder is a backstop, not the fix.
        from src.services.pipeline.assembly.assemblers_primary import assemble_step_player

        props = assemble_step_player({}, ["docker build .", "docker run app"], {}, None)

        assert props is not None
        assert [s["instruction"] for s in props["steps"]] == ["docker build .", "docker run app"]

    def test_demote_reaches_display_section_terminal(self):
        from src.services.pipeline.assembly.promotion import demote_component

        # A dict payload no rich list assembler can use — terminal rung catches it.
        result = demote_component("code_playground", {}, {"prose": "just text"}, {}, None)

        assert result is not None
        component, props = result
        assert component == "display_section"
        assert props is not None

    def test_tier_list_demotes_to_spot_explorer(self):
        from src.services.pipeline.assembly.promotion import demote_component

        # Two items — below tier_list's min of 3; the picks ship as cards (info_grid
        # never stands alone, p2 review A3).
        data = [
            {"item": "Card A", "reason": "chase pull"},
            {"item": "Card B", "reason": "playable"},
        ]
        result = demote_component("tier_list", {}, data, {}, None)

        assert result is not None
        component, props = result
        assert component == "spot_explorer"
        assert len(props["spots"]) == 2

    def test_quiz_arena_never_demotes(self):
        from src.services.pipeline.assembly.promotion import demote_component

        result = demote_component(
            "quiz_arena", {}, [{"question": "", "options": ["A", "B"]}], {}, None
        )

        assert result is None
