"""Phase-3 review fixes, the quiz lane (output-quality p3 review A2 / A3 / A5 / A6).

* A2 — the wrong options: each a near-miss built from the correct option by changing a
  different detail, never an absolute the correct option lacks. (Tried and dropped
  2026-10-22: two near-misses + one claim from elsewhere — on the stage harness it
  brought back absurd absolutes, "always produce violence", and 1–3 length tells per
  quiz on S01 / S03, where near-misses alone shipped none);
* A3 — a spare that refills a dropped question takes the dropped question's key
  position, so the shipped quiz keeps the even deal (``quiz_v2_select.shuffle_quiz``);
* A5 — a key idea without a stamp reaches every stretch call (before: none);
* A6 — a 3-question quiz (≤ 15 min) is one call over the whole video, not three calls
  each holding about one key idea.

The web half (A1 / A4: a server-dealt quiz renders as stored; a legacy one is dealt by
a language-invariant seed) is tested in ``apps/web``. No LLM, no I/O.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.quiz_v2 import QuizV2Request, content_words, keyed_option
from src.services.pipeline.quiz_v2_prompt import build_quiz_v2_prompts
from src.services.pipeline.quiz_v2_select import in_slot_of

# S03 (57:14): thirds 0:00–19:05 · 19:05–38:10 · 38:10–57:14.
_S03_S = 3434
_CLI_ANSWER = "Let users bring their own agent by shipping a CLI"
_CLI_TAKEAWAY = "Apps should ship a CLI so users bring their own agent. [45:23]"


def _transcript(duration_s: int) -> str:
    return "\n".join(
        f"[{s // 60}:{s % 60:02d}] at second {s} the speaker explains idea {s}"
        for s in range(0, duration_s, 30)
    )


def _request(duration_s: int, ideas: list[dict[str, Any]]) -> QuizV2Request:
    return QuizV2Request(
        primary_tag="learning",
        title="Behavioral biology",
        duration_s=duration_s,
        transcript=_transcript(duration_s),
        extraction={"learning": {"keyIdeas": ideas}},
    )


def _stamped(*seconds: int) -> list[dict[str, Any]]:
    return [{"claim": f"Idea at {s}", "why": "Because.", "timestamp": s} for s in seconds]


# ─── A3: the refill keeps the even deal ───


def _stored(n: int, key: int, answer: str = "", timestamp: int = 100) -> dict[str, Any]:
    """A stored quiz v2 question keyed at ``key``."""
    options = [f"Wrong {n} a", f"Wrong {n} b", f"Wrong {n} c"]
    options.insert(key, answer or f"Answer {n}")
    return {
        "question": f"Why does the speaker say so (#{n})?",
        "options": options,
        "correctIndex": key,
        "explanation": "The speaker gives this reason.",
        "timestamp": timestamp,
    }


def _settled(spare: dict[str, Any]) -> list[dict[str, Any]]:
    """The shipped quiz once question 1 (keyed at 1) is dropped for restating the hero;
    the quiz was dealt to keys 0 / 1 / 2."""
    quiz = [_stored(0, 0), _stored(1, 1, _CLI_ANSWER), _stored(2, 2)]
    ctx: Any = SimpleNamespace(
        enrichment_data={"quiz": quiz, enrichment_phase.QUIZ_SPARES_KEY: [spare]},
        synthesis_dict={"keyTakeaways": [_CLI_TAKEAWAY]},
        video_summary_id="v",
        video_data=SimpleNamespace(duration=_S03_S),
    )
    enrichment_phase.settle_late_quiz(ctx)
    return ctx.enrichment_data["quiz"]


class TestRefillKeepsTheDeal:
    def test_should_key_three_different_positions_when_a_three_question_quiz_is_refilled(
        self,
    ) -> None:
        shipped = _settled(_stored(3, 0))

        assert sorted(question["correctIndex"] for question in shipped) == [0, 1, 2]

    def test_should_keep_the_spares_answer_when_its_key_moves_to_the_dropped_position(
        self,
    ) -> None:
        shipped = _settled(_stored(3, 0))

        assert keyed_option(shipped[-1]) == "Answer 3"

    def test_should_keep_every_option_of_the_spare_when_its_key_moves(self) -> None:
        spare = _stored(3, 0)

        moved = in_slot_of(spare, _stored(1, 3))

        assert (sorted(moved["options"]), moved["correctIndex"]) == (sorted(spare["options"]), 3)

    def test_should_leave_a_spare_as_stored_when_it_holds_no_valid_key(self) -> None:
        spare = {**_stored(3, 0), "correctIndex": 7}

        assert in_slot_of(spare, _stored(1, 2)) == spare


# ─── A5: a key idea without a stamp reaches the stretch calls ───


def _key_ideas_block(prompt: str) -> str:
    return prompt.split("<key_ideas>\n", 1)[1].split("\n</key_ideas>", 1)[0]


class TestUnstampedKeyIdeas:
    def test_should_give_an_unstamped_key_idea_to_every_stretch_call_when_others_are_stamped(
        self,
    ) -> None:
        ideas = [*_stamped(100), {"claim": "The unstamped idea", "why": "Why."}, *_stamped(3000)]

        calls = build_quiz_v2_prompts(_request(_S03_S, ideas))

        assert ["- The unstamped idea — Why." in _key_ideas_block(c.text) for c in calls] == [
            True,
            True,
        ]

    def test_should_keep_a_stamped_key_idea_out_of_the_other_stretches_calls(self) -> None:
        ideas = [*_stamped(100), {"claim": "The unstamped idea"}, *_stamped(3000)]

        last = build_quiz_v2_prompts(_request(_S03_S, ideas))[-1]

        assert "Idea at 100" not in _key_ideas_block(last.text)


# ─── A6: a 3-question quiz is one call ───


class TestShortVideoOneCall:
    def test_should_make_one_call_over_the_whole_video_when_the_quiz_has_three_questions(
        self,
    ) -> None:
        calls = build_quiz_v2_prompts(_request(150, _stamped(20, 70, 120)))

        assert [call.asked for call in calls] == [7]

    def test_should_state_the_whole_videos_range_when_a_short_video_is_one_call(self) -> None:
        (call,) = build_quiz_v2_prompts(_request(150, _stamped(20, 70, 120)))

        assert "Every timestamp lies inside the video (0:00–2:30)." in call.text

    @pytest.mark.parametrize(("duration_s", "calls"), [(900, 1), (901, 3)])
    def test_should_split_by_third_only_past_the_three_question_band(
        self, duration_s: int, calls: int
    ) -> None:
        ideas = _stamped(100, duration_s // 2, duration_s - 60)

        assert len(build_quiz_v2_prompts(_request(duration_s, ideas))) == calls


# ─── A2: the wrong options ───


def _prompt() -> str:
    (call,) = build_quiz_v2_prompts(_request(150, []))
    return call.text


def _example() -> tuple[str, list[str]]:
    """The prompt example's correct option and its three wrong ones."""
    line = next(line for line in _prompt().splitlines() if line.startswith('{"quiz"'))
    (item,) = json.loads(line)["quiz"]
    options, key = item["options"], item["correctIndex"]
    return options[key], [option for i, option in enumerate(options) if i != key]


