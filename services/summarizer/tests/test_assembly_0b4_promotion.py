"""output-quality 0b.4 — info_grid keeps its content: the spot promotion carries
timestamp + evidence, the grid never cuts a value the FE can show, and long bare
strings become cards instead of drops (R2, R10; stage-audit L23/L24/L25)."""

from src.services.pipeline.assembly import (
    assemble_info_grid,
    enforce_density,
    promote_component,
)
from src.services.pipeline.assembly.promotion import INFO_GRID_LONG_VALUE

LONG = "x" * (INFO_GRID_LONG_VALUE + 30)


def _key_points(n: int) -> list[dict]:
    """S01 "Numbers" shape: raw learning.keyPoints as the extraction emits them."""
    return [
        {"title": f"Number {i}", "detail": LONG, "emoji": "📈", "timestamp": 60 * (i + 1)}
        for i in range(n)
    ]


def _explicit_rows(n: int) -> list[dict]:
    """review.specs-style explicit `{key, value, timestamp}` rows."""
    return [{"key": f"Spec {i}", "value": LONG, "timestamp": 45 * (i + 1)} for i in range(n)]


def _tips(n: int) -> list[dict]:
    """Keyless `{type, text, timestamp}` tips long enough to split into a card."""
    return [
        {"type": "chef_tip", "text": f"Tip number {i} is the headline. {LONG}", "timestamp": 30 * i}
        for i in range(1, n + 1)
    ]


def _grid_then_promote(data: list) -> tuple[str, dict]:
    """The live order: assemble_info_grid normalizes, then promotion re-shapes."""
    grid = assemble_info_grid({}, data, {}, None)
    return promote_component("info_grid", grid, data, {}, "learning")


# ─── info_grid -> spot_explorer keeps timestamp and evidence (L24) ───


class TestPromotionKeepsTimestamps:
    def test_should_keep_every_timestamp_when_promoting_to_spots(self):
        component, out = _grid_then_promote(_key_points(24))

        assert component == "spot_explorer"
        assert [s["timestamp"] for s in out["spots"]] == [60 * (i + 1) for i in range(24)]

    def test_should_keep_the_timestamp_of_an_explicit_key_value_row(self):
        _, out = _grid_then_promote(_explicit_rows(2))

        assert [s["timestamp"] for s in out["spots"]] == [45, 90]

    def test_should_keep_the_timestamp_of_a_keyless_tip(self):
        _, out = _grid_then_promote(_tips(2))

        assert [s["timestamp"] for s in out["spots"]] == [30, 60]

    def test_should_keep_a_timestamp_at_the_first_second(self):
        rows = _explicit_rows(2)
        rows[0]["timestamp"] = 0

        _, out = _grid_then_promote(rows)

        assert out["spots"][0]["timestamp"] == 0

    def test_should_read_a_seconds_anchor_as_the_timestamp(self):
        item = {"label": "Hook", "description": "d", "seconds": 42}

        out = assemble_info_grid({}, [item], {}, None)

        assert out["items"][0]["timestamp"] == 42

    def test_should_not_carry_a_timestamp_that_cannot_seek(self):
        out = assemble_info_grid({}, [{"key": "K", "value": "V", "timestamp": "1:23"}], {}, None)

        assert "timestamp" not in out["items"][0]


class TestPromotionKeepsEvidence:
    def test_should_carry_a_keyed_item_quote_as_the_spot_quote(self):
        data = _key_points(2)
        data[0]["quote"] = "the speaker's own words"

        _, out = _grid_then_promote(data)

        assert [s.get("quote") for s in out["spots"]] == ["the speaker's own words", None]

    def test_should_carry_an_explicit_row_source_as_the_spot_quote(self):
        data = _explicit_rows(2)
        data[1]["source"] = "Apple keynote"

        _, out = _grid_then_promote(data)

        assert out["spots"][1]["quote"] == "Apple keynote"

    def test_should_carry_a_tip_example_as_the_spot_quote(self):
        data = _tips(2)
        data[0]["example"] = "a 9x13 pan holds four layers"

        _, out = _grid_then_promote(data)

        assert out["spots"][0]["quote"] == "a 9x13 pan holds four layers"

    def test_should_keep_emoji_on_promoted_spots(self):
        _, out = _grid_then_promote(_key_points(2))

        assert out["spots"][0]["emoji"] == "📈"

    def test_should_stay_a_grid_when_a_headline_chip_would_be_dropped(self):
        # Degrade, never drop: a key-only chip has no spot shape, so the whole
        # tab stays an info_grid where every row renders.
        component, out = _grid_then_promote(_key_points(2) + [{"name": "Headline only"}])

        assert (component, len(out["items"])) == ("info_grid", 3)


# ─── density never cuts what the FE can show (L23) ───


