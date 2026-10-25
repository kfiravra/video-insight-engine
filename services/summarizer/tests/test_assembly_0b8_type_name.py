"""output-quality 0b.8 — `type` is a category badge, never the key: a `{type, text}`
tip is headlined by its own text, and the promoted spot keeps the badge (R5;
component-audit §2 #10: S06/S10 spots named "chef_tip" / "safety")."""

import pytest

from src.services.pipeline.assembly import (
    assemble_info_grid,
    assemble_moment_track,
    promote_component,
)

CATEGORY_KEYS = {"chef_tip", "safety", "warning", "form", "progression", "storage"}

S06_TIPS = [
    {
        "type": "chef_tip",
        "text": "🍖 Brown the meat in ball form: two batches are easy, and the contact area and "
        "gaps between the balls let steam escape, so you get deep color on the outside "
        "without overcooking the majority of the meat. It finishes cooking in the braise.",
    },
    {
        "type": "chef_tip",
        "text": "🔥 Don't force the browning along. Scorching the beautiful fond (the brown "
        "bits) in the bottom of the pan costs you flavor, because that's where it lives.",
    },
    {
        "type": "warning",
        "text": "🤏 If a couple of sheets stick together, it's no big deal, so just move on "
        "with the rest.",
    },
    {
        "type": "chef_tip",
        "text": "🥕 Keep the carrot in bolognese even if you dislike it in other pasta sauces. "
        "Like celery, it gives a well-rounded sweetness that balances the acidity of the "
        "tomato and the richness of the meat, and you will not taste it as carrot.",
    },
]


class TestTypeIsNeverTheKey:
    def test_should_headline_a_tip_by_its_text_not_its_type(self):
        out = assemble_info_grid({}, [S06_TIPS[0]], {}, None)

        assert out["items"][0]["key"] == "🍖 Brown the meat in ball form"

    def test_should_split_a_delimiterless_tip_at_its_first_sentence(self):
        out = assemble_info_grid({}, [S06_TIPS[1]], {}, None)

        assert out["items"][0]["key"] == "🔥 Don't force the browning along"
        assert out["items"][0]["value"].startswith("Scorching the beautiful fond")

    def test_should_keep_a_short_tip_as_a_headline_chip(self):
        out = assemble_info_grid({}, [S06_TIPS[2]], {}, None)

        assert out["items"][0]["value"] == ""
        assert out["items"][0]["key"].startswith("🤏 If a couple of sheets")

    def test_should_never_emit_a_category_token_as_a_key(self):
        out = assemble_info_grid({}, S06_TIPS, {}, None)

        assert not {i["key"] for i in out["items"]} & CATEGORY_KEYS

    def test_should_prefer_a_real_key_field_over_the_text(self):
        item = {"type": "safety", "title": "Breathe", "text": "Don't hold your breath."}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["key"] == "Breathe"


class TestCategoryBadge:
    def test_should_ship_the_type_as_a_humanized_category(self):
        out = assemble_info_grid({}, [S06_TIPS[0]], {}, None)

        assert out["items"][0]["category"] == "Chef tip"

    def test_should_badge_a_keyed_item_too(self):
        item = {"type": "form", "title": "Land soft", "text": "Drive your knees high."}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["category"] == "Form"

    def test_should_not_badge_a_sentence_shaped_type(self):
        item = {"type": "This is the kind of thing to avoid.", "text": "Keep stirring."}

        out = assemble_info_grid({}, [item], {}, None)

        assert "category" not in out["items"][0]

    def test_should_keep_an_already_readable_type_as_is(self):
        item = {"type": "Pro Tip", "text": "Salt the water."}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["category"] == "Pro Tip"

    def test_should_drop_a_keyless_item_with_no_text(self):
        assert assemble_info_grid({}, [{"type": "chef_tip"}], {}, None) is None


class TestPromotedSpotsKeepTheBadge:
    def test_should_name_promoted_spots_by_headline_and_carry_the_category(self):
        grid = assemble_info_grid({}, [S06_TIPS[0], S06_TIPS[3]], {}, None)

        component, out = promote_component("info_grid", grid, None, {}, "food")

        assert component == "spot_explorer"
        assert [s["name"] for s in out["spots"]] == [
            "🍖 Brown the meat in ball form",
            "🥕 Keep the carrot in bolognese even if you dislike it in other pasta sauces",
        ]
        assert [s["category"] for s in out["spots"]] == ["Chef tip", "Chef tip"]


class TestKeylessTipsDegradeNeverDrop:
    """A `{type, tip|description|definition|detail}` tip is a card, not a drop."""

    @pytest.mark.parametrize("field", ["tip", "description", "definition", "detail"])
    def test_should_headline_a_typed_tip_by_its_prose_field(self, field):
        item = {"type": "chef_tip", field: "Salt the pasta water generously"}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["key"] == "Salt the pasta water generously"

    def test_should_badge_a_typed_prose_tip(self):
        out = assemble_info_grid({}, [{"type": "safety", "tip": "Brace your core"}], {}, None)

        assert out["items"][0]["category"] == "Safety"

    def test_should_split_a_long_typed_description_at_its_first_sentence(self):
        item = {"type": "chef_tip", "description": S06_TIPS[1]["text"]}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["key"] == "🔥 Don't force the browning along"

    def test_should_not_repeat_the_tip_text_as_its_evidence(self):
        out = assemble_info_grid({}, [{"type": "chef_tip", "note": "Let it rest."}], {}, None)

        assert "evidence" not in out["items"][0]

    def test_should_carry_a_tip_example_as_evidence(self):
        item = {"type": "chef_tip", "text": "Rest the sauce", "example": "overnight in the fridge"}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["evidence"] == "overnight in the fridge"

    def test_should_drop_a_keyless_fragment_that_is_not_a_tip(self):
        # No `text`, no `type`: a concept that lost its name has nothing to headline.
        assert assemble_info_grid({}, [{"name": "", "definition": "no key"}], {}, None) is None


class TestMomentTrackUsesTheSharedNormalizer:
    def test_should_refuse_a_second_moment_at_the_same_second(self):
        # 0b.1's `normalize_moment_items` must be live on the extraction path,
        # not only on the chapter fallback.
        data = [
            {"label": "Season the beef", "seconds": 0},
            {"label": "Brown the pork", "seconds": 0},
            {"label": "Deglaze with wine", "seconds": 240},
        ]

        out = assemble_moment_track({}, data, {}, None)

        assert [m["seconds"] for m in out["items"]] == [0, 240]
