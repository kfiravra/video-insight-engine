"""p2 review fixes, lane 5 — two schema examples (review-p2-D.md D9, review.txt subScores).

An example in a schema prompt is a lesson: ``news.txt``'s fare claim said "in 2026" over a quote
that says "next year" (a date derived from nothing, D13), and ``review.txt``'s ``subScores``
template carried a score with a null quote and timestamp against its own VERDICT rule
("subScores only as the reviewer scores them, each with its timestamp and quote").
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.services.pipeline.extraction_prompt import EXAMPLES_DIR, load_schema_text
from src.services.pipeline.schema_fields import load_domain_schema

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _example_object(name: str) -> dict[str, Any]:
    """The JSON object after the schema's ``// Example`` line."""
    text = load_schema_text(name)
    start = text.index("{", text.index("// Example"))
    example, _ = json.JSONDecoder().raw_decode(text, start)
    return example


def _review_prompt_example() -> dict[str, Any]:
    """The worked output in ``examples/review.txt`` (the JSON after its intro lines)."""
    text = (EXAMPLES_DIR / "review.txt").read_text(encoding="utf-8")
    example, _ = json.JSONDecoder().raw_decode(text, text.index("{"))
    return example


class TestNewsExample:
    def test_should_state_no_year_in_a_claim_its_quote_does_not_say(self) -> None:
        claims = _example_object("news")["claims"]

        derived = [
            c["claim"]
            for c in claims
            for year in _YEAR.findall(c["claim"])
            if year not in (c.get("quote") or "")
        ]

        assert derived == []

    def test_should_keep_the_fare_claim_over_its_relative_date_quote(self) -> None:
        claims = {c["quote"]: c["claim"] for c in _example_object("news")["claims"]}

        assert claims["Fares go up fifteen percent next year."] == "Fares will rise 15% next year"


class TestReviewSubScores:
    def test_should_template_a_sub_score_only_with_its_quote_and_timestamp(self) -> None:
        rows = load_domain_schema("review").skeleton["verdict"]["subScores"]

        unanchored = [
            r
            for r in rows
            if r["score"] is not None and (r["quote"] is None or r["timestamp"] is None)
        ]

        assert unanchored == []

    def test_should_template_the_sub_score_rows_with_null_score_quote_and_timestamp(self) -> None:
        rows = load_domain_schema("review").skeleton["verdict"]["subScores"]

        assert [(r["score"], r["quote"], r["timestamp"]) for r in rows] == [(None, None, None)] * 2

    def test_should_keep_the_worked_example_sub_scores_anchored(self) -> None:
        verdict = _example_object("review")["verdict"]

        unanchored = [
            r for r in verdict["subScores"] if not r["quote"] or not isinstance(r["timestamp"], int)
        ]

        assert unanchored == []


class TestReviewPromptExample:
    """``examples/review.txt`` obeys the schema's VERDICT rule: a score only with its quote and
    timestamp (the second the reviewer gives it), else null."""

    def test_should_anchor_every_sub_score_with_a_quote_and_timestamp(self) -> None:
        rows = _review_prompt_example()["verdict"]["subScores"]

        unanchored = [
            r["category"]
            for r in rows
            if not r.get("quote") or not isinstance(r.get("timestamp"), int)
        ]

        assert unanchored == []

    def test_should_anchor_the_rating_score_with_a_quote_and_timestamp(self) -> None:
        rating = _review_prompt_example()["rating"]

        assert rating["score"] is None or (
            rating.get("quote") and isinstance(rating.get("timestamp"), int)
        )

    def test_should_anchor_the_badge_with_the_verdicts_quote_and_timestamp(self) -> None:
        verdict = _review_prompt_example()["verdict"]

        assert verdict["badge"] is None or (
            verdict.get("quote") and isinstance(verdict.get("timestamp"), int)
        )
