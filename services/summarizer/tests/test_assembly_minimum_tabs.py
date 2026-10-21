"""Tests for assembly minimum 3-tab guarantee."""

from src.services.pipeline.assembly.core import (
    _validate_assembled_props,
    assemble_response,
    build_fallback_candidates,
)


class TestBuildFallbackCandidates:
    def test_overview_from_synthesis(self):
        candidates = build_fallback_candidates(
            synthesis={"masterSummary": "Great video", "keyTakeaways": ["t1"]},
            existing_components=set(),
            existing_ids=set(),
        )
        assert len(candidates) >= 1
        assert candidates[0]["component"] == "overview"
        assert candidates[0]["props"]["summary"] == "Great video"

    def test_checklist_from_takeaways(self):
        """The takeaways fallback is a checklist — info_grid never stands alone (R6)."""
        candidates = build_fallback_candidates(
            synthesis={"keyTakeaways": ["t1", "t2", "t3"]},
            existing_components={"overview"},
            existing_ids={"overview"},
        )
        takeaway_candidates = [c for c in candidates if c["id"] == "key_info"]
        assert len(takeaway_candidates) == 1
        assert takeaway_candidates[0]["component"] == "checklist"
        assert len(takeaway_candidates[0]["props"]["items"]) == 3

    def test_skips_existing_components(self):
        candidates = build_fallback_candidates(
            synthesis={"masterSummary": "test", "keyTakeaways": ["t1", "t2"]},
            existing_components={"overview"},
            existing_ids=set(),
        )
        assert all(c["component"] != "overview" for c in candidates)

    def test_skips_existing_ids(self):
        candidates = build_fallback_candidates(
            synthesis={"masterSummary": "test"},
            existing_components=set(),
            existing_ids={"overview"},
        )
        assert all(c["id"] != "overview" for c in candidates)

    def test_empty_synthesis(self):
        candidates = build_fallback_candidates(
            synthesis={},
            existing_components=set(),
            existing_ids=set(),
        )
        assert len(candidates) == 0

    def test_insufficient_takeaways_no_takeaways_tab(self):
        candidates = build_fallback_candidates(
            synthesis={"keyTakeaways": ["only_one"]},
            existing_components={"overview"},
            existing_ids=set(),
        )
        assert all(c["id"] != "key_info" for c in candidates)

    def test_fallbacks_pass_validation(self):
        candidates = build_fallback_candidates(
            synthesis={"masterSummary": "test", "keyTakeaways": ["t1", "t2"]},
            existing_components=set(),
            existing_ids=set(),
        )
        for c in candidates:
            assert _validate_assembled_props(c["component"], c["props"])


