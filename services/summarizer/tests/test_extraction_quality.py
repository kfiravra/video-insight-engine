"""Tests for extraction quality check and retry prompt builder."""

import pytest

from src.utils.data_helpers import is_empty_data
from src.services.pipeline.extraction_quality import (
    ExtractionQuality,
    HARD_MISS_SCORE_GATE,
    build_synthesis_fed_retry_prompt,
    check_extraction_quality,
    decide_extraction_retry,
    merge_retry_fields,
    _is_narrative_content,
    _resolve_dot_path,
)


class TestIsEmptyData:
    def test_none_is_empty(self):
        assert is_empty_data(None) is True

    def test_empty_list_is_empty(self):
        assert is_empty_data([]) is True

    def test_empty_dict_is_empty(self):
        assert is_empty_data({}) is True

    def test_nonempty_list_not_empty(self):
        assert is_empty_data([1]) is False

    def test_nonempty_dict_not_empty(self):
        assert is_empty_data({"key": "val"}) is False

    def test_string_not_empty(self):
        assert is_empty_data("hello") is False

    def test_zero_not_empty(self):
        assert is_empty_data(0) is False


class TestResolveDotPath:
    def test_simple_path(self):
        data = {"travel": {"itinerary": [{"day": 1}]}}
        assert _resolve_dot_path(data, "travel.itinerary") == [{"day": 1}]

    def test_single_key(self):
        data = {"fitness": {"exercises": []}}
        assert _resolve_dot_path(data, "fitness") == {"exercises": []}

    def test_missing_path(self):
        data = {"travel": {}}
        assert _resolve_dot_path(data, "travel.budget") is None

    def test_wildcard(self):
        data = {"food": {"ingredients": ["salt"]}}
        assert _resolve_dot_path(data, "food.*") == {"ingredients": ["salt"]}

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": "deep"}}}
        assert _resolve_dot_path(data, "a.b.c") == "deep"


class TestCheckExtractionQuality:
    def test_all_populated_score_1(self):
        tabs = [
            {"dataSource": "learning.keyPoints"},
            {"dataSource": "learning.concepts"},
            {"dataSource": "learning.takeaways"},
        ]
        extraction = {
            "learning": {
                "keyPoints": [{"title": "point1"}],
                "concepts": [{"name": "concept1"}],
                "takeaways": ["takeaway1"],
            }
        }
        result = check_extraction_quality(tabs, extraction)
        assert result.score == 1.0
        assert result.populated == 3
        assert result.total == 3
        assert result.empty_fields == []

    def test_some_empty_correct_score(self):
        tabs = [
            {"dataSource": "learning.keyPoints"},
            {"dataSource": "learning.concepts"},
            {"dataSource": "learning.takeaways"},
        ]
        extraction = {
            "learning": {
                "keyPoints": [{"title": "point1"}],
                "concepts": [],
                "takeaways": [],
            }
        }
        result = check_extraction_quality(tabs, extraction)
        assert result.score == pytest.approx(1 / 3)
        assert result.populated == 1
        assert result.total == 3
        assert "learning.concepts" in result.empty_fields
        assert "learning.takeaways" in result.empty_fields

    def test_all_empty_score_0(self):
        tabs = [
            {"dataSource": "learning.keyPoints"},
            {"dataSource": "learning.concepts"},
        ]
        extraction = {"learning": {"keyPoints": [], "concepts": []}}
        result = check_extraction_quality(tabs, extraction)
        assert result.score == 0.0
        assert result.populated == 0
        assert result.total == 2

    def test_skips_meta_sources(self):
        tabs = [
            {"dataSource": "meta"},
            {"dataSource": "learning.keyPoints"},
        ]
        extraction = {"learning": {"keyPoints": [{"title": "point"}]}}
        result = check_extraction_quality(tabs, extraction)
        assert result.total == 1
        assert result.populated == 1
        assert result.score == 1.0

    def test_skips_synthesis_sources(self):
        tabs = [
            {"dataSource": "synthesis.tldr"},
            {"dataSource": "learning.keyPoints"},
        ]
        extraction = {"learning": {"keyPoints": []}}
        result = check_extraction_quality(tabs, extraction)
        assert result.total == 1
        assert result.populated == 0

    def test_skips_enrichment_sources(self):
        tabs = [
            {"dataSource": "enrichment.quiz"},
            {"dataSource": "enrichment.flashcards"},
            {"dataSource": "learning.concepts"},
        ]
        extraction = {"learning": {"concepts": [{"name": "c1"}]}}
        result = check_extraction_quality(tabs, extraction)
        assert result.total == 1
        assert result.populated == 1

    def test_none_extraction_data(self):
        tabs = [{"dataSource": "learning.keyPoints"}]
        result = check_extraction_quality(tabs, {})
        assert result.score == 0.0
        assert result.total == 0

    def test_empty_tabs(self):
        result = check_extraction_quality([], {"learning": {"keyPoints": []}})
        assert result.score == 0.0
        assert result.total == 0

    def test_missing_dotted_path(self):
        tabs = [{"dataSource": "travel.budget.breakdown"}]
        extraction = {"travel": {"budget": {}}}
        result = check_extraction_quality(tabs, extraction)
        assert result.total == 1
        assert result.populated == 0
        assert "travel.budget.breakdown" in result.empty_fields

    def test_tabs_without_datasource_skipped(self):
        tabs = [
            {"dataSource": ""},
            {"id": "no_ds"},
            {"dataSource": "learning.keyPoints"},
        ]
        extraction = {"learning": {"keyPoints": [{"title": "p"}]}}
        result = check_extraction_quality(tabs, extraction)
        assert result.total == 1
        assert result.populated == 1


