"""Unit tests for the model A/B scorers (``scripts/_model_ab_scores*.py``, pipeline-1min 4.0d).

The answers are the T1dQhQAm8Tc cassette's recorded ones (today's models),
scored against the requests the capture records from its replay (split mode),
plus small hand-made answers for the failure paths. Every scorer reads an
answer with the pipeline's own parser; an answer compared with itself agrees
fully. No network, no LLM, no Mongo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.replay.cassette import LLMEntry, load_cassette
from tests.replay.group_synth import recorded_answer, slice_answer

_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from _model_ab_capture import capture_cassettes, group_from_json  # noqa: E402
from _model_ab_report import score_rows, summarize  # noqa: E402
from _model_ab_scores import compare_to_a, score_answer  # noqa: E402
from _model_ab_scores_items import reads_card  # noqa: E402
from _model_ab_types import AbRequest, CallResult  # noqa: E402

VIDEO = "T1dQhQAm8Tc"


@pytest.fixture(scope="module")
def captured() -> dict[str, AbRequest]:
    return {r.request_id: r for r in capture_cassettes((VIDEO,))}


@pytest.fixture(scope="module")
def entries() -> dict[str, LLMEntry]:
    return {e.key.span or e.key.feature: e for e in load_cassette(VIDEO).llm}


def _request(captured: dict[str, AbRequest], call_type: str) -> AbRequest:
    return next(r for r in captured.values() if r.call_type == call_type)


def _self_compare(call_type: str, raw: str, context: dict[str, Any]) -> dict[str, Any]:
    score = score_answer(call_type, raw, context)
    return compare_to_a(call_type, score, score)


class TestRecordedAnswers:
    def test_should_parse_the_recorded_plan_when_scored_with_its_capture_context(
        self, captured: dict[str, AbRequest], entries: dict[str, LLMEntry]
    ) -> None:
        score = score_answer("plan", entries["plan"].output, _request(captured, "plan").context)
        assert (score["parsed"], score["fallback"], score["tabs"] > 0) == (True, False, True)

    def test_should_agree_fully_when_a_plan_is_compared_with_itself(
        self, captured: dict[str, AbRequest], entries: dict[str, LLMEntry]
    ) -> None:
        context = _request(captured, "plan").context
        compared = _self_compare("plan", entries["plan"].output, context)
        assert compared["vsA_tabs_jaccard"] == 1.0

    def test_should_fall_back_when_the_plan_confidence_is_low(
        self, captured: dict[str, AbRequest], entries: dict[str, LLMEntry]
    ) -> None:
        data = recorded_answer(entries["plan"]) | {"confidence": 0.1}
        score = score_answer("plan", json.dumps(data), _request(captured, "plan").context)
        assert score["fallback"] is True

    def test_should_keep_a_full_outline_when_the_recorded_memory_is_scored(
        self, captured: dict[str, AbRequest], entries: dict[str, LLMEntry]
    ) -> None:
        context = _request(captured, "memory").context
        score = score_answer("memory", entries["memory"].output, context)
        assert (score["parsed"], score["outline_valid"]) == (True, True)

    def test_should_count_usable_questions_when_the_recorded_quiz_is_scored(
        self, entries: dict[str, LLMEntry]
    ) -> None:
        score = score_answer("quiz", entries["enrichment"].output, {})
        assert score["usable"] is True

    def test_should_agree_fully_when_a_description_analysis_is_compared_with_itself(
        self, entries: dict[str, LLMEntry]
    ) -> None:
        compared = _self_compare("description", entries["description_analysis"].output, {})
        assert compared == {"vsA_fields_agree": 1.0}

    def test_should_read_the_recorded_probe_when_scored(self, entries: dict[str, LLMEntry]) -> None:
        assert score_answer("tier_probe", entries["tier_probe"].output, {})["parsed"] is True

    def test_should_fill_requested_keys_when_each_group_gets_its_slice_of_the_recording(
        self, captured: dict[str, AbRequest], entries: dict[str, LLMEntry]
    ) -> None:
        recorded = recorded_answer(entries["extraction"])
        filled = []
        for request in (r for r in captured.values() if r.call_type == "extraction"):
            group = group_from_json(request.context["group"])
            keys = [(f.domain, f.field, f.kind) for f in group.fields]
            raw = json.dumps(slice_answer(recorded, keys))
            filled.append(score_answer("extraction", raw, request.context)["filled"])
        assert all(count > 0 for count in filled)


class TestFailurePaths:
    def test_should_count_every_golden_field_as_missed_when_the_probe_is_unparseable(
        self,
    ) -> None:
        labels = {"domain": "tech", "format": "tutorial", "has_visual_demo": True}
        score = score_answer("tier_probe", "no json here", {"labels": labels})
        assert [score[f"golden_{k}"] for k in labels] == [False, False, False]

    def test_should_match_the_golden_labels_when_the_probe_answers_them(self) -> None:
        raw = '{"domain": "tech", "format": "tutorial", "has_visual_demo": true, "confidence": 0.9}'
        labels = {"domain": "tech", "format": "tutorial", "has_visual_demo": False}
        score = score_answer("tier_probe", raw, {"labels": labels})
        assert (score["golden_domain"], score["golden_has_visual_demo"]) == (True, False)

    def test_should_mark_an_extraction_unparsed_when_the_answer_is_not_json(
        self, captured: dict[str, AbRequest]
    ) -> None:
        context = _request(captured, "extraction").context
        assert score_answer("extraction", "sorry, no", context) == {"parsed": False}

    def test_should_disagree_when_the_judges_differ(self) -> None:
        ours = score_answer("faithfulness", '{"grounded": false}', {})
        theirs = score_answer("faithfulness", '{"grounded": true}', {})
        assert compare_to_a("faithfulness", ours, theirs) == {"vsA_agree": False}

    def test_should_count_strings_left_in_english_when_the_hebrew_output_echoes_one(
        self,
    ) -> None:
        context = {"originals": ["Hello world", "Second line"], "target": "he"}
        raw = json.dumps({"translations": ["שלום עולם", "Second line"]})
        assert score_answer("translation_output", raw, context)["left_share"] == 0.5

    def test_should_count_blocks_left_in_hebrew_when_the_rag_answer_keeps_one(self) -> None:
        context = {"originals": ["שלום עולם", "בוקר טוב"], "target": "en"}
        raw = json.dumps({"translations": ["Hello world", "בוקר טוב"]})
        assert score_answer("translation_rag", raw, context)["left_in_source"] == 1

    def test_should_reject_a_translation_when_the_answer_has_the_wrong_length(self) -> None:
        context = {"originals": ["a", "b"], "target": "en"}
        assert score_answer("translation_rag", '["only one"]', context) == {"parsed": False}


class TestVision:
    _METADATA = [
        {"timestamp_sec": 1.0, "s3_url": "a", "original_index": 5},
        {"timestamp_sec": 2.0, "s3_url": "b", "original_index": 6},
    ]
    _CONTEXT = {"metadata": _METADATA, "cards": {"5": ["Atmos", "Whitebeard Pirates"]}}

    def _raw(self, second_scene: str) -> str:
        return json.dumps(
            [
                {
                    "frame_index": 0,
                    "scene_type": "card_reveal",
                    "content": "Atmos card",
                    "text_visible": "Atmos, Whitebeard Pirates",
                    "educational_value": "card",
                },
                {
                    "frame_index": 1,
                    "scene_type": second_scene,
                    "content": "host talks",
                    "visual_subject": "presenter" if second_scene == "talking_head" else "hands",
                    "educational_value": "",
                },
            ]
        )

    def test_should_read_the_card_when_its_name_and_crew_are_described(self) -> None:
        score = score_answer("vision", self._raw("talking_head"), self._CONTEXT)
        assert score["cards_read"] == 1

    def test_should_drop_presenter_filler_when_selecting_frames(self) -> None:
        score = score_answer("vision", self._raw("talking_head"), self._CONTEXT)
        assert score["kept"] == [5]

    def test_should_halve_the_selection_overlap_when_one_arm_keeps_one_more_frame(self) -> None:
        ours = score_answer("vision", self._raw("talking_head"), self._CONTEXT)
        theirs = score_answer("vision", self._raw("demonstration"), self._CONTEXT)
        assert compare_to_a("vision", ours, theirs)["vsA_selection_jaccard"] == 0.5

    def test_should_match_a_card_token_when_punctuation_differs(self) -> None:
        description = {"content": "Gum Gum Kong Gun event", "text_visible": "ELBAPH"}
        assert reads_card(description, ["Gum-Gum Kong Gun", "Elbaph"]) is True


class TestReportRows:
    def _result(self, arm: str, repeat: int, raw: str, **extra: Any) -> CallResult:
        return CallResult(
            "r1", "faithfulness", arm, f"m-{arm}", "low", repeat, 100, raw=raw, **extra
        )

    def test_should_compare_with_arm_a_and_with_the_first_run(self) -> None:
        info = {"r1": {"context": {}, "pipeline_timeout_s": 20.0}}
        results = [
            self._result("A", 0, '{"grounded": true}'),
            self._result("B", 0, '{"grounded": false}'),
            self._result("B", 1, '{"grounded": true}'),
        ]
        rows = {(r["arm"], r["repeat"]): r for r in score_rows(info, results)}
        assert (rows[("B", 0)]["vsA"], rows[("B", 1)]["noise"]) == (
            {"vsA_agree": False},
            {"run_agree": False},
        )

    def test_should_count_refusals_without_scoring_them(self) -> None:
        info = {"r1": {"context": {}, "pipeline_timeout_s": 20.0}}
        results = [self._result("B", 0, "", refusal=True, finish_reason="content_filter")]
        summary = summarize(score_rows(info, results))["faithfulness"]["B"]
        assert (summary["refusals"], summary["metrics"]) == (1, {})


class TestPlanRequirements:
    _CONTEXT = {"duration": 1127, "category_hint": None, "content_format": "tutorial"}

    def _recorded_plan(self) -> dict[str, Any]:
        entry = next(e for e in load_cassette("jMq8lEu-of0").llm if e.key.span == "plan")
        return recorded_answer(entry)

    def test_should_meet_step_player_when_the_plan_picks_its_step_flow_canvas_equivalent(
        self,
    ) -> None:
        data = self._recorded_plan()
        swapped = [t for t in data["tabs"] if t.get("component") == "step_player"]
        for tab in swapped:
            tab["component"] = "step_flow_canvas"
        score = score_answer("plan", json.dumps(data), self._CONTEXT)
        assert (len(swapped), score["required_present"]) == (1, True)

    def test_should_count_a_duplicate_source_when_two_tabs_read_the_same_data(self) -> None:
        data = self._recorded_plan()
        before = score_answer("plan", json.dumps(data), self._CONTEXT)["duplicate_sources"]
        copy = next(t for t in data["tabs"] if t.get("component") == "info_grid")
        data["tabs"].append({**copy, "id": "tips_again", "label": "More tips"})
        after = score_answer("plan", json.dumps(data), self._CONTEXT)["duplicate_sources"]
        assert after == before + 1
