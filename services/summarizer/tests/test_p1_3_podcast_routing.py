"""p1.3 — podcast routing (L9, L11, L25, C10).

A podcast's Moments tab is its own segment map (``podcast.segments``, each
segment saying what was concluded, with its line): plan-done reconcile repoints
a Moments tab planned on learning's generic ``learning.timestamps`` to the
primary's own moment source. ``podcast.qa`` (new) ships an interview's questions
and answers as moments with ``speaker`` + ``question``. ``podcast.quotes`` /
``narrative.quotes`` also render as spot_explorer quote cards. ``tech.topics``
are ``{name, description, timestamp}`` objects. A key idea may name its holder
(``speaker``, a podcast's positions).
"""

from __future__ import annotations

import pytest

from src.models.domain_types import (
    PodcastSegment,
    TechData,
    validate_domain_output_with_drops,
)
from src.models.idea_types import PodcastQA
from src.models.pipeline_types import PlanResult
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_primary import assemble_spot_explorer
from src.services.pipeline.assembly.moment_normalizers import normalize_moment_items
from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.reconcile import reconcile_at_plan_done
from src.services.pipeline.schema_fields import slice_skeleton
from src.shared_config.domain_config import data_source

_QUESTION = "Why did you rebuild every HEY app as a native app instead of keeping the web wrapper?"


def _tab(tab_id: str, component: str, source: str) -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}


def _plan(primary: str, tabs: list[dict], tags: list[str]) -> PlanResult:
    return PlanResult.model_validate(
        {
            "contentTags": tags,
            "primaryTag": primary,
            "confidence": 0.9,
            "evidence": {},
            "tabs": tabs,
        }
    )


def _source(outcome_tabs: list[dict], tab_id: str) -> str:
    return next(t["dataSource"] for t in outcome_tabs if t["id"] == tab_id)


# ─── Registry + schema ───


class TestRegistry:
    def test_should_register_podcast_qa_as_a_moment_source(self) -> None:
        spec = data_source("podcast.qa")

        assert spec is not None and (spec["components"], spec["cap"]) == (["moment_track"], 15)

    @pytest.mark.parametrize("path", ["podcast.quotes", "narrative.quotes"])
    def test_should_let_quotes_render_as_spot_cards(self, path: str) -> None:
        spec = data_source(path)

        assert spec is not None and "spot_explorer" in spec["components"]

    def test_should_render_tech_topics_as_spot_cards_first(self) -> None:
        spec = data_source("tech.topics")

        assert spec is not None and spec["components"][0] == "spot_explorer"


class TestSchemas:
    def test_should_ask_every_qa_item_for_question_answer_speaker_and_timestamp(self) -> None:
        item = slice_skeleton("podcast", ["qa"])["qa"][0]

        assert {"question", "answer", "speaker", "timestamp", "quote"} <= set(item)

    def test_should_ask_every_segment_for_its_quote(self) -> None:
        item = slice_skeleton("podcast", ["segments"])["segments"][0]

        assert {"summary", "quote"} <= set(item)

    def test_should_ask_every_tech_topic_for_description_and_timestamp(self) -> None:
        item = slice_skeleton("tech", ["topics"])["topics"][0]

        assert set(item) == {"name", "description", "timestamp"}

    def test_should_ask_every_travel_spot_for_timestamp_and_quote(self) -> None:
        spot = slice_skeleton("travel", ["itinerary"])["itinerary"][0]["spots"][0]

        assert {"timestamp", "quote"} <= set(spot)


# ─── Models ───


