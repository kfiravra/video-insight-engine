"""output-quality 0b.13 — nothing derived from nothing (D13, R10): scores, tiers,
statuses, rest times, dates and durations ship only when the extraction carries
them on an item anchored in the video (a timestamp); no numeric axis → no radar;
no stated rank → a list, not tiers. One guard (`assembly/derived_guard.py`),
also on the overview's spread of the primary block and on spot facts."""

import pytest

from src.services.pipeline.assembly import (
    _normalize_exercise,
    assemble_claims_tracker,
    assemble_comparison,
    assemble_overview,
    assemble_step_player,
    assemble_tier_list,
    promote_component,
)
from src.services.pipeline.assembly.attachments import attach_secondaries
from src.services.pipeline.assembly.derived_guard import (
    anchor_seconds,
    is_anchored,
    is_numeric_axis,
    stated_block_scalars,
    stated_number,
    stated_rows,
    stated_scalars,
)
from src.services.pipeline.assembly.normalizers import (
    _normalize_step,
    _normalize_to_spot,
    _to_flash_card,
)

ROWS = [
    {"feature": "Battery", "thisProduct": "6 h", "competitor": "8 h"},
    {"feature": "Price", "thisProduct": "$249", "competitor": "$279"},
]


# S07 (AirPods Pro 2) as stored: a rating and sub-scores the reviewer never gave.
_S07_REVIEW = {
    "product": "Apple AirPods Pro 2",
    "rating": {"score": 9, "maxScore": 10, "label": "Great"},
    "pros": ["Entirely new driver and amplifier"],
    "cons": ["Still no lossless audio"],
    "verdict": {
        "badge": "recommended",
        "bestFor": ["'AirPods people' who wear them everywhere"],
        "notFor": ["Buyers who need lossless audio"],
        "bottomLine": "AirPods Pro 2 are 'really, really good'.",
        "subScores": [{"category": "Sound Quality", "score": 9}],
    },
}
_SPOT = {"name": "Ichiran", "description": "Solo ramen booths", "cost": "¥980", "rating": 4.5}


def _review(rating: dict, sub_scores: list[dict] | None = None) -> dict:
    verdict: dict = {"badge": "recommended", "bottomLine": "Really, really good."}
    if sub_scores is not None:
        verdict["subScores"] = sub_scores
    return {"review": {"product": "AirPods Pro 2", "rating": rating, "verdict": verdict}}


def _overview_data(primary_tag: str, block: dict, video_meta: dict | None = None) -> dict:
    meta = video_meta or {"title": "T", "duration": 677}
    tab = {"_primary_tag": primary_tag, "_video_meta": meta}
    return assemble_overview(tab, None, {primary_tag: block}, None)["data"]


# ─── the guard itself ───


class TestAnchor:
    def test_should_read_the_timestamp_as_the_anchor(self):
        assert anchor_seconds({"timestamp": 915}) == 915

    def test_should_read_seconds_when_there_is_no_timestamp(self):
        assert anchor_seconds({"seconds": 42.9}) == 42

    def test_should_refuse_a_boolean_or_negative_or_missing_anchor(self):
        assert [anchor_seconds(x) for x in ({"timestamp": True}, {"timestamp": -1}, {}, "x")] == [
            None,
            None,
            None,
            None,
        ]

    def test_should_return_the_scalars_only_from_an_anchored_item(self):
        anchored = {"rest": "10 s", "timestamp": 3}

        assert stated_scalars(anchored, ("rest", "sets")) == {"rest": "10 s"}
        assert stated_scalars({"rest": "10 s"}, ("rest",)) == {}

    def test_should_return_a_number_only_when_numeric_and_anchored(self):
        assert stated_number({"score": 9, "timestamp": 5}, "score") == 9
        assert stated_number({"score": "9", "timestamp": 5}, "score") is None
        assert stated_number({"score": 9}, "score") is None

    def test_should_keep_only_anchored_rows_that_carry_the_field(self):
        rows = [{"score": 9, "timestamp": 1}, {"score": 8}, {"timestamp": 2}]

        assert stated_rows(rows, "score") == [{"score": 9, "timestamp": 1}]

    @pytest.mark.parametrize("score", ["9/10", "9", float("nan"), float("inf"), True])
    def test_should_drop_a_row_whose_field_is_not_a_finite_number(self, score: object):
        rows = [{"category": "Sound", "score": score, "timestamp": 1}]

        assert stated_rows(rows, "score") == []

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_should_not_state_a_non_finite_number(self, value: float):
        assert stated_number({"score": value, "timestamp": 5}, "score") is None

    @pytest.mark.parametrize("stamp", [float("nan"), float("inf"), float("-inf")])
    def test_should_skip_a_non_finite_anchor_without_raising(self, stamp: float):
        assert anchor_seconds({"timestamp": stamp}) is None

    def test_should_fall_through_a_non_finite_stamp_to_the_next_anchor(self):
        assert anchor_seconds({"timestamp": float("inf"), "seconds": 42}) == 42

    def test_should_tell_an_anchored_item(self):
        assert is_anchored({"timestamp": 0}) is True
        assert is_anchored({"label": "x"}) is False

    def test_should_not_read_a_string_timestamp_as_an_anchor(self):
        assert anchor_seconds({"timestamp": "915"}) is None

    def test_should_fall_through_a_clock_string_to_the_numeric_seconds(self):
        assert anchor_seconds({"timestamp": "8:30", "seconds": 510}) == 510


