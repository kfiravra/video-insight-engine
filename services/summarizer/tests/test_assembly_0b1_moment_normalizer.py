"""0b.1 moment normalizer: no "Moment N", no textless item, no duplicate seconds, description.

R5 / R7 (output-quality proposal §1): a moment is prose with a time. The
normalizer refuses an item that has neither label nor description instead of
naming it by position (S06 shipped "Moment 1…16" from food.steps whose only
prose is `instruction`; S11 shipped ten itinerary days at 0:00), carries the
prose any source holds under its own name (PodcastSegment.summary,
FoodStep.instruction, a chapter summary), and keeps one timed item per second:
an item that states no time cannot be seeked, so it is refused.
"""

from __future__ import annotations

import pytest

from src.models.domain_types import LearningTimestamp, PodcastSegment
from src.services.pipeline.assembly import assemble_moment_track, assemble_response
from src.services.pipeline.assembly.normalizers import (
    _chapters_to_moments,
    _normalize_moment_item,
    normalize_moment_items,
)

_INSTRUCTION = (
    "Season the beef and pork liberally with salt and combine the meats. Form the meat into balls."
)
_FOOD_STEPS = [
    {"number": 1, "title": None, "instruction": _INSTRUCTION, "timestamp": 101},
    {
        "number": 2,
        "title": None,
        "instruction": "Brown the meatballs in two batches.",
        "timestamp": 161,
    },
]
_ITINERARY = [
    {"day": 1, "city": "Hanoi", "theme": "Old Quarter", "spots": [{"name": "Old Quarter"}]},
    {"day": 2, "city": "Ha Long", "theme": "Bay cruise", "spots": [{"name": "Ha Long Bay"}]},
]
_OUTLINE = [
    {"start": 0, "end": 300, "title": "Arrival in Hanoi"},
    {"start": 300, "end": 900, "title": "Ha Long Bay cruise"},
]


def _labels(items: list[dict] | None) -> list[str]:
    return [item["label"] for item in items or []]


class TestNeverMomentN:
    def test_should_refuse_an_item_that_only_has_a_time(self) -> None:
        assert _normalize_moment_item({"seconds": 60}, 2) is None

    def test_should_refuse_a_placeholder_label_without_prose(self) -> None:
        assert _normalize_moment_item({"label": "Moment 3", "seconds": 60}, 0) is None

    def test_should_keep_a_lowercase_moment_label_as_the_source_own_words(self) -> None:
        result = _normalize_moment_item({"label": "moment 1", "seconds": 60}, 0)

        assert result is not None and result["label"] == "moment 1"

    def test_should_replace_a_placeholder_label_with_the_description(self) -> None:
        result = _normalize_moment_item(
            {"label": "Moment 3", "description": "Searing the meatballs", "seconds": 60}, 0
        )

        assert result is not None and result["label"] == "Searing the meatballs"

    def test_should_ship_no_placeholder_for_food_steps(self) -> None:
        result = assemble_moment_track({}, _FOOD_STEPS, {}, None)

        assert result is not None
        assert not any(label.startswith("Moment ") for label in _labels(result["items"]))


class TestRefuseTextless:
    @pytest.mark.parametrize(
        "item",
        [
            {"day": 1, "city": "Hanoi", "spots": []},
            {"label": "   ", "title": "", "name": None, "seconds": 5},
            {"label": None, "description": "", "timestamp": 30},
            "   ",
            42,
            None,
        ],
    )
    def test_should_return_none_for_an_item_without_prose(self, item: object) -> None:
        assert _normalize_moment_item(item, 0) is None

    def test_should_return_none_for_a_track_whose_items_all_lack_prose(self) -> None:
        assert assemble_moment_track({}, _ITINERARY, {}, None) is None

    def test_should_keep_an_item_whose_only_prose_is_a_string(self) -> None:
        assert _normalize_moment_item("Introduction", 0) == {
            "label": "Introduction",
            "time": "0:00",
            "seconds": 0,
        }

    def test_should_skip_a_chapter_without_a_title(self) -> None:
        chapters = [{"start_time": 0, "title": ""}, {"start_time": 100, "title": "Demo"}]

        assert _labels(_chapters_to_moments(chapters, 400)) == ["Demo"]