class TestModels:
    def test_should_keep_a_qa_answer_when_the_model_names_it_description(self) -> None:
        qa = PodcastQA.model_validate({"question": "Q?", "description": "A.", "timestamp": "61"})

        assert (qa.answer, qa.timestamp) == ("A.", 61)

    def test_should_drop_a_qa_item_when_it_has_no_question(self) -> None:
        raw = {"podcast": {"qa": [{"answer": "orphan"}, {"question": "Q?", "answer": "A."}]}}

        data, _ = validate_domain_output_with_drops(["podcast"], [], raw)

        assert [qa["question"] for qa in data["podcast"]["qa"]] == ["Q?"]

    def test_should_keep_a_segment_quote_when_given(self) -> None:
        segment = PodcastSegment.model_validate({"title": "T", "quote": " Pencils down. "})

        assert segment.quote == "Pencils down."

    def test_should_read_a_bare_string_topic_as_its_name(self) -> None:
        topics = TechData.model_validate({"topics": ["useState"]}).model_dump()["topics"]

        assert topics == [{"name": "useState", "description": "", "timestamp": None}]

    def test_should_keep_a_topic_object_with_its_stamp(self) -> None:
        raw = {
            "topics": [{"topic": "useEffect", "description": "Runs after render.", "timestamp": 95}]
        }

        topic = TechData.model_validate(raw).model_dump()["topics"][0]

        assert topic == {"name": "useEffect", "description": "Runs after render.", "timestamp": 95}


# ─── Assembly ───


class TestQaMoments:
    def test_should_head_the_row_with_the_question_and_carry_it_whole(self) -> None:
        raw = {
            "question": _QUESTION,
            "answer": "The wrapper could not do offline.",
            "speaker": "DHH",
            "timestamp": 1500,
        }

        item = normalize_moment_items([raw])[0]

        assert item["label"] == _QUESTION
        assert (item["question"], item["description"], item["speaker"]) == (
            _QUESTION,
            "The wrapper could not do offline.",
            "DHH",
        )

    def test_should_title_a_long_question_by_its_first_sentence_without_an_ellipsis(
        self,
    ) -> None:
        question = (
            "With the $8 million you just got, how is it being used? What's your vision for "
            "making Linux on the desktop happen, and where's that money going?"
        )

        item = normalize_moment_items([{"question": question, "answer": "a", "timestamp": 9}])[0]

        assert item["label"] == "With the $8 million you just got, how is it being used?"

    def test_should_cut_a_long_single_sentence_question_at_a_word(self) -> None:
        question = "Why " + "really " * 30 + "now?"

        label = normalize_moment_items([{"question": question, "answer": "a", "timestamp": 9}])[0][
            "label"
        ]

        assert len(label) <= 140 and label.endswith("…") and " really" in label

    def test_should_ask_for_every_question_of_the_whole_conversation(self) -> None:
        podcast = load_schema_text("podcast")

        assert "the last 10 minutes included" in podcast and "8-15" in podcast

    def test_should_ship_an_interview_qa_tab_through_assembly(self) -> None:
        qa = [
            {
                "question": f"Question {i}?",
                "answer": f"Answer {i} said in full.",
                "speaker": "G",
                "timestamp": 300 * i,
                "quote": None,
            }
            for i in range(1, 5)
        ]
        triage = {
            "primaryTag": "podcast",
            "contentTags": ["podcast"],
            "tabs": [_tab("qa", "moment_track", "podcast.qa")],
        }

        result = assemble_response(
            triage, {"podcast": {"qa": qa}}, None, None, video_meta={"duration": 2532}
        )
        items = next(t for t in result["tabs"] if t["id"] == "qa")["props"]["items"]

        assert [i["description"] for i in items] == [q["answer"] for q in qa]


