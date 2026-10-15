"""Moments rank-sampled by salience, capped by duration, none when the steps are the
timeline (output-quality 0b.2 — R7, R9, D6; content-model §9).

``_evenly_sample`` cut a 63-min talk's 46 moments to 20 by index, so the headline beats
fell between the strides and the stride count ignored the video's length. The cap now
follows content-model §9, each stride of the video keeps its most salient moment
(description / quote / number, and the beats the rest of the extraction points at),
and a recipe / workout / step tutorial — navigated by its steps — ships no Moments tab.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.core import (
    STEPS_ARE_THE_TIMELINE,
    _post_process_tabs,
    steps_are_the_timeline,
)
from src.services.pipeline.assembly.sampling import (
    MomentAnchors,
    collect_moment_anchors,
    is_described_track,
    moment_cap_for_duration,
    moment_salience,
    moment_track_cap,
    rank_sample,
    sample_moments,
)


def _moment(seconds: int, label: str, **extra: object) -> dict:
    return {
        "label": label,
        "seconds": seconds,
        "time": f"{seconds // 60}:{seconds % 60:02d}",
        **extra,
    }


def _word(index: int) -> str:
    """A unique, digit-free word per index (A … Z, BA, BB …): a digit would score."""
    word = ""
    while True:
        index, letter = divmod(index, 26)
        word = chr(65 + letter) + word
        if index == 0:
            return word


def _plain_moments(count: int, spacing: int = 60) -> list[dict]:
    return [_moment(i * spacing, f"Beat {_word(i)}") for i in range(count)]


def _moment_tab(items: list[dict]) -> dict:
    return {
        "id": "timeline",
        "label": "Key Moments",
        "emoji": "🎬",
        "component": "moment_track",
        "props": {"items": items},
    }


# ─── Cap by duration (content-model §9) ───


class TestMomentCapForDuration:
    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [(10, 4), (15, 5), (17, 8), (44, 11), (63, 12), (83, 16), (90, 18), (280, 46)],
    )
    def test_should_size_the_cap_to_the_video(self, minutes: int, expected: int) -> None:
        assert moment_cap_for_duration(minutes * 60, 20) == expected

    def test_should_keep_the_configured_cap_without_a_duration(self) -> None:
        assert moment_cap_for_duration(None, 20) == 20

    def test_should_never_exceed_sixty_moments(self) -> None:
        assert moment_cap_for_duration(10 * 3600, 20) == 60


# ─── Salience (R7) ───


class TestMomentSalience:
    def test_should_rank_a_described_moment_above_a_bare_label(self) -> None:
        described = _moment(10, "Opening", description="What the speaker established here.")

        assert moment_salience(described) > moment_salience(_moment(10, "Opening"))

    def test_should_count_a_verbatim_quote(self) -> None:
        quoted = _moment(10, 'Closing: "total optimism is the only play"')

        assert moment_salience(quoted) == 1

    def test_should_count_a_number(self) -> None:
        assert moment_salience(_moment(10, "Install time falls to 9 seconds")) == 1

    def test_should_treat_the_time_field_as_no_number(self) -> None:
        assert moment_salience(_moment(610, "A plain beat")) == 0

    def test_should_add_a_point_per_anchor_the_extraction_points_at(self) -> None:
        anchors = MomentAnchors(
            times=[(600.0, None)], texts=[frozenset({"pencils", "hand-written", "code"})]
        )

        assert moment_salience(_moment(610, "Pencils down on hand-written code"), anchors) == 2

    def test_should_anchor_a_moment_inside_another_items_span(self) -> None:
        anchors = MomentAnchors(times=[(500.0, 900.0)], texts=[])

        assert moment_salience(_moment(700, "Inside the span"), anchors) == 1

    def test_should_cap_anchor_points_at_three(self) -> None:
        anchors = MomentAnchors(times=[(100.0, None)] * 6, texts=[])

        assert moment_salience(_moment(100, "Crowded"), anchors) == 3

    def test_should_never_read_a_bool_as_seconds(self) -> None:
        anchors = MomentAnchors(times=[(0.0, None)], texts=[])

        assert moment_salience({"label": "A plain beat", "seconds": True}, anchors) == 0

    def test_should_score_a_plain_fixture_label_zero(self) -> None:
        assert [moment_salience(m) for m in _plain_moments(30)] == [0] * 30


class TestCollectMomentAnchors:
    def test_should_take_timestamps_and_titles_from_every_other_list(self) -> None:
        extraction = {
            "learning": {
                "timestamps": [_moment(5, "Never an anchor")],
                "keyPoints": [{"title": "The Rails moment", "timestamp": 1093}],
                "takeaways": ["Go pencils down"],
            },
            "news": {
                "claims": [{"claim": "Prices doubled", "timestamp": 2361, "endTimestamp": 2569}]
            },
        }

        anchors = collect_moment_anchors(extraction, {"keyTakeaways": ["Total optimism"]})

        assert anchors.times == ((1093.0, None), (2361.0, 2569.0))
        assert len(anchors.texts) == 4

    @pytest.mark.parametrize(
        "path",
        [
            "learning.timestamps",
            "narrative.keyMoments",
            "podcast.segments",
            "gaming.highlights",
            "sport.matchEvents",
            "news.storyTimeline",
            "music.structure",
        ],
    )
    def test_should_never_anchor_a_moment_list_on_itself(self, path: str) -> None:
        domain, field = path.split(".")
        moments = [{"title": "Kickoff", "timestamp": 30, "endTimestamp": 90}]

        anchors = collect_moment_anchors({domain: {field: moments}}, None)

        assert (anchors.times, anchors.texts) == ((), ())

    def test_should_keep_a_list_another_component_renders_first_as_an_anchor(self) -> None:
        steps = [{"instruction": "Brown the beef", "timestamp": 120}]

        anchors = collect_moment_anchors({"food": {"steps": steps}}, None)

        assert anchors.times == ((120.0, None),)

    def test_should_never_read_a_bool_as_a_timestamp(self) -> None:
        points = [{"title": "Flagged", "timestamp": True, "endTimestamp": False}]

        anchors = collect_moment_anchors({"learning": {"keyPoints": points}}, None)

        assert anchors.times == ()

    def test_should_return_empty_anchors_without_an_extraction(self) -> None:
        anchors = collect_moment_anchors(None, None)

        assert (anchors.times, anchors.texts) == ((), ())


# ─── Sampling: salience first, then even spacing ───


class TestSampleMoments:
    def test_should_keep_every_moment_under_the_cap(self) -> None:
        items = _plain_moments(5)

        assert sample_moments(items, 12, 600) == items

    def test_should_keep_the_salient_beat_of_each_stride(self) -> None:
        items = _plain_moments(40, spacing=60)  # 40 min, one beat a minute
        items[7]["description"] = "established"  # stride 0 (0–10 min)
        items[33]["description"] = "established"  # stride 3 (30–40 min)

        kept = sample_moments(items, 4, 2400)

        assert {items[7]["seconds"], items[33]["seconds"]} <= {m["seconds"] for m in kept}

    def test_should_represent_every_stride_of_the_video(self) -> None:
        items = _plain_moments(40, spacing=60)

        kept = sample_moments(items, 4, 2400)

        assert [m["seconds"] // 600 for m in kept] == [0, 1, 2, 3]

    def test_should_keep_chronological_order(self) -> None:
        items = _plain_moments(30)
        items[25]["description"] = "late but salient"

        kept = sample_moments(items, 6, 1800)

        assert [m["seconds"] for m in kept] == sorted(m["seconds"] for m in kept)

    def test_should_hand_an_empty_strides_slot_to_the_best_moment_left(self) -> None:
        items = [_moment(s, "early") for s in (10, 20, 30, 40)]  # all in the first half
        items[2]["description"] = "the one that matters"

        kept = sample_moments(items, 2, 1000)

        assert items[2] in kept
        assert len(kept) == 2

    def test_should_sample_by_index_without_a_duration(self) -> None:
        items = _plain_moments(20)

        kept = sample_moments(items, 4, None)

        assert len(kept) == 4
        assert kept[-1]["seconds"] >= items[15]["seconds"]

    def test_should_stride_by_index_when_a_moment_has_no_seconds(self) -> None:
        items = _plain_moments(40, spacing=60)
        del items[35]["seconds"]  # its index (35) must never be read as 0:35
        items[35]["description"] = "late and salient"

        kept = sample_moments(items, 4, 2400)

        assert [items.index(m) // 10 for m in kept] == [0, 1, 2, 3]


class TestRankSample:
    def test_should_take_the_highest_tiers_whole(self) -> None:
        items = list(range(10))

        assert rank_sample(items, 3, lambda i: 1 if i in (2, 5, 8) else 0) == [2, 5, 8]

    def test_should_even_sample_the_tier_that_does_not_fit(self) -> None:
        assert rank_sample(list(range(100)), 3, lambda i: 0) == [0, 50, 99]

    def test_should_return_everything_under_the_cap(self) -> None:
        assert rank_sample([1, 2, 3], 10, lambda i: 0) == [1, 2, 3]


# ─── Through the assembler ───


class TestMomentTabPostProcessing:
    def test_should_cap_a_timeline_to_the_videos_band(self) -> None:
        tab = _moment_tab(_plain_moments(46, spacing=82))

        _post_process_tabs([tab], 63 * 60)

        assert len(tab["props"]["items"]) == 12

    def test_should_keep_the_configured_cap_without_a_duration(self) -> None:
        tab = _moment_tab(_plain_moments(55))

        _post_process_tabs([tab])

        assert len(tab["props"]["items"]) == 20


# ─── §9 sizes label-only tracks; a described track keeps the item cap (g0b open item 1) ───


def _described_moments(count: int, spacing: int = 40) -> list[dict]:
    return [
        _moment(i * spacing, f"Event {_word(i)}", description=f"What event {_word(i)} settled.")
        for i in range(count)
    ]


class TestDescribedTrackCap:
    def test_should_ship_every_event_of_a_described_ten_minute_track(self) -> None:
        """S08: 14 described events in a 10-minute explainer — §9 alone kept 4."""
        tab = _moment_tab(_described_moments(14))

        _post_process_tabs([tab], 600)

        assert len(tab["props"]["items"]) == 14

    def test_should_size_a_label_only_track_by_duration(self) -> None:
        tab = _moment_tab(_plain_moments(14, spacing=40))

        _post_process_tabs([tab], 600)

        assert len(tab["props"]["items"]) == moment_cap_for_duration(600, 20)

    def test_should_rank_sample_a_described_track_over_the_item_cap(self) -> None:
        tab = _moment_tab(_described_moments(30, spacing=20))

        _post_process_tabs([tab], 600)

        assert len(tab["props"]["items"]) == 20

    def test_should_never_give_a_long_described_video_fewer_moments_than_by_duration(
        self,
    ) -> None:
        assert moment_track_cap(_described_moments(80), 280 * 60, 20) == 46

    def test_should_count_a_track_half_described_as_described(self) -> None:
        items = _described_moments(2) + _plain_moments(2)

        assert is_described_track(items) is True

    def test_should_not_count_a_description_that_repeats_the_label(self) -> None:
        items = [
            _moment(i * 60, f"Beat {_word(i)}", description=f" Beat {_word(i)} ") for i in range(4)
        ]

        assert is_described_track(items) is False

    def test_should_treat_an_empty_track_as_label_only(self) -> None:
        assert is_described_track([]) is False


# ─── No Moments tab when the steps are the timeline (D6) ───

_STEPS = [
    {
        "number": i,
        "instruction": f"Step {i}: do the thing, then the next thing.",
        "timestamp": i * 60,
    }
    for i in range(1, 5)
]
_INGREDIENTS = [
    {"name": "Beef", "amount": "1", "unit": "lb"},
    {"name": "Pasta", "amount": "1", "unit": "box"},
]
_FOOD_EXTRACTION = {
    "food": {
        "ingredients": _INGREDIENTS,
        "steps": _STEPS,
        "tips": [{"text": "Rest the lasagna before cutting.", "type": "chef_tip"}],
    }
}
_META = {
    "duration": 1000,
    "outline": [
        {"start": 0, "end": 500, "title": "Sauce"},
        {"start": 500, "end": 1000, "title": "Bake"},
    ],
    "chapters": [],
}


def _food_triage(*extra: dict) -> dict:
    return {
        "contentTags": ["food"],
        "primaryTag": "food",
        "tabs": [
            {"id": "ingredients", "label": "Ingredients", "component": "checklist",
             "dataSource": "food.ingredients", "goal": "Shop"},
            {"id": "cook_steps", "label": "Steps", "component": "step_player",
             "dataSource": "food.steps", "goal": "Cook"},
            *extra,
        ],
    }  # fmt: skip


_PLANNED_MOMENTS = {"id": "moments", "label": "Key Moments", "component": "moment_track",
                    "dataSource": "food.steps", "goal": "Jump"}  # fmt: skip


class TestStepsAreTheTimeline:
    @pytest.mark.parametrize(
        ("component", "expected"),
        [("workout_room", True), ("step_flow_canvas", True), ("spot_explorer", False)],
    )
    def test_should_detect_a_step_player_flow_canvas_or_workout_room(
        self, component: str, expected: bool
    ) -> None:
        assert steps_are_the_timeline([{"component": component}], "fitness", []) is expected

    def test_should_drop_the_planned_moments_tab_of_a_recipe(self) -> None:
        result = assemble_response(
            _food_triage(_PLANNED_MOMENTS), _FOOD_EXTRACTION, None, None, _META
        )

        assert [t["component"] for t in result["tabs"] if t["component"] == "moment_track"] == []

    def test_should_record_why_the_tab_was_dropped(self) -> None:
        result = assemble_response(
            _food_triage(_PLANNED_MOMENTS), _FOOD_EXTRACTION, None, None, _META
        )

        assert {(d["id"], d["reason"]) for d in result["dropped"]} >= {
            ("moments", STEPS_ARE_THE_TIMELINE)
        }

    def test_should_record_the_dropped_tabs_planned_data_source(self) -> None:
        result = assemble_response(
            _food_triage(_PLANNED_MOMENTS), _FOOD_EXTRACTION, None, None, _META
        )

        assert [d["dataSource"] for d in result["dropped"] if d["id"] == "moments"] == [
            "food.steps"
        ]

    def test_should_not_add_the_always_moments_tab_either(self) -> None:
        result = assemble_response(_food_triage(), _FOOD_EXTRACTION, None, None, _META)

        assert "key_moments" not in [t["id"] for t in result["tabs"]]

    def test_should_keep_the_moments_tab_when_no_tab_is_a_step_timeline(self) -> None:
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {"id": "ideas", "label": "Ideas", "component": "info_grid",
                 "dataSource": "learning.keyPoints", "goal": "Learn"},
                {"id": "moments", "label": "Moments", "component": "moment_track",
                 "dataSource": "learning.timestamps", "goal": "Jump"},
            ],
        }  # fmt: skip
        extraction = {
            "learning": {
                "keyPoints": [{"title": "A", "detail": "a"}, {"title": "B", "detail": "b"}],
                "timestamps": _plain_moments(3, spacing=200),
            }
        }

        result = assemble_response(triage, extraction, None, None, _META)

        assert "moments" in [t["id"] for t in result["tabs"]]