class TestCarryDescription:
    def test_should_carry_a_podcast_segment_summary_as_description(self) -> None:
        result = _normalize_moment_item(
            {
                "title": "Why consensus is hard",
                "summary": "Lena breaks down CAP.",
                "timestamp": 480,
            },
            0,
        )

        assert result is not None and result["description"] == "Lena breaks down CAP."

    def test_should_carry_a_food_step_instruction_as_description(self) -> None:
        result = _normalize_moment_item(_FOOD_STEPS[0], 0)

        assert result is not None and result["description"] == _INSTRUCTION

    def test_should_label_a_step_from_the_head_of_its_instruction(self) -> None:
        result = _normalize_moment_item(_FOOD_STEPS[0], 0)

        assert result is not None
        assert result["label"] == "Season the beef and pork liberally with salt and combine…"

    def test_should_carry_a_chapter_summary_as_description(self) -> None:
        chapters = [{"start_time": 0, "title": "Intro", "summary": "The route in one minute."}]

        assert _chapters_to_moments(chapters, 400)[0]["description"] == "The route in one minute."

    def test_should_keep_an_explicit_label_over_the_description(self) -> None:
        result = _normalize_moment_item(
            {"label": "Meatballs", "instruction": "Brown them in batches.", "seconds": 10}, 0
        )

        assert result is not None and result["label"] == "Meatballs"

    def test_should_not_repeat_the_label_as_description(self) -> None:
        result = _normalize_moment_item({"description": "Short note", "seconds": 10}, 0)

        assert result is not None and "description" not in result

    def test_should_strip_whitespace_from_the_carried_description(self) -> None:
        result = _normalize_moment_item(
            {"label": "X", "description": "  padded  ", "seconds": 1}, 0
        )

        assert result is not None and result["description"] == "padded"


class TestRefuseDuplicateSeconds:
    def test_should_keep_the_first_item_per_second(self) -> None:
        items = normalize_moment_items(
            [
                {"label": "First", "seconds": 60},
                {"label": "Second", "seconds": 60},
                {"label": "Third", "seconds": 120},
            ]
        )

        assert _labels(items) == ["First", "Third"]

    def test_should_return_an_empty_list_for_a_non_list(self) -> None:
        assert normalize_moment_items({"label": "A"}) == []

    def test_should_keep_one_chapter_per_start(self) -> None:
        chapters = [
            {"start_time": 0, "title": "A"},
            {"start_time": 0, "title": "A again"},
            {"start_time": 200, "title": "B"},
        ]

        assert _labels(_chapters_to_moments(chapters, 400)) == ["A", "B"]

    def test_should_end_a_chapter_at_the_next_later_start_past_a_duplicate(self) -> None:
        chapters = [
            {"start_time": 0, "title": "A"},
            {"start_time": 0, "title": "A again"},
            {"start_time": 200, "title": "B"},
        ]

        assert _chapters_to_moments(chapters, 400)[0]["endSeconds"] == 199


class TestRefuseTimeless:
    """A moment is a seek point: an item that states no time is refused, never parked at 0:00."""

    def test_should_refuse_an_item_that_states_no_time(self) -> None:
        items = normalize_moment_items([{"label": "A"}, {"label": "C", "seconds": 9}])

        assert _labels(items) == ["C"]

    def test_should_keep_the_real_opening_after_a_timeless_item(self) -> None:
        items = normalize_moment_items([{"label": "Timeless"}, {"label": "Opening", "seconds": 0}])

        assert _labels(items) == ["Opening"]

    def test_should_refuse_a_bare_string_from_the_track(self) -> None:
        assert normalize_moment_items(["Introduction"]) == []

    def test_should_refuse_an_unparseable_time(self) -> None:
        assert normalize_moment_items([{"label": "X", "seconds": "abc"}]) == []

    def test_should_ship_no_track_from_an_all_timeless_list(self) -> None:
        assert assemble_moment_track({}, [{"label": "A"}, {"label": "B"}], {}, None) is None