class TestBlockScalars:
    def test_should_hold_back_every_scalar_of_an_unanchored_rating_block(self):
        assert stated_block_scalars({"score": 9, "label": "Great"}, "rating") == {}

    def test_should_pass_prose_and_counts_but_hold_back_unanchored_facts(self):
        block = {"product": "AirPods", "status": "verified", "estimatedTime": "2 h", "servings": 4}

        assert stated_block_scalars(block) == {"product": "AirPods", "servings": 4}

    def test_should_pass_the_facts_of_an_anchored_block(self):
        block = {"tier": "S", "timestamp": 12}

        assert stated_block_scalars(block) == block

    def test_should_skip_blank_and_non_scalar_values(self):
        block = {"summary": "  ", "takeaways": ["a"], "meta": {"x": 1}, "keyQuestion": None}

        assert stated_block_scalars(block) == {}


# ─── the overview spread (S07: no 9 / 10 / "Great" beside the hero) ───


class TestOverviewScalars:
    def test_should_not_spread_an_unanchored_review_rating(self):
        data = _overview_data("review", _S07_REVIEW)

        assert {"score", "maxScore", "label"} & data.keys() == set()

    def test_should_keep_the_review_prose(self):
        data = _overview_data("review", _S07_REVIEW)

        assert (data["product"], data["bottomLine"]) == (
            "Apple AirPods Pro 2",
            "AirPods Pro 2 are 'really, really good'.",
        )

    def test_should_spread_a_rating_stated_with_a_timestamp(self):
        rating = {"score": 9, "maxScore": 10, "label": "Great", "timestamp": 600}

        data = _overview_data("review", {**_S07_REVIEW, "rating": rating})

        assert (data["score"], data["maxScore"], data["label"]) == (9, 10, "Great")

    def test_should_drop_an_unanchored_duration_from_the_domain_meta(self):
        fitness = {"meta": {"type": "HIIT", "difficulty": "beginner", "duration": 7}}

        data = _overview_data("fitness", fitness, video_meta={"title": "T"})

        assert "duration" not in data

    def test_should_keep_the_video_duration_over_the_domain_one(self):
        fitness = {"meta": {"type": "HIIT", "duration": 7}}

        assert _overview_data("fitness", fitness)["duration"] == 677


# ─── score / maxScore / subScores (S07: no 9.0, no sub-scores) ───


class TestReviewScore:
    def test_should_not_ship_a_score_the_extraction_did_not_anchor(self):
        out = assemble_comparison({}, ROWS, _review({"score": 9, "maxScore": 10}), None)

        assert "score" not in out["verdict"]
        assert "maxScore" not in out["verdict"]

    def test_should_ship_a_score_stated_with_a_timestamp(self):
        rating = {"score": 9, "maxScore": 10, "timestamp": 600}

        out = assemble_comparison({}, ROWS, _review(rating), None)

        assert (out["verdict"]["score"], out["verdict"]["maxScore"]) == (9, 10)

    def test_should_not_ship_unanchored_sub_scores(self):
        subs = [{"category": "Sound Quality", "score": 9}, {"category": "ANC", "score": 8.5}]

        out = assemble_comparison({}, ROWS, _review({"score": 9}, subs), None)

        assert "subScores" not in out["verdict"]

    def test_should_keep_only_the_anchored_sub_scores(self):
        subs = [
            {"category": "Sound", "score": 9, "timestamp": 120},
            {"category": "ANC", "score": 8.5},
        ]

        out = assemble_comparison({}, ROWS, _review({"score": 9}, subs), None)

        assert out["verdict"]["subScores"] == [{"category": "Sound", "score": 9, "timestamp": 120}]

    def test_should_not_ship_a_sub_score_that_is_not_a_number(self):
        subs = [{"category": "Sound", "score": "9/10", "timestamp": 120}]

        out = assemble_comparison({}, ROWS, _review({"score": 9}, subs), None)

        assert "subScores" not in out["verdict"]

    def test_should_omit_a_badge_the_reviewer_did_not_state(self):
        review = _review({"score": 9})
        review["review"]["verdict"]["badge"] = None

        out = assemble_comparison({}, ROWS, review, None)

        assert "badge" not in out["verdict"]

    @pytest.mark.parametrize("badge", ["RECOMMENDED", "yes", "", 1])
    def test_should_omit_a_badge_outside_the_four_verdicts(self, badge: object):
        """Close-out review W6: a raw verdict ships its badge only as the validator allows."""
        review = _review({"score": 9})
        review["review"]["verdict"]["badge"] = badge

        out = assemble_comparison({}, ROWS, review, None)

        assert "badge" not in out["verdict"]

    def test_should_keep_the_verdict_prose_without_a_score(self):
        out = assemble_comparison({}, ROWS, _review({"score": 9}), None)

        assert out["verdict"]["bottomLine"] == "Really, really good."


