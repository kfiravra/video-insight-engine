"""Tests for the golden-dataset schema, per-video assertions and trace metrics.

Covers ``scripts/_eval_schema.py`` (incl. the committed ``videos.yaml``
against the live component registry and classifier formats),
``scripts/_eval_assertions.py`` and the trace readers in
``scripts/_eval_metrics.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.services.pipeline.classifier import VALID_FORMATS

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from _eval_assertions import TraceSignals, evaluate_assertions  # noqa: E402
from _eval_metrics import (  # noqa: E402
    duplicate_item_rate,
    extract_classifier_format,
    extract_faithfulness,
)
from _eval_schema import GoldenDataset, is_allowed_video_url, parse_assertions  # noqa: E402

_DATASET = _REPO_ROOT / "dev" / "golden-dataset" / "videos.yaml"
_REGISTRY = _REPO_ROOT / "packages" / "shared" / "src" / "config" / "domains.json"
_RETIRED = {"verdict", "quiz", "code_explorer", "gallery", "lyrics_player", "exercise_tracker"}


def _record(assertions: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    base = {
        "id": "vid",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "domain": "food",
        "format": "tutorial",
        "language": "en",
        "expectedTabs": [],
        "requiredComponents": [],
        "keyContent": [],
        "assertions": assertions,
    }
    base.update(overrides)
    return base


def _check(assertion: dict[str, Any], actual: dict[str, Any], signals: TraceSignals | None = None):
    [result] = evaluate_assertions(
        parse_assertions(_record([assertion])), actual, signals or TraceSignals()
    )
    return result


def _tabs(*components: str, props: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tabs": [{"id": c, "component": c, "props": props or {}} for c in components]}


# ─── Committed dataset ─────────────────────────────────────────────────
@pytest.fixture(scope="module")
def dataset() -> GoldenDataset:
    return GoldenDataset.model_validate(yaml.safe_load(_DATASET.read_text(encoding="utf-8")))


def _component_names(video: Any) -> set[str]:
    names = set(video.required_components) | set(video.forbidden_components)
    for assertion in video.assertions:
        for entry in getattr(assertion, "components", []):
            names.update([entry] if isinstance(entry, str) else entry)
        if getattr(assertion, "component", None):
            names.add(assertion.component)
    return names


class TestCommittedDataset:
    def test_should_validate_when_loading_the_committed_golden_set(
        self, dataset: GoldenDataset
    ) -> None:
        assert len(dataset.videos) == 28

    def test_should_only_name_registry_components(self, dataset: GoldenDataset) -> None:
        registry = set(json.loads(_REGISTRY.read_text(encoding="utf-8"))["components"])
        unknown = {v.id: _component_names(v) - registry for v in dataset.videos}
        assert {vid: names for vid, names in unknown.items() if names} == {}

    def test_should_never_name_retired_components(self, dataset: GoldenDataset) -> None:
        assert not any(_component_names(v) & _RETIRED for v in dataset.videos)

    def test_should_only_use_classifier_formats(self, dataset: GoldenDataset) -> None:
        formats = {v.format for v in dataset.videos}
        for v in dataset.videos:
            formats.update(*(a.values for a in v.assertions if a.type == "expectedFormat"))
        assert formats <= VALID_FORMATS

    def test_should_carry_assertions_on_the_four_pipeline_1min_anchors(
        self, dataset: GoldenDataset
    ) -> None:
        by_url = {v.url.rsplit("=", 1)[-1]: v for v in dataset.videos}
        anchors = ("v8KaQr0MhjE", "wCkLNqy5OHE", "uC45_4nnEAI", "Jru5B044HOs")
        assert all(by_url[yid].assertions and not by_url[yid].disabled for yid in anchors)

    def test_should_flag_exactly_one_quick_entry_per_live_domain(
        self, dataset: GoldenDataset
    ) -> None:
        live_domains = sorted({v.domain for v in dataset.videos if not v.disabled})
        quick_domains = sorted(v.domain for v in dataset.videos if v.quick)
        assert quick_domains == live_domains


# ─── Schema rules ──────────────────────────────────────────────────────
class TestSchema:
    def test_should_reject_an_unknown_assertion_type(self) -> None:
        with pytest.raises(ValidationError):
            parse_assertions(_record([{"type": "vibes"}]))

    def test_should_reject_a_minitems_without_min(self) -> None:
        with pytest.raises(ValidationError):
            parse_assertions(_record([{"type": "minItems", "component": "step_player"}]))

    def test_should_reject_an_unknown_assertion_key(self) -> None:
        bad = {"type": "quizAbsentOrLast", "component": "quiz_arena"}
        with pytest.raises(ValidationError):
            parse_assertions(_record([bad]))

    def test_should_reject_a_live_entry_with_a_todo(self) -> None:
        with pytest.raises(ValidationError, match="must be disabled"):
            parse_assertions(_record([], todo="pick an id"))

    def test_should_reject_a_quick_entry_when_it_is_disabled(self) -> None:
        with pytest.raises(ValidationError, match="must be live"):
            parse_assertions(_record([], quick=True, disabled=True))

    def test_should_reject_two_quick_entries_when_they_share_a_domain(self) -> None:
        videos = [_record([], id="a", quick=True), _record([], id="b", quick=True)]
        with pytest.raises(ValidationError, match="exactly one quick"):
            GoldenDataset.model_validate({"videos": videos})

    def test_should_reject_quick_flags_when_a_live_domain_has_none(self) -> None:
        videos = [_record([], id="a", quick=True), _record([], id="b", domain="tech")]
        with pytest.raises(ValidationError, match="'tech' needs exactly one quick"):
            GoldenDataset.model_validate({"videos": videos})

    def test_should_reject_duplicate_ids(self) -> None:
        with pytest.raises(ValidationError, match="duplicate golden id"):
            GoldenDataset.model_validate({"videos": [_record([]), _record([])]})

    @pytest.mark.parametrize(
        ("url", "allowed"),
        [
            ("https://www.youtube.com/watch?v=abc", True),
            ("https://youtu.be/abc", True),
            ("http://m.youtube.com/watch?v=abc", True),
            ("https://internal.example.com/admin", False),
            ("http://localhost:8080/x", False),
            ("file:///etc/passwd", False),
            ("javascript:alert(1)", False),
            ("", False),
            ("not a url", False),
        ],
    )
    def test_should_allow_only_youtube_urls(self, url: str, allowed: bool) -> None:
        assert is_allowed_video_url(url) is allowed


# ─── Assertion evaluators ──────────────────────────────────────────────
class TestAssertions:
    def test_should_fail_forbidden_when_component_present(self) -> None:
        result = _check(
            {"type": "forbiddenComponents", "components": ["step_player"]}, _tabs("step_player")
        )
        assert result.passed is False

    def test_should_accept_any_alternative_when_required_entry_is_a_list(self) -> None:
        check = {"type": "requiredComponents", "components": [["step_player", "step_flow_canvas"]]}
        assert _check(check, _tabs("overview", "step_flow_canvas")).passed is True

    def test_should_pass_quiz_check_when_quiz_is_last(self) -> None:
        assert _check({"type": "quizAbsentOrLast"}, _tabs("overview", "quiz_arena")).passed is True

    def test_should_fail_quiz_check_when_quiz_is_not_last(self) -> None:
        assert _check({"type": "quizAbsentOrLast"}, _tabs("quiz_arena", "overview")).passed is False

    def test_should_fail_min_items_when_list_is_short(self) -> None:
        check = {"type": "minItems", "component": "step_player", "min": 4, "field": "steps"}
        actual = _tabs("step_player", props={"steps": [{"instruction": "a"}]})
        assert _check(check, actual).passed is False

    def test_should_pass_moment_images_check_when_one_item_has_a_thumbnail(self) -> None:
        check = {
            "type": "minItemsWithField",
            "component": "moment_track",
            "fields": ["thumbnailUrl", "s3Key"],
            "min": 1,
        }
        items = [{"label": "pull", "seconds": 5, "thumbnailUrl": "https://x/f.jpg"}, {"label": "b"}]
        assert _check(check, _tabs("moment_track", props={"items": items})).passed is True

    def test_should_fail_timestamp_check_when_a_moment_is_past_the_end(self) -> None:
        actual = {**_tabs("moment_track", props={"items": [{"seconds": 400}]}), "duration": 300}
        assert _check({"type": "noTimestampBeyondDuration"}, actual).passed is False

    def test_should_read_clock_strings_when_checking_timestamps(self) -> None:
        actual = {**_tabs("overview", props={"items": [{"time": "5:10"}]}), "duration": 300}
        assert _check({"type": "noTimestampBeyondDuration"}, actual).passed is False

    def test_should_skip_timestamp_check_when_duration_is_unknown(self) -> None:
        assert _check({"type": "noTimestampBeyondDuration"}, _tabs("overview")).passed is None

    def test_should_skip_format_check_when_trace_has_no_classifier(self) -> None:
        check = {"type": "expectedFormat", "values": ["vlog"]}
        assert _check(check, _tabs("overview")).passed is None

    def test_should_pass_format_check_when_classifier_format_matches(self) -> None:
        check = {"type": "expectedFormat", "values": ["vlog"]}
        signals = TraceSignals(classifier_format="vlog")
        assert _check(check, _tabs("overview"), signals).passed is True

    def test_should_carry_xfail_reason_without_gating(self) -> None:
        check = {"type": "forbiddenComponents", "components": ["checklist"], "xfail": "C19"}
        assert _check(check, _tabs("checklist")).gating_failure is False


# ─── Metrics ───────────────────────────────────────────────────────────
class TestMetrics:
    def test_should_count_near_duplicates_within_a_list(self) -> None:
        items = [{"text": "fold the dough three times"}, {"text": "fold the dough three times!"}]
        tabs = [{"component": "step_player", "props": {"steps": items}}]
        assert duplicate_item_rate(tabs) == 0.5

    def test_should_exempt_overview_from_the_cross_tab_check(self) -> None:
        item = {"text": "fold the dough three times"}
        tabs = [
            {"component": "step_player", "props": {"steps": [item]}},
            {"component": "overview", "props": {"items": [item]}},
        ]
        assert duplicate_item_rate(tabs) == 0.0

    def test_should_return_none_when_no_item_is_long_enough(self) -> None:
        tabs = [{"component": "checklist", "props": {"items": [{"label": "Salt"}]}}]
        assert duplicate_item_rate(tabs) is None

    def test_should_read_latest_faithfulness_score_from_trace(self) -> None:
        trace = {
            "scores": [
                {"name": "faithfulness", "value": 0.5, "timestamp": "2026-10-07T10:00:00Z"},
                {"name": "faithfulness", "value": 0.8, "timestamp": "2026-10-07T11:00:00Z"},
            ]
        }
        assert extract_faithfulness(trace) == 0.8

    def test_should_parse_classifier_format_from_generation_output(self) -> None:
        trace = {
            "observations": [{"name": "classifier", "output": '```json\n{"format": "Vlog"}\n```'}]
        }
        assert extract_classifier_format(trace) == "vlog"