class TestDensityCapsOnlyCompactModes:
    def test_should_leave_a_long_value_intact_in_the_default_card_mode(self):
        props = {"items": [{"key": "useState", "value": "v" * 480}]}

        out = enforce_density("info_grid", props)

        assert out["items"][0]["value"] == "v" * 480

    def test_should_leave_evidence_intact_in_the_default_card_mode(self):
        props = {"items": [{"key": "K", "value": "v", "evidence": "e" * 300}]}

        out = enforce_density("info_grid", props)

        assert out["items"][0]["evidence"] == "e" * 300

    def test_should_cap_values_in_table_mode(self):
        props = {"mode": "table", "items": [{"key": "K", "value": "v" * 300}]}

        out = enforce_density("info_grid", props)

        assert len(out["items"][0]["value"]) == 120

    def test_should_cap_values_in_tag_cloud_mode(self):
        props = {"mode": "tag_cloud", "items": [{"key": "K", "value": "v" * 300}]}

        out = enforce_density("info_grid", props)

        assert out["items"][0]["value"].endswith("…")

    def test_should_still_fold_an_empty_grid(self):
        assert enforce_density("info_grid", {"items": ["not a row"]}) is None


# ─── long bare strings become cards, not drops (L25, S12 7/7) ───


S12_TAKEAWAYS = [
    "Call the API directly when you only send a prompt and get an answer, or when the "
    "process is a straight line. A whole framework for that is like bringing a crane to "
    "lift a coffee.",
    "Use a ready-made agent (OpenClaw, Hermes) if you just want a personal agent for "
    "yourself. You don't need to write any code.",
    "Write it by hand for a straight line. Consider LangChain once some complexity starts "
    "to develop.",
    "Reach for LangGraph when the process becomes a loop, needs a persistent state, or "
    "requires human approval.",
    "Use LangGraph checkpoints and human-in-the-loop when an action is irreversible, such as "
    "crediting a customer. The agent stops before the refund, waits for your approval, and "
    "after a crash resumes without refunding twice.",
    "Build your own agent only when it is part of a product someone else uses, and the "
    "process is a loop with a person in the middle.",
    "Before picking any framework, ask: do you really need it? Sometimes the smartest "
    "answer is short code with no framework.",
]


class TestLongBareStringsAreKept:
    def test_should_keep_all_seven_rules(self):
        out = assemble_info_grid({}, S12_TAKEAWAYS, {}, None)

        assert len(out["items"]) == 7

    def test_should_split_a_long_paragraph_at_its_first_sentence(self):
        out = assemble_info_grid({}, [S12_TAKEAWAYS[0]], {}, None)

        assert out["items"][0] == {
            "key": "Call the API directly when you only send a prompt and get an answer, or when "
            "the process is a straight line",
            "value": "A whole framework for that is like bringing a crane to lift a coffee.",
        }

    def test_should_keep_a_single_long_sentence_as_a_headline_chip(self):
        out = assemble_info_grid({}, [S12_TAKEAWAYS[5]], {}, None)

        assert out["items"][0] == {"key": S12_TAKEAWAYS[5], "value": ""}

    def test_should_not_split_on_an_abbreviation_inside_the_headline_minimum(self):
        text = "No. 1 rule of lasagna is patience, and it holds for every layer you build " * 2

        out = assemble_info_grid({}, [text.strip()], {}, None)

        assert out["items"][0]["key"].startswith("No. 1 rule of lasagna is patience")

    def test_should_prefer_a_delimiter_split_over_a_sentence_split(self):
        out = assemble_info_grid({}, [S12_TAKEAWAYS[6]], {}, None)

        assert out["items"][0]["key"] == "Before picking any framework, ask"

    def test_should_keep_an_ellipsis_that_closes_the_headline(self):
        text = (
            "Wait for the dough to rest the full hour... Then roll it as thin as you can "
            "without tearing, and keep the rest covered so it never dries out on the board."
        )

        out = assemble_info_grid({}, [text], {}, None)

        assert out["items"][0]["key"] == "Wait for the dough to rest the full hour..."

    def test_should_keep_a_long_cjk_paragraph_whole(self):
        # "。" carries no following space, so there is no boundary to split at.
        text = "在选择框架之前先问自己是否真的需要它。有时最聪明的答案是没有框架的简短代码。" * 4

        out = assemble_info_grid({}, [text], {}, None)

        assert out["items"][0] == {"key": text, "value": ""}

    def test_should_keep_a_paragraph_length_key_only_dict(self):
        out = assemble_info_grid({}, [{"key": "k" * 150, "value": ""}], {}, None)

        assert out["items"][0]["key"] == "k" * 150

    def test_should_keep_a_paragraph_length_name_only_dict(self):
        out = assemble_info_grid({}, [{"name": "n" * 150}], {}, None)

        assert out["items"][0]["key"] == "n" * 150
