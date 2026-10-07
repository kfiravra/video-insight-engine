"""Tests for `resolve_cross_tab_links` — the cross-tab navigation resolver.

Verifies that a link's text is the target tab's own label (the plan no longer
writes per-link CTA text — pipeline-1min 1b.2). The rules themselves are
purely structural — no hardcoded English label strings.
"""

from __future__ import annotations

from src.services.pipeline.assembly.cross_tab import resolve_cross_tab_links


class TestResolveCrossTabLinks:
    def test_should_use_target_tab_label_when_linking(self):
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "concepts", "label": "5 Concepts", "component": "flash_deck"},
        ]
        links = resolve_cross_tab_links(
            tab_id="overview",
            all_tab_ids={"overview", "concepts"},
            component="overview",
            all_tabs=all_tabs,
            primary_tag="learning",
        )
        assert {"targetTab": "concepts", "label": "5 Concepts"} in links

    def test_should_keep_non_english_target_label_when_linking(self):
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "concepts", "label": "5 מושגים", "component": "flash_deck"},
        ]
        links = resolve_cross_tab_links(
            tab_id="overview",
            all_tab_ids={"overview", "concepts"},
            component="overview",
            all_tabs=all_tabs,
            primary_tag="learning",
        )
        target = next(link for link in links if link["targetTab"] == "concepts")
        assert target["label"] == "5 מושגים"

    def test_should_fall_back_to_target_id_when_target_has_no_label(self):
        links = resolve_cross_tab_links("ingredients", {"ingredients", "steps"})
        assert {"targetTab": "steps", "label": "steps"} in links

    def test_rule_logic_still_fires_without_label(self):
        """Rules decide WHICH links to render; the label only changes the
        text. A food video should still get overview→checklist regardless
        of whether the Plan provided a label."""
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "ingredients", "label": "12 Ingredients", "component": "checklist"},
        ]
        links = resolve_cross_tab_links(
            tab_id="overview",
            all_tab_ids={"overview", "ingredients"},
            component="overview",
            all_tabs=all_tabs,
            primary_tag="food",
        )
        assert any(l["targetTab"] == "ingredients" for l in links)

    def test_does_not_link_to_self(self):
        all_tabs = [
            {"id": "overview", "component": "overview"},
        ]
        links = resolve_cross_tab_links(
            tab_id="overview",
            all_tab_ids={"overview"},
            component="overview",
            all_tabs=all_tabs,
            primary_tag="learning",
        )
        assert all(l["targetTab"] != "overview" for l in links)

    def test_no_label_string_is_hardcoded_english_in_rules(self):
        """Sanity: the rule tuples should not contain English label strings.
        This test pins the structural-only contract on the rule data."""
        import src.services.pipeline.assembly.cross_tab as mod

        for rule in mod._COMPONENT_LINK_RULES:
            assert len(rule) == 3, f"Expected 3-tuple (src, tgt, domain), got {rule}"
        for rule in mod._LEGACY_LINK_RULES:
            assert len(rule) == 2, f"Expected 2-tuple (src, tgt), got {rule}"

    def test_no_retired_component_names_in_rules(self):
        """1D: rules must not reference components retired in the overhaul."""
        import src.services.pipeline.assembly.cross_tab as mod

        retired = {
            "verdict",
            "code_explorer",
            "quiz",
            "exercise_tracker",
            "lyrics_player",
            "gallery",
        }
        names = {r[0] for r in mod._COMPONENT_LINK_RULES} | {
            r[1] for r in mod._COMPONENT_LINK_RULES
        }
        assert not (names & retired), f"Retired names still in rules: {names & retired}"

    def test_promoted_concept_canvas_links_like_flash_deck(self):
        """A flash_deck promoted to concept_canvas (1A) still links to the quiz."""
        all_tabs = [
            {"id": "concepts", "component": "concept_canvas"},
            {"id": "quiz", "label": "Test Yourself", "component": "quiz_arena"},
        ]
        links = resolve_cross_tab_links(
            tab_id="concepts",
            all_tab_ids={"concepts", "quiz"},
            component="concept_canvas",
            all_tabs=all_tabs,
            primary_tag="learning",
        )
        assert any(l["targetTab"] == "quiz" for l in links)
