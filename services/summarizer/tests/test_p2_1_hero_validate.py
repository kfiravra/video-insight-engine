"""The hero rewrite's checks and its view of the extraction (output-quality 2.1; D13, D18, G1-6)."""

from __future__ import annotations

from typing import Any

import pytest

from src.services.pipeline.hero_compact import (
    HERO_EXTRACTION_MAX_TOKENS,
    compact_for_hero,
    estimate_tokens,
)
from src.services.pipeline.hero_validate import (
    HeroDraft,
    HeroEvidence,
    Problem,
    check_hero,
    finalize,
    numbers_in,
    parse_draft,
)

_EVIDENCE_TEXT = (
    "Alice on agents. [0:30] Alice argues 37signals went pencils down in 2025 and wrote "
    "150,000 lines. The Kodak Brownie made painters pivot. Rails and boring technology win."
)
_GOOD: dict[str, Any] = {
    "tldr": "Hand-written code stopped paying in 2025, so let agents write it.",
    "brief": (
        "Alice opens on the Kodak Brownie. She argues that 37signals went pencils down in 2025. "
        "Her output grew to 150,000 lines. The close asks for total optimism."
    ),
    "keyTakeaways": [
        {"text": "Hand-written code no longer pays.", "ref": "K1"},
        {"text": "Ship a CLI by next Friday.", "ref": "K2"},
        {"text": "Output grew to 150,000 lines.", "ref": "M1"},
    ],
    "worthItFor": "Engineering leads deciding how far to let agents write their code.",
    "skipIf": "You want a how-to: no code is written on stage.",
    "seoDescription": "Alice on why hand-written code stopped paying.",
}


def _evidence(**changes: Any) -> HeroEvidence:
    values: dict[str, Any] = {
        "text": _EVIDENCE_TEXT,
        "stamps": {"K1": 60, "K2": 245, "M1": 400},
        "duration": 600,
        "tab_labels": ("💡 Key Ideas", "🎬 Moments"),
        "takeaway_count": 3,
    }
    return HeroEvidence(**{**values, **changes})


def _draft(**changes: Any) -> HeroDraft:
    draft = parse_draft({**_GOOD, **changes}, _evidence())
    assert draft is not None
    return draft


def _problems(**changes: Any) -> list[Problem]:
    return check_hero(_draft(**changes), _evidence())


def _fields(problems: list[Problem]) -> list[str]:
    return [p.field for p in problems]


class TestGoodAnswer:
    def test_should_pass_every_check_when_the_answer_follows_the_rules(self) -> None:
        assert _problems() == []

    def test_should_not_read_an_answer_that_is_not_a_json_object(self) -> None:
        assert parse_draft(["tldr"], _evidence()) is None


class TestWorthItForAndSkipIf:
    def test_should_fail_a_line_over_20_words(self) -> None:
        line = "Engineering leads " + "and their teams " * 7  # 23 words

        assert _fields(_problems(worthItFor=line)) == ["worthItFor"]

    def test_should_fail_a_line_naming_a_fact_from_outside_the_video(self) -> None:
        problems = _problems(worthItFor="Fans of agents who also liked the Netflix documentary.")

        assert [p.message for p in problems] == [
            'worthItFor names "Netflix", which the video does not contain'
        ]

    def test_should_fail_a_line_stating_a_number_not_in_the_video(self) -> None:
        assert _fields(_problems(skipIf="You want the 3 hour cut.")) == ["skipIf"]

    @pytest.mark.parametrize(
        "line",
        [
            "You cannot stand a host who rambles.",
            "You find the speaker annoying.",
            "You came for a great presenter.",
            "The host is hard to follow.",
        ],
    )
    def test_should_fail_a_line_that_judges_the_creator(self, line: str) -> None:
        assert _fields(_problems(skipIf=line)) == ["skipIf"]

    def test_should_pass_a_content_word_that_also_judges_when_it_names_an_idea(self) -> None:
        assert _problems(worthItFor="Rails teams weighing boring technology against agents.") == []

    def test_should_pass_a_clock_inside_the_video(self) -> None:
        assert _problems(skipIf="You want code: the only demo starts at 8:20.") == []

    def test_should_fail_a_clock_past_the_videos_end(self) -> None:
        assert _fields(_problems(skipIf="You want code: the only demo starts at 48:13.")) == [
            "skipIf"
        ]