def _shared(option: str, key: str) -> frozenset[str]:
    return content_words(option) & content_words(key)


class TestWrongOptions:
    def test_should_ask_for_wrong_options_built_from_the_correct_one(self) -> None:
        assert "build each wrong option from it by changing the one detail" in _prompt()

    def test_should_forbid_an_absolute_the_correct_option_does_not_use(self) -> None:
        assert "never an absolute the correct option does not use" in _prompt()

    def test_should_show_wrong_options_that_each_keep_part_of_the_key_in_the_example(
        self,
    ) -> None:
        key, wrong = _example()

        assert all(_shared(option, key) for option in wrong)

    def test_should_show_wrong_options_that_change_different_details_in_the_example(
        self,
    ) -> None:
        key, wrong = _example()
        changed = [content_words(option) ^ content_words(key) for option in wrong]

        assert all(changed) and len({frozenset(c) for c in changed}) == len(wrong)

    def test_should_keep_every_example_option_within_two_words_of_the_key(self) -> None:
        key, wrong = _example()

        assert all(abs(len(option.split()) - len(key.split())) <= 2 for option in wrong)

    def test_should_show_no_absolute_in_the_examples_wrong_options(self) -> None:
        _, wrong = _example()
        absolutes = {"always", "never", "only", "all", "completely"}

        assert not any(absolutes & set(option.casefold().split()) for option in wrong)
