"""Phase-1 review fixes, lane B — the models (review-p1-findings.md).

An unknown probe format is logged (R1-W4), an answer-less interview exchange is
dropped by validation (R7-W2), a transcript clock is read as seconds (R4-W5), a
refused claim status is logged (R13-W5) and an ingredient amount nobody stated
stays None (R13-W6). The pipeline fixes are in ``test_p1_review_b.py``.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from src.models.domain_types import (
    FoodIngredient,
    LearningConcept,
    validate_domain_output_with_drops,
)
from src.models.idea_types import Claim, LearningKeyIdea, PodcastQA, seconds_or_none
from src.models.probe_types import DEFAULT_FORMAT, TierProbe

# ─── R1-W4: an unknown probe format is logged ───


def _probe(fmt: str) -> TierProbe:
    return TierProbe.model_validate(
        {"domain": "learning", "format": fmt, "has_visual_demo": False, "confidence": 0.8}
    )


class TestUnknownProbeFormat:
    def test_should_log_the_dropped_label_when_the_format_is_unknown(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.models.probe_types"):
            _probe("keynote")

        assert "'keynote' is not a known format" in caplog.text

    def test_should_still_fall_back_to_the_default_format(self) -> None:
        assert _probe("fireside").format == DEFAULT_FORMAT

    def test_should_log_nothing_when_the_format_is_known(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.models.probe_types"):
            _probe("interview")

        assert "not a known format" not in caplog.text


# ─── R7-W2: an exchange needs its answer ───


class TestPodcastAnswerRequired:
    @pytest.mark.parametrize("raw", [{"question": "Why?"}, {"question": "Why?", "answer": "  "}])
    def test_should_refuse_an_exchange_when_it_has_no_answer(self, raw: dict) -> None:
        with pytest.raises(ValidationError):
            PodcastQA.model_validate(raw)

    def test_should_read_the_answer_from_its_description_alias(self) -> None:
        assert PodcastQA.model_validate({"question": "Q?", "description": "A."}).answer == "A."

    def test_should_drop_the_answerless_exchange_and_keep_the_others(self) -> None:
        data = {"qa": [{"question": "Why native?", "answer": "Speed."}, {"question": "And?"}]}

        validated, _ = validate_domain_output_with_drops(["podcast"], [], data)

        assert [qa["question"] for qa in validated["podcast"]["qa"]] == ["Why native?"]


# ─── R4-W5: a transcript clock is read as seconds ───


class TestClockStamps:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("1:35", 95), ("1:02:05", 3725), ("[4:32]", 272), (" 0:07 ", 7), ("95", 95), (95, 95)],
    )
    def test_should_read_seconds_or_a_whole_clock(self, value: object, expected: int) -> None:
        assert seconds_or_none(value) == expected

    @pytest.mark.parametrize("value", ["12:60", "at 1:35", "1:35 in", "soon", -3, True])
    def test_should_refuse_anything_else(self, value: object) -> None:
        assert seconds_or_none(value) is None

    def test_should_keep_a_concepts_clock_stamp(self) -> None:
        concept = LearningConcept.model_validate({"name": "Attention", "timestamp": "1:35"})

        assert concept.timestamp == 95

    def test_should_keep_a_key_ideas_clock_stamp(self) -> None:
        assert LearningKeyIdea.model_validate({"claim": "c", "timestamp": "2:05"}).timestamp == 125


# ─── R13-W5: a refused claim status is logged ───


class TestRefusedClaimStatus:
    def test_should_log_the_refused_word_when_the_status_is_not_one_of_three(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.models.idea_types"):
            Claim.model_validate({"claim": "c", "status": "verified"})

        assert "'verified' refused" in caplog.text

    def test_should_still_ship_the_claim_without_a_status(self) -> None:
        assert Claim.model_validate({"claim": "c", "status": "disputed"}).status is None

    def test_should_log_nothing_when_the_status_is_missing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.models.idea_types"):
            Claim.model_validate({"claim": "c"})

        assert "refused" not in caplog.text


# ─── R13-W6: an ingredient amount only as stated ───


class TestIngredientAmount:
    @pytest.mark.parametrize("raw", [None, "a pinch", "", True, [2]])
    def test_should_leave_the_amount_unset_when_no_number_is_stated(self, raw: object) -> None:
        assert FoodIngredient.model_validate({"name": "salt", "amount": raw}).amount is None

    def test_should_leave_the_amount_unset_when_the_key_is_missing(self) -> None:
        assert FoodIngredient.model_validate({"name": "salt"}).amount is None

    @pytest.mark.parametrize(("raw", "expected"), [("2", 2.0), (1.5, 1.5), (0, 0.0)])
    def test_should_keep_a_stated_number(self, raw: object, expected: float) -> None:
        assert FoodIngredient.model_validate({"name": "flour", "amount": raw}).amount == expected