class TestNumbers:
    def test_should_read_thousands_separators_and_leading_zeros_as_the_same_number(self) -> None:
        assert numbers_in("1,000x in 09 days, 4.5 or 150000") == {"1000", "9", "4.5", "150000"}

    def test_should_pass_a_number_the_video_wrote_with_a_separator(self) -> None:
        assert (
            _problems(tldr="Agents wrote 150000 lines, so hand-written code stopped paying.") == []
        )

    @pytest.mark.parametrize(
        "sentence",
        [
            "The cable car ride takes a 20-min climb from 1946 onwards.",
            "Tickets cost 1070/1779 dong at the gate.",
        ],
    )
    def test_should_flag_a_brief_sentence_stating_figures_not_in_the_transcript(
        self, sentence: str
    ) -> None:
        brief = f"{_GOOD['brief']} {sentence}"

        problems = _problems(brief=brief)

        assert [(p.field, p.repair, p.index) for p in problems] == [("brief", "drop_sentence", 4)]

    def test_should_fail_a_takeaway_stating_a_number_not_in_the_video(self) -> None:
        takeaways = [*_GOOD["keyTakeaways"][:2], {"text": "Output grew 60 times.", "ref": "M1"}]

        assert _fields(_problems(keyTakeaways=takeaways)) == ["keyTakeaways"]

    def test_should_ignore_transcript_markers_when_collecting_the_videos_numbers(self) -> None:
        assert "30" not in _evidence().numbers


class TestBrief:
    def test_should_fail_a_brief_under_three_sentences(self) -> None:
        problems = _problems(brief="Alice opens on the Kodak Brownie. The close asks for optimism.")

        assert [(p.field, p.repair) for p in problems] == [("brief", "fail")]

    def test_should_flag_a_sentence_that_names_a_tab(self) -> None:
        brief = f"{_GOOD['brief']} The Key Ideas list every argument."

        assert [(p.field, p.index) for p in _problems(brief=brief)] == [("brief", 4)]

    def test_should_flag_a_sentence_that_points_the_reader_at_the_page(self) -> None:
        brief = f"Check out the closing call. {_GOOD['brief']}"

        assert [(p.field, p.index) for p in _problems(brief=brief)] == [("brief", 0)]

    def test_should_fail_a_tldr_that_names_a_tab(self) -> None:
        assert _fields(_problems(tldr="The Key Ideas say hand-written code stopped paying.")) == [
            "tldr"
        ]


class TestTakeaways:
    def test_should_fail_a_set_shorter_than_the_band(self) -> None:
        problems = _problems(keyTakeaways=_GOOD["keyTakeaways"][:2])

        assert [(p.field, p.repair) for p in problems] == [("keyTakeaways", "fail")]

    def test_should_fail_a_takeaway_citing_no_known_id_and_no_clock(self) -> None:
        takeaways = [*_GOOD["keyTakeaways"][:2], {"text": "Optimism wins.", "ref": "K99"}]

        assert _fields(_problems(keyTakeaways=takeaways)) == ["keyTakeaways"]

    def test_should_fail_a_takeaway_clock_past_the_videos_end(self) -> None:
        takeaways = [*_GOOD["keyTakeaways"][:2], {"text": "Optimism wins.", "timestamp": "11:00"}]

        assert _fields(_problems(keyTakeaways=takeaways)) == ["keyTakeaways"]

    def test_should_fail_a_repeated_takeaway(self) -> None:
        takeaways = [*_GOOD["keyTakeaways"][:2], _GOOD["keyTakeaways"][0]]

        assert _fields(_problems(keyTakeaways=takeaways)) == ["keyTakeaways"]