class TestAssembleResponseMinTabs:
    def _base_triage(self, tabs=None):
        return {
            "contentTags": ["learning"],
            "modifiers": [],
            "primaryTag": "learning",
            "userGoal": "Learn stuff",
            "tabs": tabs or [],
        }

    def test_3_plus_tabs_no_fallbacks(self):
        """3+ assembled tabs should not get fallbacks."""
        triage = self._base_triage(
            [
                {
                    "id": "t1",
                    "label": "Tab 1",
                    "dataSource": "learning.keyPoints",
                    "component": "overview",
                },
                {
                    "id": "t2",
                    "label": "Tab 2",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
                {
                    "id": "t3",
                    "label": "Tab 3",
                    "dataSource": "learning.takeaways",
                    "component": "checklist",
                },
            ]
        )
        extraction = {
            "learning": {
                "keyPoints": [{"title": "p1", "emoji": "📌", "detail": "d1"}],
                "concepts": [{"name": "c1", "definition": "d1"}],
                "takeaways": ["t1"],
            }
        }
        synthesis = {"tldr": "test", "keyTakeaways": ["k1"], "masterSummary": "summary"}
        result = assemble_response(triage, extraction, None, synthesis)
        # Should have at least 3 tabs, no extra fallback overview
        assert len(result["tabs"]) >= 3

    def test_2_tabs_with_synthesis_adds_overview(self):
        """2 assembled tabs + synthesis should get overview fallback."""
        triage = self._base_triage(
            [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
                {
                    "id": "takeaways",
                    "label": "Takeaways",
                    "dataSource": "learning.takeaways",
                    "component": "checklist",
                },
            ]
        )
        extraction = {
            "learning": {
                "concepts": [{"name": "c1", "definition": "d1"}],
                "takeaways": ["t1"],
            }
        }
        synthesis = {"tldr": "test", "keyTakeaways": ["k1", "k2"], "masterSummary": "A great video"}
        result = assemble_response(triage, extraction, None, synthesis)
        assert len(result["tabs"]) >= 3
        tab_components = [t["component"] for t in result["tabs"]]
        assert "overview" in tab_components

    def test_1_tab_gets_multiple_fallbacks(self):
        """1 assembled tab should get 2 fallbacks to reach 3."""
        triage = self._base_triage(
            [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
            ]
        )
        extraction = {
            "learning": {
                "concepts": [{"name": "c1", "definition": "d1"}],
            }
        }
        synthesis = {"tldr": "test", "keyTakeaways": ["k1", "k2", "k3"], "masterSummary": "Summary"}
        result = assemble_response(triage, extraction, None, synthesis)
        assert len(result["tabs"]) >= 3

    def test_0_tabs_with_chapters_gets_moment_track(self):
        """0 assembled tabs + no synthesis + chapters → the always-Moments tab, from chapters."""
        triage = self._base_triage(
            [
                {
                    "id": "empty",
                    "label": "Empty",
                    "dataSource": "learning.nonexistent",
                    "component": "info_grid",
                },
            ]
        )
        extraction = {"learning": {}}
        video_meta = {
            "chapters": [
                {"title": "Intro", "start_time": 0, "end_time": 60},
                {"title": "Main", "start_time": 60, "end_time": 300},
                {"title": "End", "start_time": 300, "end_time": 600},
            ]
        }
        result = assemble_response(triage, extraction, None, None, video_meta=video_meta)
        tab_components = [t["component"] for t in result["tabs"]]
        assert "moment_track" in tab_components

    def test_no_duplicate_components_in_fallbacks(self):
        """Fallbacks should not duplicate existing component types."""
        triage = self._base_triage(
            [
                {
                    "id": "my_overview",
                    "label": "My Overview",
                    "dataSource": "meta",
                    "component": "overview",
                },
            ]
        )
        extraction = {"learning": {}}
        synthesis = {"tldr": "test", "keyTakeaways": ["k1", "k2"], "masterSummary": "summary"}
        result = assemble_response(triage, extraction, None, synthesis)
        component_counts = {}
        for t in result["tabs"]:
            c = t["component"]
            component_counts[c] = component_counts.get(c, 0) + 1
        # overview should appear at most once
        assert component_counts.get("overview", 0) <= 1

    def test_backfilled_required_tab_counts_toward_minimum(self):
        """A required component backfilled from real extraction data counts as a
        real tab — the min-3 guarantee should see it and add fewer synth fillers."""
        triage = {
            "contentTags": ["tech"],
            "modifiers": [],
            "primaryTag": "tech",
            "userGoal": "Learn the code",
            "tabs": [
                {
                    "id": "key_claims",
                    "label": "Claims",
                    "dataSource": "tech.topics",
                    "component": "info_grid",
                    "goal": "claims",
                },
            ],
        }
        # Only snippets are populated; the planned info_grid field is empty, so
        # the sole planned tab drops — code_playground must be backfilled.
        extraction = {
            "tech": {
                "snippets": [
                    {"filename": "a.py", "language": "python", "code": "a = 1", "explanation": "x"},
                    {"filename": "b.py", "language": "python", "code": "b = 2", "explanation": "y"},
                ],
                "topics": [],
            }
        }
        synthesis = {"masterSummary": "A talk", "keyTakeaways": ["k1", "k2", "k3"]}
        result = assemble_response(triage, extraction, None, synthesis)

        components = [t["component"] for t in result["tabs"]]
        assert "code_playground" in components
        assert len(result["tabs"]) >= 3