# ─── tiers (S09: no invented tiers; no stated rank → a list) ───


class TestTiers:
    def test_should_strip_a_tier_the_extraction_did_not_anchor(self):
        data = [
            {"item": "Loki", "tier": "S", "reason": "needs four", "timestamp": 30},
            {"item": "Nami", "tier": "A", "reason": "alt art"},
            {"item": "Zoro", "tier": "B", "reason": "filler"},
        ]

        out = assemble_tier_list({}, data, {}, None)

        assert [i.get("tier") for i in out["items"]] == ["S", None, None]

    def test_should_carry_the_anchor_on_a_ranked_item(self):
        data = [{"item": f"Card {i}", "tier": "S", "timestamp": 10 * i} for i in range(3)]

        out = assemble_tier_list({}, data, {}, None)

        assert [i["timestamp"] for i in out["items"]] == [0, 10, 20]

    def test_should_refuse_the_tier_list_when_no_rank_is_stated(self):
        # S09: 19 rankings, none anchored → the demote ladder ships a list.
        data = [{"item": f"Card {i}", "tier": "S", "reason": "pulled"} for i in range(19)]

        assert assemble_tier_list({}, data, {}, None) is None


# ─── claim status ───


class TestClaimStatus:
    def test_should_downgrade_an_unanchored_verified_status_to_context(self):
        data = [
            {"claim": "4 failing states", "status": "verified"},
            {"claim": "3 wars", "status": "disputed", "timestamp": 2},
        ]

        out = assemble_claims_tracker({}, data, {}, None)

        assert [c["status"] for c in out["claims"]] == ["context", "disputed"]

    def test_should_ship_the_anchor_that_let_a_status_through(self):
        # Any anchor key lets a status through; that second is the reader's seek point.
        data = [
            {"claim": "4 failing states", "status": "verified", "seconds": 42},
            {"claim": "3 wars", "status": "disputed", "timestamp": 2},
        ]

        out = assemble_claims_tracker({}, data, {}, None)

        assert out["claims"][0]["timestamp"] == 42


# ─── rest times and durations ───


class TestRestAndDuration:
    def test_should_strip_rest_and_duration_from_an_unanchored_exercise(self):
        item = {"name": "Wall sit", "duration": "30 seconds", "rest": "10 seconds"}

        out = _normalize_exercise(item)

        assert "rest" not in out and "duration" not in out

    def test_should_keep_rest_and_duration_on_an_anchored_exercise(self):
        item = {"name": "Wall sit", "duration": "30 seconds", "rest": "10 seconds", "timestamp": 28}

        out = _normalize_exercise(item)

        assert (out["duration"], out["rest"]) == ("30 seconds", "10 seconds")

    def test_should_strip_a_step_duration_without_an_anchor(self):
        data = [{"instruction": "Rest the lasagna", "duration": 10}]

        out = assemble_step_player({}, data, {}, None)

        assert out["steps"][0]["duration"] is None

    def test_should_keep_a_step_duration_stated_with_a_timestamp(self):
        data = [{"instruction": "Rest the lasagna", "duration": 10, "timestamp": 915}]

        out = assemble_step_player({}, data, {}, None)

        assert out["steps"][0]["duration"] == 10

    def test_should_leave_the_duration_off_an_unanchored_exercise_card(self):
        card = _to_flash_card({"name": "Plank", "formCues": ["hips level"], "duration": "60 s"})

        assert card["back"] == "hips level"

    def test_should_put_the_duration_on_an_anchored_exercise_card(self):
        item = {"name": "Plank", "formCues": ["hips level"], "duration": "60 s", "timestamp": 95}

        assert _to_flash_card(item)["back"] == "hips level · 60 s"

    def test_should_keep_the_seek_point_and_duration_of_a_step_at_0_00(self):
        item = {"instruction": "Preheat the oven", "duration": 5, "timestamp": 0}

        step = _normalize_step(item, 0)

        assert (step["timestamp"], step["duration"]) == (0, 5)