class TestFinalize:
    def test_should_drop_a_failing_optional_line_and_keep_the_rest(self) -> None:
        draft = _draft(worthItFor="Fans of the Netflix documentary.")

        result = finalize(draft, check_hero(draft, _evidence()), 3)

        assert result is not None
        assert (result.worth_it_for, result.skip_if) == ("", _GOOD["skipIf"])

    def test_should_drop_a_failing_brief_sentence_while_three_remain(self) -> None:
        draft = _draft(brief=f"{_GOOD['brief']} It began in 1946.")

        result = finalize(draft, check_hero(draft, _evidence()), 3)

        assert result is not None and result.brief == " ".join(_draft().sentences)

    def test_should_return_none_when_the_tldr_fails(self) -> None:
        draft = _draft(tldr="It all began in 1946.")

        assert finalize(draft, check_hero(draft, _evidence()), 3) is None

    def test_should_keep_the_first_takeaways_of_a_set_longer_than_the_band(self) -> None:
        takeaways = [*_GOOD["keyTakeaways"], {"text": "Optimism wins.", "ref": "K2"}]
        draft = _draft(keyTakeaways=takeaways)

        result = finalize(draft, check_hero(draft, _evidence()), 3)

        assert result is not None and [t.timestamp for t in result.key_takeaways] == [60, 245, 400]

    def test_should_keep_the_first_five_sentences_of_a_longer_brief(self) -> None:
        brief = f"{_GOOD['brief']} It is optimistic. It ends on optimism."
        draft = _draft(brief=brief)

        result = finalize(draft, check_hero(draft, _evidence()), 3)

        assert result is not None and result.brief.endswith("It is optimistic.")


# ─── The compacted extraction ───


def _idea(n: int, seconds: int, words: int = 10) -> dict[str, Any]:
    return {
        "claim": f"Claim {n} " + "word " * words,
        "why": "because " * words,
        "quote": "said " * words,
        "timestamp": seconds,
        "kind": "argument",
    }


_SMALL: dict[str, Any] = {
    "learning": {
        "summary": "A talk.",
        "keyIdeas": [_idea(1, 60), _idea(2, 120)],
        "timestamps": [{"seconds": 30, "time": "0:30", "label": "Opening"}],
        "takeaways": ["Go pencils down"],
    },
    "_groups": [{"group": "text1"}],
}


class TestCompactForHero:
    def test_should_give_each_stamped_item_an_id_and_its_seconds(self) -> None:
        compact = compact_for_hero(_SMALL, 600)

        assert dict(compact.stamps) == {"K1": 60, "K2": 120, "M1": 30}

    def test_should_render_one_line_per_item_with_its_id_and_clock(self) -> None:
        lines = compact_for_hero(_SMALL, 600).text.split("\n")

        assert lines[2].startswith("K1 [1:00] Claim 1") and "(kind=argument)" in lines[2]

    def test_should_leave_a_stamp_past_the_end_without_an_id(self) -> None:
        extraction = {"learning": {"keyIdeas": [_idea(1, 60), _idea(2, 900)]}}

        assert dict(compact_for_hero(extraction, 600).stamps) == {"K1": 60}

    def test_should_strip_ids_and_clocks_from_the_evidence(self) -> None:
        evidence = compact_for_hero(_SMALL, 600).evidence

        assert ("K1 [1:00]" in evidence, "Claim 1" in evidence) == (False, True)

    def test_should_number_nested_stamped_items(self) -> None:
        extraction = {
            "travel": {
                "itinerary": [
                    {"day": 1, "city": "Hanoi", "spots": [{"name": "Lake", "timestamp": 70}]},
                    {"day": 2, "city": "Hue", "spots": [{"name": "Citadel", "timestamp": 300}]},
                ]
            }
        }

        assert dict(compact_for_hero(extraction, 600).stamps) == {"L1": 70, "L2": 300}

    def test_should_fit_the_token_budget_on_a_long_video(self) -> None:
        ideas = [_idea(n, n * 60, words=40) for n in range(1, 61)]
        extraction = {"learning": {"keyIdeas": ideas, "timestamps": ideas}}

        compact = compact_for_hero(extraction, 3600)

        assert estimate_tokens(compact.text) <= HERO_EXTRACTION_MAX_TOKENS

    def test_should_keep_ids_and_the_last_item_when_a_long_list_is_spread(self) -> None:
        ideas = [_idea(n, n * 60, words=40) for n in range(1, 61)]

        compact = compact_for_hero({"learning": {"keyIdeas": ideas}}, 3600, max_tokens=800)

        assert (compact.stamps.get("K60"), compact.stamps.get("K1")) == (3600, 60)

    def test_should_render_the_same_bytes_for_the_same_extraction(self) -> None:
        assert compact_for_hero(_SMALL, 600) == compact_for_hero(_SMALL, 600)

    def test_should_return_nothing_for_an_empty_extraction(self) -> None:
        assert compact_for_hero({}, 600).text == ""
