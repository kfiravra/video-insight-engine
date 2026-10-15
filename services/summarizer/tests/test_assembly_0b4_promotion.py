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


def _key_point_rows(n: int) -> list[dict]:
    """S01 "Numbers" shape: learning.keyPoints normalized by assemble_info_grid."""
    return [
        {"key": f"Number {i}", "value": LONG, "emoji": "📈", "timestamp": 60 * (i + 1)}
        for i in range(n)
    ]


# ─── info_grid -> spot_explorer keeps timestamp and evidence (L24) ───


class TestPromotionKeepsTimestampAndEvidence:
    def test_should_keep_every_timestamp_when_promoting_to_spots(self):
        props = {"items": _key_point_rows(24)}

        component, out = promote_component("info_grid", props, None, {}, "learning")

        assert component == "spot_explorer"
        assert [s["timestamp"] for s in out["spots"]] == [60 * (i + 1) for i in range(24)]

    def test_should_carry_evidence_as_the_spot_quote(self):
        rows = _key_point_rows(2)
        rows[0]["evidence"] = "the speaker's own words"

        _, out = promote_component("info_grid", {"items": rows}, None, {}, "learning")

        assert out["spots"][0]["quote"] == "the speaker's own words"
        assert "quote" not in out["spots"][1]

    def test_should_keep_emoji_on_promoted_spots(self):
        _, out = promote_component("info_grid", {"items": _key_point_rows(2)}, None, {}, "learning")

        assert out["spots"][0]["emoji"] == "📈"

    def test_should_stay_a_grid_when_a_headline_chip_would_be_dropped(self):
        # Degrade, never drop: a key-only chip has no spot shape, so the whole
        # tab stays an info_grid where every row renders.
        rows = _key_point_rows(2) + [{"key": "Headline only", "value": ""}]

        component, out = promote_component("info_grid", {"items": rows}, None, {}, "learning")

        assert component == "info_grid"
        assert len(out["items"]) == 3


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

    def test_should_keep_a_paragraph_length_key_only_dict(self):
        out = assemble_info_grid({}, [{"key": "k" * 150, "value": ""}], {}, None)

        assert out["items"][0]["key"] == "k" * 150

    def test_should_keep_a_paragraph_length_name_only_dict(self):
        out = assemble_info_grid({}, [{"name": "n" * 150}], {}, None)

        assert out["items"][0]["key"] == "n" * 150