class TestQuoteCards:
    def test_should_name_a_podcast_quote_card_by_its_speaker(self) -> None:
        quotes = [
            {"quote": "Start with goals, not risks.", "speaker": "Max Tegmark", "timestamp": 4801},
            {
                "quote": "Intelligence is substrate-independent.",
                "speaker": "Max Tegmark",
                "timestamp": 610,
            },
        ]

        props = assemble_spot_explorer({}, quotes, {}, None)

        assert props is not None and props["spots"][0] == {
            "name": "Max Tegmark",
            "description": "",
            "quote": "Start with goals, not risks.",
            "timestamp": 4801,
        }

    def test_should_describe_a_narrative_quote_card_by_its_context(self) -> None:
        quotes = [
            {
                "text": "We had eight hundred dollars.",
                "speaker": "Alex",
                "timestamp": 345,
                "context": "The lowest point",
            },
            {"text": "It sold out.", "speaker": "Alex", "timestamp": 720, "context": None},
        ]

        props = assemble_spot_explorer({}, quotes, {}, None)

        assert props is not None and props["spots"][0]["description"] == "The lowest point"

    def test_should_name_an_unattributed_quote_by_its_own_head(self) -> None:
        quotes = [
            {"quote": "Pencils down.", "speaker": None, "timestamp": 1262},
            {"quote": "Total optimism.", "speaker": None, "timestamp": 3600},
        ]

        props = assemble_spot_explorer({}, quotes, {}, None)

        assert props is not None and props["spots"][0] == {
            "name": "Pencils down.",
            "description": "",
            "timestamp": 1262,
        }


class TestTopicCards:
    def test_should_ship_topic_objects_as_spot_cards_with_their_stamps(self) -> None:
        topics = TechData.model_validate(
            {
                "topics": [
                    {"name": "useState", "description": "Holds state.", "timestamp": 40},
                    {"name": "useRef", "description": "Holds a mutable box.", "timestamp": 400},
                ]
            }
        ).model_dump()["topics"]

        props = assemble_spot_explorer({}, topics, {}, None)

        assert props is not None and [s["timestamp"] for s in props["spots"]] == [40, 400]


class TestKeyIdeaSpeaker:
    def test_should_attribute_a_position_card_when_the_idea_names_its_holder(self) -> None:
        ideas = [
            {"claim": "Start with goals", "why": "w", "speaker": "Tegmark"},
            {"claim": "Intelligence is substrate-independent", "why": "w", "speaker": None},
        ]

        props = assemble_spot_explorer({}, ideas, {}, None)

        assert props is not None
        assert [s.get("speaker") for s in props["spots"]] == ["Tegmark", None]


# ─── Plan-done: the primary's own moment source ───


class TestPrimaryMomentSource:
    def test_should_repoint_a_podcast_moments_tab_to_its_segments(self) -> None:
        plan = _plan(
            "podcast",
            [
                _tab("map", "moment_track", "learning.timestamps"),
                _tab("ideas", "spot_explorer", "learning.keyIdeas"),
            ],
            ["podcast", "learning"],
        )

        outcome = reconcile_at_plan_done(plan, None, "podcast")

        assert _source(outcome.tabs, "map") == "podcast.segments"
        assert any(r["reason"] == "primary_moment_source" for r in outcome.records)

    def test_should_repoint_a_news_moments_tab_to_its_story_timeline(self) -> None:
        plan = _plan(
            "news", [_tab("t", "moment_track", "learning.timestamps")], ["news", "learning"]
        )

        outcome = reconcile_at_plan_done(plan, None, None)

        assert _source(outcome.tabs, "t") == "news.storyTimeline"

    def test_should_keep_the_learning_list_when_the_segments_already_have_a_tab(self) -> None:
        tabs = [
            _tab("map", "moment_track", "podcast.segments"),
            _tab("chapters", "moment_track", "learning.timestamps"),
        ]

        outcome = reconcile_at_plan_done(
            _plan("podcast", tabs, ["podcast", "learning"]), None, None
        )

        assert not any(r["reason"] == "primary_moment_source" for r in outcome.records)

    @pytest.mark.parametrize("primary", ["learning", "tech"])
    def test_should_keep_the_learning_list_when_the_primary_has_no_moment_source(
        self, primary: str
    ) -> None:
        tabs = [_tab("m", "moment_track", "learning.timestamps")]

        outcome = reconcile_at_plan_done(_plan(primary, tabs, [primary, "learning"]), None, None)

        assert _source(outcome.tabs, "m") == "learning.timestamps"
