"""The judge's hero + quiz sample (output-quality 2.4, D13 / R10): what is sampled.

The hero's takeaways (each closing with the ``[m:ss]`` it cites) and two quiz
questions are windowed against the transcript around their stamps; a doc without
either, a run without a timed transcript, or a sample rate of 0 judges nothing.
The calls, scores, launch and cost bound live in ``test_p2_4_judge_hero_quiz_calls``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.pipeline import faithfulness_hero_quiz as hq

_PENCILS = "we have gone pencils down on hand-written code at the company"
_ANSWER = "the reason is that the agents now write most of the code for us"
_SCREEN = (
    "<visual_annotations>\n[20:50] Slide | Pencils down\n[40:00] Other slide\n</visual_annotations>"
)


def _segments(until_s: int = 1500, step_s: int = 5) -> list[dict[str, Any]]:
    """A timed transcript: a numbered filler line every ``step_s`` seconds, with the
    two evidence lines at 20:45 and 5:00."""
    lines = {1245: _PENCILS, 300: _ANSWER}
    return [
        {
            "text": lines.get(start, f"filler line number {start}"),
            "start": start,
            "duration": step_s,
        }
        for start in range(0, until_s, step_s)
    ]


def _evidence(**overrides: Any) -> hq.EvidenceSource:
    args: dict[str, Any] = {"segments": _segments(), "visual_annotations": ""}
    args.update(overrides)
    return hq.EvidenceSource.build(**args)


def _question(stamp: int | None, key: int = 1, text: str = "Why did they stop?") -> dict[str, Any]:
    question: dict[str, Any] = {
        "question": text,
        "options": ["Budget cuts", "Agents write the code", "A new manager"],
        "correctIndex": key,
        "explanation": "Said at 5:00.",
    }
    if stamp is not None:
        question["timestamp"] = stamp
    return question


def _quiz_tab(questions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": "quiz", "component": "quiz_arena", "props": {"questions": questions}}


_TAKEAWAYS = [
    "37signals has gone pencils down on hand-written code [20:45]",
    "Agents now write most of the code [5:00]",
    "The talk closes on optimism [24:10]",
]


def _samples(meta: dict[str, Any], tabs: list[Any], **kwargs: Any) -> hq.HeroQuizSamples | None:
    return hq.hero_quiz_samples(
        youtube_id=kwargs.pop("youtube_id", "vid123"),
        meta=meta,
        tabs=tabs,
        evidence=kwargs.pop("evidence", _evidence()),
        sample_rate=kwargs.pop("sample_rate", 0.2),
    )


# ─── The hero sample ────────────────────────────────────────────────────
class TestHeroSample:
    def test_should_judge_every_stamped_takeaway_at_the_second_its_marker_cites(self):
        sample = hq.hero_sample({"keyTakeaways": _TAKEAWAYS}, _evidence())

        assert [item.seconds for item in sample.items] == [1245, 300, 1450]

    def test_should_send_the_claim_without_its_marker(self):
        sample = hq.hero_sample({"keyTakeaways": _TAKEAWAYS[:1]}, _evidence())

        assert sample.items[0].body == "CLAIM: 37signals has gone pencils down on hand-written code"

    def test_should_window_the_evidence_from_30_s_before_to_90_s_after_the_marker(self):
        window = _evidence().window(1245)

        assert _PENCILS in window
        assert "filler line number 1215" in window
        assert "filler line number 1325" in window

    def test_should_leave_speech_outside_the_window_out_of_the_evidence(self):
        window = _evidence().window(1245)

        assert "filler line number 1205" not in window
        assert "filler line number 1335" not in window

    def test_should_mark_the_evidence_lines_with_their_clock(self):
        window = _evidence().window(1245)

        assert window.startswith("[20:15] filler line number 1215")

    def test_should_read_an_hour_marker_when_the_takeaway_cites_past_an_hour(self):
        evidence = _evidence(segments=_segments(until_s=4000))
        sample = hq.hero_sample({"keyTakeaways": ["A late point [1:02:05]"]}, evidence)

        assert sample.items[0].seconds == 3725

    def test_should_count_a_takeaway_as_skipped_when_it_has_no_marker(self):
        meta = {"keyTakeaways": [*_TAKEAWAYS[:2], "An unstamped takeaway"]}

        sample = hq.hero_sample(meta, _evidence())

        assert (len(sample.items), sample.skipped) == (2, 1)

    def test_should_judge_at_most_seven_takeaways_when_meta_carries_more(self):
        meta = {"keyTakeaways": [f"Takeaway {i} [{i}:00]" for i in range(1, 10)]}

        sample = hq.hero_sample(meta, _evidence())

        assert len(sample.items) == hq.HERO_MAX_ITEMS

    def test_should_add_what_the_screen_shows_inside_the_window_when_the_run_has_annotations(
        self,
    ):
        window = _evidence(visual_annotations=_SCREEN).window(1245)

        assert "ON SCREEN:\n[20:50] Slide | Pencils down" in window

    def test_should_leave_screen_entries_outside_the_window_out(self):
        window = _evidence(visual_annotations=_SCREEN).window(1245)

        assert "Other slide" not in window

    def test_should_say_the_window_is_empty_when_no_speech_falls_inside_it(self):
        window = _evidence().window(9000)

        assert window == "(no transcript text in this window)"


# ─── The quiz sample ────────────────────────────────────────────────────
class TestQuizSample:
    def test_should_sample_two_questions_when_the_quiz_has_more(self):
        tab = _quiz_tab([_question(300 + i, text=f"Question {i}?") for i in range(8)])

        sample = hq.quiz_sample([tab], _evidence(), "vid123")

        assert len(sample.items) == hq.QUIZ_SAMPLE

    def test_should_draw_the_same_questions_when_the_video_is_judged_again(self):
        tab = _quiz_tab([_question(300 + i, text=f"Question {i}?") for i in range(8)])

        first = hq.quiz_sample([tab], _evidence(), "vid123")
        second = hq.quiz_sample([tab], _evidence(), "vid123")

        assert first.items == second.items

    def test_should_keep_the_sampled_questions_in_page_order(self):
        tab = _quiz_tab([_question(300 + i, text=f"Question {i}?") for i in range(8)])

        sample = hq.quiz_sample([tab], _evidence(), "vid123")

        assert [item.seconds for item in sample.items] == sorted(i.seconds for i in sample.items)

    def test_should_render_the_keyed_option_as_the_marked_answer(self):
        sample = hq.quiz_sample([_quiz_tab([_question(300)])], _evidence(), "vid123")

        assert sample.items[0].body.endswith("MARKED ANSWER: B. Agents write the code")

    def test_should_judge_the_question_against_the_window_around_its_stamp(self):
        sample = hq.quiz_sample([_quiz_tab([_question(300)])], _evidence(), "vid123")

        assert _ANSWER in sample.items[0].evidence

    @pytest.mark.parametrize(
        "question",
        [_question(None), _question(300, key=7), _question(300, text="  "), _question(-5)],
        ids=["no stamp", "key out of range", "blank question", "negative stamp"],
    )
    def test_should_skip_a_question_when_it_cannot_be_judged(self, question: dict[str, Any]):
        sample = hq.quiz_sample([_quiz_tab([question])], _evidence(), "vid123")

        assert (sample.items, sample.skipped) == ((), 1)

    def test_should_read_questions_only_from_quiz_arena_tabs(self):
        other = {"id": "flash", "component": "flash_deck", "props": {"questions": [_question(300)]}}

        sample = hq.quiz_sample([other], _evidence(), "vid123")

        assert sample.items == ()


# ─── Skip paths ─────────────────────────────────────────────────────────
class TestSkipPaths:
    def test_should_judge_nothing_when_the_sample_rate_is_zero(self):
        samples = _samples({"keyTakeaways": _TAKEAWAYS}, [], sample_rate=0.0)

        assert samples is None

    def test_should_judge_nothing_when_the_doc_has_neither_hero_takeaways_nor_a_quiz(self):
        assert _samples({}, [{"id": "x", "component": "spot_explorer", "props": {}}]) is None

    def test_should_judge_nothing_when_the_run_has_no_timed_transcript(self):
        samples = _samples({"keyTakeaways": _TAKEAWAYS}, [], evidence=_evidence(segments=[]))

        assert samples is None

    def test_should_make_only_the_quiz_call_when_the_doc_has_no_hero_takeaways(self):
        samples = _samples({}, [_quiz_tab([_question(300)])])

        assert samples is not None and [s.score_name for s in samples.calls()] == [hq.SCORE_QUIZ]

    def test_should_make_only_the_hero_call_when_the_doc_has_no_quiz(self):
        samples = _samples({"keyTakeaways": _TAKEAWAYS}, [])

        assert samples is not None and [s.score_name for s in samples.calls()] == [hq.SCORE_HERO]


# ─── Verdict parsing ────────────────────────────────────────────────────
class TestParseVerdicts:
    def test_should_map_each_verdict_to_its_item_id(self):
        raw = '{"verdicts": [{"id": 2, "grounded": false}, {"id": 1, "grounded": true}]}'

        assert hq.parse_verdicts(raw, 2) == [True, False]

    def test_should_leave_an_item_unjudged_when_the_judge_skips_its_id(self):
        assert hq.parse_verdicts('{"verdicts": [{"id": 1, "grounded": true}]}', 3) == [
            True,
            None,
            None,
        ]

    def test_should_read_string_ids_and_string_booleans(self):
        raw = '{"verdicts": [{"id": "1", "grounded": "false"}]}'

        assert hq.parse_verdicts(raw, 1) == [False]

    def test_should_ignore_ids_out_of_range_and_repeated_ids(self):
        raw = (
            '{"verdicts": [{"id": 0, "grounded": true}, {"id": 1, "grounded": false},'
            ' {"id": 1, "grounded": true}, {"id": 9, "grounded": true}]}'
        )

        assert hq.parse_verdicts(raw, 2) == [False, None]

    @pytest.mark.parametrize("raw", ["", "not json", '{"grounded": true}', '{"verdicts": "x"}'])
    def test_should_judge_nothing_when_the_reply_is_garbled(self, raw: str):
        assert hq.parse_verdicts(raw, 2) == [None, None]