# ─── spot facts (the demote rung of a refused tier list / comparison / claims) ───


class TestSpotFacts:
    def test_should_strip_cost_and_rating_from_an_unanchored_spot(self):
        spot = _normalize_to_spot({**_SPOT, "duration": "1 h"})

        assert {"cost", "duration", "rating"} & spot.keys() == set()

    def test_should_keep_the_facts_of_an_anchored_spot(self):
        spot = _normalize_to_spot({**_SPOT, "duration": "1 h", "timestamp": 312})

        assert (spot["cost"], spot["duration"], spot["rating"]) == ("¥980", "1 h", 4.5)

    def test_should_refuse_a_spot_whose_only_content_is_an_unanchored_fact(self):
        assert _normalize_to_spot({"name": "Ichiran", "rating": 4.5}) is None


class TestSpotSeekPointAndQuote:
    def test_should_stamp_the_spot_in_whole_seconds(self):
        assert _normalize_to_spot({**_SPOT, "timestamp": 754.9})["timestamp"] == 754

    @pytest.mark.parametrize("stamp", [-5, "12:34", float("nan"), True])
    def test_should_omit_a_stamp_the_spot_cannot_seek_to(self, stamp: object):
        assert "timestamp" not in _normalize_to_spot({**_SPOT, "timestamp": stamp})

    def test_should_seek_to_the_next_anchor_when_the_timestamp_is_unusable(self):
        spot = _normalize_to_spot({**_SPOT, "timestamp": -1, "seconds": 312})

        assert spot["timestamp"] == 312

    def test_should_pass_the_speakers_quote_through_trimmed(self):
        spot = _normalize_to_spot({**_SPOT, "quote": "  Best ramen in the city.  "})

        assert spot["quote"] == "Best ramen in the city."

    @pytest.mark.parametrize("quote", ["   ", None, 42])
    def test_should_omit_a_blank_or_non_text_quote(self, quote: object):
        assert "quote" not in _normalize_to_spot({**_SPOT, "quote": quote})


# ─── dates (no schema field is a date today; the overview copies any scalar) ───


class TestDates:
    def test_should_not_spread_an_unanchored_date_on_the_overview(self):
        data = _overview_data("news", {"headline": "Armistice", "date": "1918-11-11"})

        assert "date" not in data

    def test_should_spread_a_date_its_block_anchors(self):
        block = {"headline": "Armistice", "event": {"date": "1918-11-11", "timestamp": 40}}

        assert _overview_data("news", block)["date"] == "1918-11-11"


# ─── radar: no numeric axis → no radar ───


class TestRadarAxes:
    def test_should_not_promote_a_comparison_without_numeric_axes(self):
        rows = [
            {"feature": f"F{i}", "thisProduct": "better", "competitor": "worse"} for i in range(6)
        ]

        component, _ = promote_component("comparison", {"comparisons": rows}, None, {}, "review")

        assert component == "comparison"

    def test_should_score_only_a_row_with_numbers_on_both_sides(self):
        assert is_numeric_axis(ROWS[0]) is True
        assert is_numeric_axis({"thisProduct": "6 h", "competitor": "n/a"}) is False


# ─── stat_banner goes through the same anchor ───


class TestStatBannerAnchor:
    def test_should_skip_unanchored_figures(self):
        points = [
            {"title": f"{i + 1}0 million users", "detail": "d", "timestamp": 60 * i}
            for i in range(5)
        ]
        points.append({"title": "99% of the time", "detail": "d"})
        tab = {"id": "answer", "component": "spot_explorer", "props": {"spots": []}}

        extraction = {"learning": {"keyPoints": points}}

        attachments = attach_secondaries(
            tab, extraction, None, None, "learning", is_answer_tab=True
        )

        banner = next(a for a in attachments if a["component"] == "stat_banner")
        assert [s["timestamp"] for s in banner["props"]["stats"]] == [0, 60, 120, 180, 240]
