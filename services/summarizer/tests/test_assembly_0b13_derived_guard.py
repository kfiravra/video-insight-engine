"""output-quality 0b.13 — nothing derived from nothing (D13, R10): scores, tiers,
statuses, rest times, dates and durations ship only when the extraction carries
them on an item anchored in the video (a timestamp); no numeric axis → no radar;
no stated rank → a list, not tiers. One guard (`assembly/derived_guard.py`)."""

from src.services.pipeline.assembly import (
    _normalize_exercise,
    assemble_claims_tracker,
    assemble_comparison,
    assemble_step_player,
    assemble_tier_list,
    normalize_moment_items,
    promote_component,
)
from src.services.pipeline.assembly.attachments import attach_secondaries
from src.services.pipeline.assembly.derived_guard import (
    anchor_seconds,
    is_anchored,
    is_numeric_axis,
    stated_number,
    stated_rows,
    stated_scalars,
)
from src.services.pipeline.assembly.normalizers import _to_flash_card

ROWS = [
    {"feature": "Battery", "thisProduct": "6 h", "competitor": "8 h"},
    {"feature": "Price", "thisProduct": "$249", "competitor": "$279"},
]


def _review(rating: dict, sub_scores: list[dict] | None = None) -> dict:
    verdict: dict = {"badge": "recommended", "bottomLine": "Really, really good."}
    if sub_scores is not None:
        verdict["subScores"] = sub_scores
    return {"review": {"product": "AirPods Pro 2", "rating": rating, "verdict": verdict}}


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
        rows = [{"tier": "S", "timestamp": 1}, {"tier": "A"}, {"timestamp": 2}]

        assert stated_rows(rows, "tier") == [{"tier": "S", "timestamp": 1}]

    def test_should_tell_an_anchored_item(self):
        assert is_anchored({"timestamp": 0}) is True
        assert is_anchored({"label": "x"}) is False


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


# ─── dates ───


class TestDates:
    def test_should_not_ship_a_date_on_a_moment(self):
        # No model carries a date; the moment contract passes none through,
        # so a date can never appear without the extraction stating it.
        moment = {"label": "Ottoman collapse", "description": "tribes fight", "seconds": 100}

        items = normalize_moment_items([{**moment, "date": "1918"}])

        assert "date" not in items[0]


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