class TestBuildSynthesisFedRetryPrompt:
    def test_includes_empty_fields(self):
        prompt = build_synthesis_fed_retry_prompt(
            ["learning.concepts", "learning.takeaways"],
            {"tldr": "Test video", "keyTakeaways": ["t1", "t2"], "masterSummary": "Summary"},
        )
        assert "learning.concepts" in prompt
        assert "learning.takeaways" in prompt

    def test_includes_synthesis_evidence(self):
        prompt = build_synthesis_fed_retry_prompt(
            ["learning.concepts"],
            {
                "tldr": "Great video about Python",
                "keyTakeaways": ["Learn decorators"],
                "masterSummary": "A deep dive",
            },
        )
        assert "Great video about Python" in prompt
        assert "Learn decorators" in prompt
        assert "A deep dive" in prompt

    def test_warns_against_fabrication(self):
        prompt = build_synthesis_fed_retry_prompt(
            ["learning.concepts"],
            {"tldr": "test"},
        )
        assert "DO NOT fabricate" in prompt
        assert "DO NOT hallucinate" in prompt

    def test_wraps_evidence_in_tags(self):
        prompt = build_synthesis_fed_retry_prompt(
            ["learning.concepts"],
            {"tldr": "test"},
        )
        assert "<synthesis_evidence>" in prompt
        assert "</synthesis_evidence>" in prompt

    def test_handles_empty_synthesis(self):
        prompt = build_synthesis_fed_retry_prompt(
            ["learning.concepts"],
            {},
        )
        assert "learning.concepts" in prompt
        assert "RETRY INSTRUCTION" in prompt

    def test_truncates_master_summary(self):
        long_summary = "x" * 1000
        prompt = build_synthesis_fed_retry_prompt(
            ["field"],
            {"masterSummary": long_summary},
        )
        # Should only include first 500 chars
        assert "x" * 500 in prompt
        assert "x" * 501 not in prompt


