"""Assembler passthrough tests for the 4.1 drift fix (project-score-9).

Adding fields to the Pydantic mirrors is not enough on its own — the assembler
normalizers (`_normalize_to_spot`, `_normalize_step`) whitelist which item
fields reach the frontend props. These tests pin the new TS-declared fields
(SpotItem: currency/bookingSearch/specs/rating; StepItem: title/safetyNote)
through the normalizers so the fix is effective end-to-end.
"""

from __future__ import annotations

from src.services.pipeline.assembly.normalizers import (
    _normalize_step,
    _normalize_to_spot,
)


class TestSpotPassthrough:
    def test_new_spot_fields_pass_through(self):
        spot = _normalize_to_spot(
            {
                "name": "Senso-ji Temple",
                "description": "Tokyo's oldest temple",
                "cost": "Free",
                "currency": "JPY",
                "bookingSearch": "Senso-ji guided tour",
                "specs": "Open 6:00-17:00",
                "rating": 4.7,
                # cost/duration/rating ship only when anchored (0b.13 derived_guard),
                # a rating only beside the words that give it (2.0 c).
                "timestamp": 95,
                "quote": "Easily a five-star stop.",
            }
        )
        assert spot is not None
        assert spot["currency"] == "JPY"
        assert spot["bookingSearch"] == "Senso-ji guided tour"
        assert spot["specs"] == "Open 6:00-17:00"
        assert spot["rating"] == 4.7

    def test_empty_new_fields_are_omitted(self):
        spot = _normalize_to_spot(
            {
                "name": "Louvre",
                "description": "Museum",
                "currency": "",
                "rating": None,
            }
        )
        assert spot is not None
        assert "currency" not in spot
        assert "rating" not in spot


class TestStepPassthrough:
    def test_safety_note_and_title_pass_through(self):
        step = _normalize_step(
            {
                "number": 3,
                "title": "Sear the steak",
                "instruction": "Sear 90 seconds per side",
                "safetyNote": "Hot oil spatters — keep the pan lid nearby",
            },
            2,
        )
        assert step["safetyNote"] == "Hot oil spatters — keep the pan lid nearby"
        assert step["title"] == "Sear the steak"

    def test_absent_safety_note_is_omitted(self):
        step = _normalize_step({"number": 1, "instruction": "Chop the onions"}, 0)
        assert "safetyNote" not in step
        assert "title" not in step

    def test_title_not_duplicated_when_used_as_instruction(self):
        """When the item has no instruction, title is promoted to instruction —
        emitting it as `title` too would render the same text twice."""
        step = _normalize_step({"number": 1, "title": "Cut the boards"}, 0)
        assert step["instruction"] == "Cut the boards"
        assert "title" not in step


class TestSpotKeyValueMapping:
    """Regression: ReviewSpec {key, value} rows (review.specs routed to
    spot_explorer by the planner) previously normalized to None and dropped
    the whole tab. promotion.py already knew this mapping; the normalizer
    must too."""

    def test_key_value_maps_to_name_description(self):
        spot = _normalize_to_spot({"key": "Box price", "value": "$420 at purchase"})
        assert spot is not None
        assert spot["name"] == "Box price"
        assert spot["description"] == "$420 at purchase"

    def test_named_fields_win_over_key_value(self):
        spot = _normalize_to_spot(
            {"name": "Luffy SR", "key": "ignored", "description": "Secret rare", "value": "x"}
        )
        assert spot is not None
        assert spot["name"] == "Luffy SR"
        assert spot["description"] == "Secret rare"

    def test_key_without_any_content_still_dropped(self):
        assert _normalize_to_spot({"key": "Lone key"}) is None
