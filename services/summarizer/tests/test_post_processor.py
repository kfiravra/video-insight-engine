"""Tests for post_processor utilities."""

import pytest

from src.services.pipeline.post_processor import (
    drop_empty_tabs,
    assign_section_accents,
    merge_narrative,
    resolve_celebrations,
    compute_extraction_coverage,
    COVERAGE_GATE_RATIO,
)


class TestDropEmptyTabs:
    """Test drop_empty_tabs function."""

    def test_keeps_tabs_with_enough_items(self):
        tabs = [
            {"id": "itinerary", "label": "Itinerary", "dataSource": "travel.itinerary"},
        ]
        data = {
            "travel": {
                "itinerary": [{"day": 1}, {"day": 2}, {"day": 3}],
            },
        }

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 1

    def test_drops_tabs_with_less_than_two_items(self):
        tabs = [
            {"id": "itinerary", "label": "Itinerary", "dataSource": "travel.itinerary"},
            {"id": "budget", "label": "Budget", "dataSource": "travel.budget"},
        ]
        data = {
            "travel": {
                "itinerary": [{"day": 1}],  # Only 1 item
                "budget": {"total": 1000},  # Not a list
            },
        }

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 1
        assert result[0]["id"] == "budget"

    def test_keeps_tabs_with_no_data_source(self):
        tabs = [
            {"id": "overview", "label": "Overview"},
        ]
        data = {}

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 1

    def test_keeps_tabs_with_unresolved_path(self):
        tabs = [
            {"id": "test", "label": "Test", "dataSource": "nonexistent.path"},
        ]
        data = {}

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 1

    def test_keeps_tabs_with_enough_analysis_items(self):
        tabs = [
            {"id": "analysis", "label": "Analysis", "dataSource": "music.analysis"},
        ]
        data = {
            "music": {
                "analysis": [
                    {"aspect": "Production", "detail": "Great"},
                    {"aspect": "Vocals", "detail": "Powerful"},
                ],
            },
        }

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 1

    def test_empty_tabs_list(self):
        result = drop_empty_tabs([], {})
        assert result == []

    def test_drops_tab_with_empty_list(self):
        tabs = [
            {"id": "tips", "label": "Tips", "dataSource": "food.tips"},
        ]
        data = {
            "food": {
                "tips": [],
            },
        }

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 0

    def test_empty_data_source_string_keeps_tab(self):
        tabs = [
            {"id": "overview", "label": "Overview", "dataSource": ""},
        ]
        data = {}

        result = drop_empty_tabs(tabs, data)
        assert len(result) == 1


class TestAssignSectionAccents:
    """Test assign_section_accents function."""

    def test_assigns_cycling_indices(self):
        sections = [{"type": "day", "items": []} for _ in range(10)]

        result = assign_section_accents(sections)

        assert len(result) == 10
        assert result[0]["accentIndex"] == 0
        assert result[1]["accentIndex"] == 1
        assert result[6]["accentIndex"] == 6
        assert result[7]["accentIndex"] == 0  # Cycles back

    def test_does_not_mutate_originals(self):
        sections = [{"type": "day", "items": []}]

        result = assign_section_accents(sections)

        assert "accentIndex" not in sections[0]
        assert "accentIndex" in result[0]

    def test_empty_sections(self):
        result = assign_section_accents([])
        assert result == []

    def test_preserves_existing_fields(self):
        sections = [{"type": "step", "items": [{"label": "Step 1"}], "extra": "data"}]

        result = assign_section_accents(sections)

        assert result[0]["type"] == "step"
        assert result[0]["items"] == [{"label": "Step 1"}]
        assert result[0]["extra"] == "data"
        assert result[0]["accentIndex"] == 0


class TestMergeNarrative:
    """Test merge_narrative function."""

    def test_merges_narrative_data(self):
        data = {"food": {"ingredients": []}}
        narrative = {"keyMoments": [{"description": "A moment"}], "quotes": []}

        result = merge_narrative(data, narrative)

        assert "narrative" in result
        assert result["narrative"]["keyMoments"][0]["description"] == "A moment"
        assert result["food"] == {"ingredients": []}

    def test_returns_data_unchanged_when_none(self):
        data = {"food": {"ingredients": []}}

        result = merge_narrative(data, None)

        assert result == data
        assert "narrative" not in result

    def test_does_not_mutate_original(self):
        data = {"food": {"ingredients": []}}
        narrative = {"keyMoments": []}

        result = merge_narrative(data, narrative)

        assert "narrative" not in data
        assert "narrative" in result


class TestResolveCelebrations:
    """Test resolve_celebrations function."""

    def test_adds_celebration_to_quiz_tab(self):
        tabs = [
            {"id": "quizzes", "label": "Quiz"},
            {"id": "overview", "label": "Overview"},
        ]

        result = resolve_celebrations(tabs)

        assert result[0]["celebration"] is True
        assert result[1]["celebration"] is False

    def test_celebration_tab_ids(self):
        celebration_ids = [
            "quizzes",
            "flashcards",
            "scenarios",
            "packing",
            "ingredients",
            "materials",
            "tools",
        ]
        for tab_id in celebration_ids:
            tabs = [{"id": tab_id, "label": "Test"}]
            result = resolve_celebrations(tabs)
            assert result[0]["celebration"] is True, f"{tab_id} should trigger celebration"

    def test_non_celebration_tabs(self):
        non_celebration_ids = ["overview", "analysis", "budget", "itinerary", "tips"]
        for tab_id in non_celebration_ids:
            tabs = [{"id": tab_id, "label": "Test"}]
            result = resolve_celebrations(tabs)
            assert result[0]["celebration"] is False, f"{tab_id} should not trigger celebration"

    def test_does_not_mutate_originals(self):
        tabs = [{"id": "quizzes", "label": "Quiz"}]

        result = resolve_celebrations(tabs)

        assert "celebration" not in tabs[0]
        assert "celebration" in result[0]

    def test_empty_tabs(self):
        result = resolve_celebrations([])
        assert result == []


class TestComputeExtractionCoverage:
    def test_full_coverage_passes_gate(self):
        data = {"learning": {"timestamps": [{"seconds": 0}, {"seconds": 16000}]}}

        coverage = compute_extraction_coverage(data, 16789)

        assert coverage is not None
        assert coverage["maxTimestamp"] == 16000
        assert coverage["ratio"] >= COVERAGE_GATE_RATIO

    def test_truncated_coverage_trips_gate(self):
        # The regression: 4.5h video whose timeline stops at 1:34:56 (5696s).
        data = {
            "learning": {
                "timestamps": [
                    {"time": "0:00", "seconds": 0},
                    {"time": "1:34:56", "seconds": 5696},
                ]
            }
        }

        coverage = compute_extraction_coverage(data, 16789)

        assert coverage is not None
        assert coverage["ratio"] < COVERAGE_GATE_RATIO
        assert coverage["tailMissingSeconds"] == 16789 - 5696

    def test_parses_string_offsets_in_narrative(self):
        data = {
            "narrative": {
                "keyMoments": [
                    {"timestamp": "2:00"},
                    {"timestamp": "1:20:00"},
                ]
            }
        }

        coverage = compute_extraction_coverage(data, 5000)

        assert coverage is not None
        assert coverage["maxTimestamp"] == 4800  # 1:20:00

    def test_returns_none_without_timestamps_or_duration(self):
        assert compute_extraction_coverage({"learning": {}}, 1000) is None
        assert compute_extraction_coverage(None, 1000) is None
        assert (
            compute_extraction_coverage({"learning": {"timestamps": [{"seconds": 5}]}}, 0) is None
        )
