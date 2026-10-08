"""Round-trip regression tests for domain_types drift (project-score-9 4.1).

The 2026-07-05 audit found live drift between ``packages/types/src/vie-response.ts``
and the Pydantic mirrors in ``domain_types.py``:

- ``TravelSpot`` lacked ``currency``, ``bookingSearch``, ``specs``, ``rating``,
  ``thumbnailUrl`` (all declared on TS ``SpotItem``).
- The food-side ``StepItem`` mirror (``FoodStep``) lacked ``safetyNote`` (and
  ``title``/``thumbnailUrl``).

Undeclared Pydantic fields silently drop on ``model_dump`` — same failure class
as the shipped connections/group data-loss bug (fixed 2026-06-07). Each test
feeds a fixture carrying every TS-declared field through the REAL validation
path (``validate_domain_output``) and asserts the fields survive.
"""

from __future__ import annotations

from src.models.domain_types import (
    FoodData,
    NarrativeData,
    ProjectData,
    TravelData,
    validate_domain_output,
)


class TestTravelSpotRoundTrip:
    """TS SpotItem: cost, currency, duration, mapQuery, bookingSearch, tips,
    specs, rating, thumbnailUrl must all survive travel validation."""

    _SPOT = {
        "name": "Senso-ji Temple",
        "emoji": "⛩️",
        "description": "Tokyo's oldest temple",
        "cost": "Free",
        "currency": "JPY",
        "duration": "1-2 hours",
        "mapQuery": "Senso-ji Temple Tokyo",
        "bookingSearch": "Senso-ji guided tour",
        "tips": "Go early to beat the crowds",
        "specs": "Open 6:00-17:00",
        "rating": 4.7,
        "thumbnailUrl": "https://cdn.example.com/sensoji.jpg",
    }

    def _dump_spot(self) -> dict:
        data = {"itinerary": [{"day": 1, "city": "Tokyo", "spots": [self._SPOT]}]}
        validated = validate_domain_output(["travel"], [], data)
        return validated["travel"]["itinerary"][0]["spots"][0]

    def test_new_fields_survive_validation(self):
        spot = self._dump_spot()
        assert spot["currency"] == "JPY"
        assert spot["bookingSearch"] == "Senso-ji guided tour"
        assert spot["specs"] == "Open 6:00-17:00"
        assert spot["rating"] == 4.7
        assert spot["thumbnailUrl"] == "https://cdn.example.com/sensoji.jpg"

    def test_full_ts_field_set_survives(self):
        spot = self._dump_spot()
        for key, value in self._SPOT.items():
            assert spot[key] == value, f"SpotItem.{key} was dropped or mangled"

    def test_direct_model_round_trip(self):
        data = {"itinerary": [{"day": 1, "spots": [self._SPOT]}]}
        dumped = TravelData.model_validate(data).model_dump(by_alias=True)
        spot = dumped["itinerary"][0]["spots"][0]
        for key, value in self._SPOT.items():
            assert spot[key] == value


class TestStepItemRoundTrip:
    """TS StepItem: safetyNote (audit finding) + title/thumbnailUrl must survive."""

    _FOOD_STEP = {
        "number": 3,
        "title": "Sear the steak",
        "instruction": "Sear 90 seconds per side on high heat",
        "duration": 3,
        "tips": "Pat the steak dry first",
        "safetyNote": "Hot oil spatters — keep the pan lid nearby",
        "timestamp": 245,
        "thumbnailUrl": "https://cdn.example.com/sear.jpg",
    }

    def test_food_step_safety_note_survives(self):
        validated = validate_domain_output(["food"], [], {"steps": [self._FOOD_STEP]})
        step = validated["food"]["steps"][0]
        assert step["safetyNote"] == self._FOOD_STEP["safetyNote"]

    def test_food_step_full_field_set_survives(self):
        dumped = FoodData.model_validate({"steps": [self._FOOD_STEP]}).model_dump(by_alias=True)
        step = dumped["steps"][0]
        for key, value in self._FOOD_STEP.items():
            assert step[key] == value, f"StepItem.{key} was dropped or mangled"

    def test_project_step_safety_note_still_survives(self):
        """Regression guard — ProjectStep already declared safetyNote; keep it that way."""
        data = {
            "projectName": "Bookshelf",
            "steps": [
                {
                    "number": 1,
                    "title": "Cut the boards",
                    "instruction": "Cut four boards to 80cm",
                    "safetyNote": "Wear eye protection",
                    "thumbnailUrl": "https://cdn.example.com/cut.jpg",
                },
            ],
        }
        dumped = ProjectData.model_validate(data).model_dump(by_alias=True)
        step = dumped["steps"][0]
        assert step["safetyNote"] == "Wear eye protection"
        assert step["thumbnailUrl"] == "https://cdn.example.com/cut.jpg"


class TestFoodMetaRoundTrip:
    """TS FoodMeta declares totalTime — surfaced by the 4.2 parity sweep."""

    def test_total_time_survives(self):
        data = {"meta": {"prepTime": 10, "cookTime": 20, "totalTime": 30}}
        dumped = FoodData.model_validate(data).model_dump(by_alias=True)
        assert dumped["meta"]["totalTime"] == 30


class TestPackingItemRoundTrip:
    """PackingMission reads weight (kg tally) and emoji from each packing item."""

    def _dump_item(self, item: dict) -> dict:
        validated = validate_domain_output(["travel"], [], {"packingList": [item]})
        return validated["travel"]["packingList"][0]

    def test_should_keep_weight_and_emoji_when_the_item_states_them(self):
        item = self._dump_item({"item": "Hiking boots", "weight": 1.5, "emoji": "🥾"})

        assert (item["weight"], item["emoji"]) == (1.5, "🥾")

    def test_should_drop_weight_when_it_carries_a_unit(self):
        item = self._dump_item({"item": "Sunscreen", "weight": "200 g"})

        assert item["weight"] is None

    def test_should_drop_weight_when_it_is_zero(self):
        item = self._dump_item({"item": "Passport", "weight": 0})

        assert item["weight"] is None


class TestSpanEndRoundTrip:
    """MomentTrack highlight spans read endTimestamp from moments and song sections."""

    def test_should_keep_end_timestamp_on_a_narrative_key_moment(self):
        moment = {"timestamp": 720, "endTimestamp": 812, "description": "Launch day"}

        dumped = NarrativeData.model_validate({"keyMoments": [moment]}).model_dump(by_alias=True)

        assert dumped["keyMoments"][0]["endTimestamp"] == 812

    def test_should_keep_end_timestamp_on_a_music_section(self):
        section = {"name": "Chorus", "timestamp": 60, "endTimestamp": 89}

        validated = validate_domain_output(["music"], [], {"structure": [section]})

        assert validated["music"]["structure"][0]["endTimestamp"] == 89