class TestClockBeatsADefaultedZero:
    """Phase-0 review W-B1: ``LearningTimestamp`` turns a missing ``seconds`` into 0, so
    the ``time`` clock decides — on validated data, as the extraction hands it over."""

    @staticmethod
    def _validated(*raw: dict) -> list[dict]:
        # A learning moment states what was established (p1.2): the clock rules are
        # about its start, so each fixture carries one.
        described = ({"description": "What it established.", **item} for item in raw)
        return [LearningTimestamp.model_validate(item).model_dump() for item in described]

    def test_should_ship_the_clocked_item_and_the_real_opening(self) -> None:
        data = self._validated(
            {"time": "2:10", "seconds": None, "label": "Setup"},
            {"time": "0:00", "seconds": 0, "label": "Intro"},
        )

        items = normalize_moment_items(data)

        assert [(i["label"], i["seconds"]) for i in items] == [("Setup", 130), ("Intro", 0)]

    def test_should_seek_where_the_chip_says(self) -> None:
        data = self._validated({"time": "2:10", "seconds": None, "label": "Setup"})

        item = normalize_moment_items(data)[0]

        assert (item["seconds"], item["time"]) == (130, "2:10")

    def test_should_derive_the_chip_from_stated_seconds_over_a_disagreeing_clock(self) -> None:
        item = _normalize_moment_item({"time": "5:00", "seconds": 130, "label": "Setup"}, 0)

        assert item is not None and (item["seconds"], item["time"]) == (130, "2:10")

    def test_should_refuse_a_negative_seconds_without_a_clock(self) -> None:
        data = self._validated({"time": "", "seconds": -5, "label": "Sentinel"})

        assert normalize_moment_items(data) == []

    def test_should_place_a_negative_seconds_by_its_clock(self) -> None:
        data = self._validated({"time": "3:00", "seconds": -1, "label": "Late"})

        assert normalize_moment_items(data)[0]["seconds"] == 180

    def test_should_keep_a_zero_without_a_clock_as_the_opening(self) -> None:
        data = self._validated({"seconds": 0, "label": "Opening"})

        assert normalize_moment_items(data)[0]["seconds"] == 0

    def test_should_let_a_positive_sibling_stamp_beat_a_zero_seconds(self) -> None:
        """Close-out review W4: a 0 ``seconds`` is no stamp beside a ``startSeconds``."""
        item = _normalize_moment_item({"seconds": 0, "startSeconds": 95, "label": "Setup"}, 0)

        assert item is not None and item["seconds"] == 95

    @pytest.mark.parametrize("bad", [float("inf"), -float("inf"), float("nan")])
    def test_should_place_a_non_finite_seconds_by_its_clock(self, bad: float) -> None:
        """Close-out review W3: Infinity raised OverflowError out of ``int()``."""
        item = _normalize_moment_item({"seconds": bad, "time": "2:10", "label": "Setup"}, 0)

        assert item is not None and item["seconds"] == 130

    @pytest.mark.parametrize("bad", [float("inf"), float("nan")])
    def test_should_refuse_a_non_finite_seconds_without_a_clock(self, bad: float) -> None:
        assert normalize_moment_items([{"seconds": bad, "label": "Setup"}]) == []

    def test_should_ship_both_through_the_moment_track_assembler(self) -> None:
        data = self._validated(
            {"time": "2:10", "seconds": None, "label": "Setup"},
            {"time": "0:00", "seconds": 0, "label": "Intro"},
        )

        result = assemble_moment_track({}, data, {}, None)

        assert result is not None and _labels(result["items"]) == ["Setup", "Intro"]


class TestChapterOrder:
    def test_should_read_unsorted_chapters_in_time_order(self) -> None:
        chapters = [{"start_time": 200, "title": "B"}, {"start_time": 0, "title": "A"}]

        assert _labels(_chapters_to_moments(chapters, 400)) == ["A", "B"]

    def test_should_end_an_unsorted_chapter_at_the_next_later_start(self) -> None:
        chapters = [{"start_time": 200, "title": "B"}, {"start_time": 0, "title": "A"}]

        assert _chapters_to_moments(chapters, 400)[0]["endSeconds"] == 199

    def test_should_skip_a_chapter_without_a_start(self) -> None:
        chapters = [
            {"title": "No start"},
            {"start_time": 0, "title": "Opening"},
            {"start_time": 200, "title": "B"},
        ]

        assert _labels(_chapters_to_moments(chapters, 400)) == ["Opening", "B"]


class TestAssembledResponse:
    """The planned source lacks prose or times → the fallback chain feeds the tab, never padded."""

    def test_should_fall_back_to_the_outline_for_an_itinerary_fed_moment_tab(self) -> None:
        triage = {
            "contentTags": ["travel"],
            "primaryTag": "travel",
            "tabs": [
                {
                    "id": "timeline",
                    "label": "Key Moments",
                    "emoji": "🎬",
                    "component": "moment_track",
                    "dataSource": "travel.itinerary",
                }
            ],
        }
        meta = {"duration": 900, "outline": _OUTLINE, "chapters": []}

        result = assemble_response(
            triage, {"travel": {"itinerary": _ITINERARY}}, None, None, video_meta=meta
        )

        moments = next(t for t in result["tabs"] if t["component"] == "moment_track")
        assert _labels(moments["props"]["items"]) == ["Arrival in Hanoi", "Ha Long Bay cruise"]

    def test_should_fall_back_to_the_outline_for_timeless_key_moments(self) -> None:
        triage = {
            "contentTags": ["learning"],
            "modifiers": ["narrative"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "moments",
                    "label": "Key Moments",
                    "emoji": "🎬",
                    "component": "moment_track",
                    "dataSource": "narrative.keyMoments",
                }
            ],
        }
        timeless = [{"description": "The reveal"}, {"description": "The twist"}]
        meta = {"duration": 900, "outline": _OUTLINE, "chapters": []}

        result = assemble_response(
            triage, {"narrative": {"keyMoments": timeless}}, None, None, video_meta=meta
        )

        moments = next(t for t in result["tabs"] if t["component"] == "moment_track")
        assert _labels(moments["props"]["items"]) == ["Arrival in Hanoi", "Ha Long Bay cruise"]


class TestPodcastSegmentModel:
    def test_should_accept_description_as_the_segment_summary(self) -> None:
        segment = PodcastSegment.model_validate({"title": "Intro", "description": "Guest welcome."})

        assert segment.summary == "Guest welcome."

    def test_should_still_accept_summary_by_its_own_name(self) -> None:
        segment = PodcastSegment.model_validate({"title": "Intro", "summary": "Guest welcome."})

        assert segment.model_dump()["summary"] == "Guest welcome."