class TestDecideExtractionRetry:
    def test_low_score_triggers_retry(self):
        quality = ExtractionQuality(score=0.3, populated=1, total=3, empty_fields=["a", "b"])
        decision = decide_extraction_retry(quality, count_warnings={})
        assert decision.should_retry is True
        assert "low score" in decision.reason
        assert decision.hard_miss_fields == []

    def test_high_score_no_misses_no_retry(self):
        quality = ExtractionQuality(score=0.9, populated=9, total=10, empty_fields=[])
        decision = decide_extraction_retry(quality, count_warnings={})
        assert decision.should_retry is False
        assert decision.reason == ""

    def test_hard_miss_below_gate_triggers_retry(self):
        """Regression: plan said 6 tips, extraction got 0, score in the gate band.

        Score = 0.65 sits between the low-score threshold (0.6) and the
        hard-miss gate (0.7). The retry must fire despite the score being
        above 0.6, because a completely missed section is its own signal.
        """
        quality = ExtractionQuality(
            score=0.65,
            populated=4,
            total=6,
            empty_fields=["food.tips"],
        )
        count_warnings = {
            "tips": {"manifest": 6, "extracted": 0, "ratio": 0.0},
        }
        decision = decide_extraction_retry(quality, count_warnings)
        assert decision.should_retry is True
        assert "hard miss" in decision.reason
        assert "tips" in decision.reason
        assert decision.hard_miss_fields == ["tips"]

    def test_high_score_above_gate_suppresses_hard_miss_retry(self):
        """A hard miss alone does NOT trigger retry above HARD_MISS_SCORE_GATE.

        At score >= 0.7 the residual gap is most likely a single mis-planned
        field; the ~$0.20 retry rarely recovers data. This guards the cost
        gate that was the primary motivation for the format-aware fix.
        """
        quality = ExtractionQuality(
            score=5 / 6,
            populated=5,
            total=6,
            empty_fields=["food.tips"],
        )
        count_warnings = {
            "tips": {"manifest": 6, "extracted": 0, "ratio": 0.0},
        }
        decision = decide_extraction_retry(quality, count_warnings)
        assert decision.should_retry is False
        assert quality.score >= HARD_MISS_SCORE_GATE

    def test_partial_count_warning_no_retry(self):
        """A partial undercount (extracted > 0 but below 60%) is not a hard miss."""
        quality = ExtractionQuality(score=0.9, populated=9, total=10, empty_fields=[])
        count_warnings = {
            "tips": {"manifest": 6, "extracted": 3, "ratio": 0.5},
        }
        decision = decide_extraction_retry(quality, count_warnings)
        assert decision.should_retry is False
        assert decision.hard_miss_fields == []

    def test_zero_total_no_retry_even_with_zero_score(self):
        quality = ExtractionQuality(score=0.0, populated=0, total=0, empty_fields=[])
        decision = decide_extraction_retry(quality, count_warnings={})
        assert decision.should_retry is False

    def test_multiple_hard_misses(self):
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=["food.tips"])
        count_warnings = {
            "tips": {"manifest": 6, "extracted": 0, "ratio": 0.0},
            "ingredients": {"manifest": 8, "extracted": 0, "ratio": 0.0},
        }
        decision = decide_extraction_retry(quality, count_warnings)
        assert decision.should_retry is True
        assert set(decision.hard_miss_fields) == {"tips", "ingredients"}

    def test_combines_reasons_when_both_conditions_met(self):
        """When both triggers fire, the reason string preserves BOTH signals
        so the retry log captures the full context (not just the first match).
        """
        quality = ExtractionQuality(score=0.3, populated=1, total=4, empty_fields=["a"])
        count_warnings = {"tips": {"manifest": 6, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(quality, count_warnings)
        assert decision.should_retry is True
        assert "low score" in decision.reason
        assert "hard miss" in decision.reason
        assert "tips" in decision.reason
        assert decision.hard_miss_fields == ["tips"]

    def test_skips_hard_miss_for_field_not_in_active_schemas(self):
        """Regression for the dropped-tab bug on tech tutorials.

        For a `tech + learning` video, neither schema defines `steps[]` or
        `tips[]`. The plan stage may still predict counts for these fields,
        but a retry can't possibly populate them — it would just waste 3+
        minutes. The `content_tags` argument lets the decision filter out
        such impossible fields.
        """
        quality = ExtractionQuality(
            score=0.8,
            populated=4,
            total=5,
            empty_fields=[],
        )
        count_warnings = {
            "steps": {"manifest": 8, "extracted": 0, "ratio": 0.0},
            "tips": {"manifest": 6, "extracted": 0, "ratio": 0.0},
        }
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_tags=["tech", "learning"],
        )
        assert decision.should_retry is False
        assert decision.hard_miss_fields == []

    def test_keeps_hard_miss_when_field_is_in_active_schema(self):
        """If at least one active contentTag schema owns the field, retry."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=["food.tips"])
        count_warnings = {"tips": {"manifest": 6, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_tags=["food"],
        )
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["tips"]

    def test_partial_schema_match_keeps_only_relevant_field(self):
        """A mixed set: keep fields owned by active domains, drop the rest."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {
            "steps": {
                "manifest": 8,
                "extracted": 0,
                "ratio": 0.0,
            },  # food/project — kept (food active)
            "spots": {"manifest": 5, "extracted": 0, "ratio": 0.0},  # travel only — dropped
        }
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_tags=["food", "learning"],
        )
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["steps"]

    def test_unknown_field_in_count_warnings_is_kept(self):
        """A field not in the schema map (no domain mapping) should be kept —
        we don't have evidence it's unfulfillable."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {"unknown_field": {"manifest": 3, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_tags=["tech"],
        )
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["unknown_field"]

    def test_omitting_content_tags_preserves_legacy_behavior(self):
        """When the caller passes no content_tags, the schema gating is
        skipped entirely so we don't break callers that haven't migrated."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {"steps": {"manifest": 8, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(quality, count_warnings)  # no content_tags
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["steps"]


class TestFormatAwareRetryGate:
    """Tests for the format-aware filter and the hard-miss score gate."""

    def test_narrative_format_drops_step_like_hard_miss(self):
        """A vlog with a hard miss on 'steps' should NOT retry — vlogs don't
        produce step structure, retrying wastes ~$0.20 with zero recoverable
        data."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {"steps": {"manifest": 6, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_format="vlog",
        )
        assert decision.should_retry is False
        assert decision.hard_miss_fields == []

    def test_tutorial_format_keeps_step_like_hard_miss(self):
        """A tutorial with a hard miss on 'steps' SHOULD retry — tutorials
        legitimately have step structure, and a missed section is real."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {"steps": {"manifest": 6, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_format="tutorial",
        )
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["steps"]

    def test_low_score_with_non_step_hard_miss_retries(self):
        """A low-score retry fires regardless of format gating —
        a low overall score is its own systemic signal."""
        quality = ExtractionQuality(score=0.3, populated=1, total=4, empty_fields=["concepts"])
        count_warnings = {"concepts": {"manifest": 4, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_format="vlog",
        )
        assert decision.should_retry is True
        assert "low score" in decision.reason

    def test_narrative_keeps_non_step_hard_miss(self):
        """Narrative gate only filters STEP_LIKE_FIELDS — other hard misses
        are kept and still trigger the gated retry."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {"concepts": {"manifest": 5, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_format="vlog",
        )
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["concepts"]

    def test_narrative_with_mixed_hard_misses_filters_only_step_like(self):
        """Mixed step + non-step hard misses on a narrative video: drop
        only the step-like ones; the rest still gate the retry."""
        quality = ExtractionQuality(score=0.65, populated=4, total=6, empty_fields=[])
        count_warnings = {
            "steps": {"manifest": 6, "extracted": 0, "ratio": 0.0},
            "concepts": {"manifest": 5, "extracted": 0, "ratio": 0.0},
        }
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_format="podcast",
        )
        assert decision.should_retry is True
        assert decision.hard_miss_fields == ["concepts"]

    def test_high_score_with_narrative_step_miss_no_retry(self):
        """Both filters apply at once: narrative format drops step-like, and
        the score gate would have suppressed it anyway. End result: no retry."""
        quality = ExtractionQuality(score=0.85, populated=5, total=6, empty_fields=[])
        count_warnings = {"itinerary": {"manifest": 6, "extracted": 0, "ratio": 0.0}}
        decision = decide_extraction_retry(
            quality,
            count_warnings,
            content_format="commentary",
        )
        assert decision.should_retry is False

    def test_is_narrative_content_helper_format(self):
        """Direct check of the helper used by decide_extraction_retry."""
        assert _is_narrative_content("vlog") is True
        assert _is_narrative_content("podcast") is True
        assert _is_narrative_content("tutorial") is False
        assert _is_narrative_content(None) is False


class TestMergeRetryFields:
    def test_passes_through_empty_fields_unchanged(self):
        result = merge_retry_fields(
            empty_fields=["food.tips", "food.steps"],
            hard_miss_fields=[],
            count_warnings={},
        )
        assert result == ["food.tips", "food.steps"]

    def test_annotates_hard_miss_with_planned_count(self):
        result = merge_retry_fields(
            empty_fields=[],
            hard_miss_fields=["tips"],
            count_warnings={"tips": {"manifest": 6, "extracted": 0, "ratio": 0.0}},
        )
        assert len(result) == 1
        assert "tips" in result[0]
        assert "6" in result[0]
        assert "extraction returned 0" in result[0]

    def test_annotates_matching_dot_path_in_place(self):
        """When empty_fields contains a dot-path whose final segment matches
        a hard-miss field, the annotation is applied in-place rather than
        listing the same schema field twice in the retry prompt."""
        result = merge_retry_fields(
            empty_fields=["food.tips"],
            hard_miss_fields=["tips"],
            count_warnings={"tips": {"manifest": 6, "extracted": 0}},
        )
        assert len(result) == 1
        assert result[0].startswith("food.tips")
        assert "plan expected 6" in result[0]
        assert "extraction returned 0" in result[0]

    def test_appends_standalone_when_no_matching_path(self):
        """A hard-miss field with no matching dot-path is appended as a new
        annotated entry, preserving the count-validation signal."""
        result = merge_retry_fields(
            empty_fields=["food.steps"],
            hard_miss_fields=["tips"],
            count_warnings={"tips": {"manifest": 6, "extracted": 0}},
        )
        assert "food.steps" in result
        assert any(r.startswith("tips ") and "6" in r for r in result)
        assert len(result) == 2

    def test_missing_warning_metadata_falls_back_to_zero(self):
        result = merge_retry_fields(
            empty_fields=[],
            hard_miss_fields=["tips"],
            count_warnings={},
        )
        assert len(result) == 1
        assert "plan expected 0" in result[0]
