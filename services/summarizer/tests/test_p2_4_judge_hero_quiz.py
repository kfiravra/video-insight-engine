"""The judge's hero + quiz sample (output-quality 2.4, D13 / R10).

The hero's takeaways (each closing with the ``[m:ss]`` it cites) and two quiz
questions are judged against the transcript window around their stamps, in two
background calls on the judge model, scored as ``faithfulness_hero`` and
``faithfulness_quiz``. Fake LLM service only (conftest blanks ``LLM_JUDGE_MODEL``,
so the judge runs on the service's fast tier); no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest
from llm_common.context import llm_feature_var

from src.routes import pipeline_faithfulness
from src.services.observability import langfuse_client as lc
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


def _reply(*verdicts: bool) -> str:
    rows = [{"id": i, "grounded": v, "evidence": ""} for i, v in enumerate(verdicts, start=1)]
    return json.dumps({"verdicts": rows})


def _llm(side_effect: Any) -> MagicMock:
    """A fake LLM service; both tiers behave the same."""
    service = MagicMock()
    service.call_llm_fast = AsyncMock(side_effect=side_effect)
    service.call_llm = AsyncMock(side_effect=side_effect)
    service.model = "anthropic/claude-sonnet-5-5"
    service.fast_model = "openai/gpt-4o-mini"
    return service


def _judge_llm(hero_reply: str, quiz_reply: str) -> MagicMock:
    """A fake LLM service answering each call by which prompt it got."""

    async def answer(prompt: str, **_kwargs: Any) -> str:
        return hero_reply if "key takeaway" in prompt else quiz_reply

    return _llm(answer)


@pytest.fixture(autouse=True)
def _reset_observability(monkeypatch: pytest.MonkeyPatch):
    lc._reset_for_tests()
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", None, raising=False)
    yield
    lc._reset_for_tests()


@pytest.fixture
def fake_trace(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A Langfuse client whose pipeline trace records ``score`` calls."""
    monkeypatch.setattr(lc, "Langfuse", MagicMock())
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
    lc.init_langfuse()
    trace = MagicMock()
    lc._client.trace.return_value = trace
    return trace


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


# ─── The score payload ──────────────────────────────────────────────────
def _both_samples() -> hq.HeroQuizSamples:
    samples = _samples(
        {"keyTakeaways": [*_TAKEAWAYS, "An unstamped takeaway"]},
        [_quiz_tab([_question(300), _question(310, text="Who said it?")])],
    )
    assert samples is not None
    return samples


class TestScorePayload:
    async def test_should_log_one_named_score_per_sample_when_both_are_judged(
        self, fake_trace: MagicMock
    ):
        llm = _judge_llm(_reply(True, False, True), _reply(True, True))

        async with lc.pipeline_trace("vid123"):
            await hq.run_hero_quiz_check(llm_service=llm, samples=_both_samples())

        scores = {c.kwargs["name"]: c.kwargs["value"] for c in fake_trace.score.call_args_list}
        assert scores == {"faithfulness_hero": pytest.approx(0.667), "faithfulness_quiz": 1.0}

    async def test_should_say_what_was_judged_in_the_hero_score_comment(
        self, fake_trace: MagicMock
    ):
        llm = _judge_llm(_reply(True, False, True), _reply(True, True))

        async with lc.pipeline_trace("vid123"):
            await hq.run_hero_quiz_check(llm_service=llm, samples=_both_samples())

        comments = {c.kwargs["name"]: c.kwargs["comment"] for c in fake_trace.score.call_args_list}
        assert comments["faithfulness_hero"] == (
            "2/3 takeaways grounded at their stamps (0 judge errors, 1 unstamped)"
        )

    async def test_should_score_only_the_usable_verdicts_when_the_judge_leaves_an_item_out(
        self, fake_trace: MagicMock
    ):
        llm = _judge_llm(_reply(True, False, True), '{"verdicts": [{"id": 2, "grounded": false}]}')

        async with lc.pipeline_trace("vid123"):
            reports = await hq.run_hero_quiz_check(llm_service=llm, samples=_both_samples())

        quiz = reports["faithfulness_quiz"]
        assert (quiz.sample_size, quiz.grounded_count, quiz.score) == (1, 0, 0.0)

    async def test_should_leave_the_extraction_score_name_to_the_extraction_sample(
        self, fake_trace: MagicMock
    ):
        llm = _judge_llm(_reply(True, True, True), _reply(True, True))

        async with lc.pipeline_trace("vid123"):
            await hq.run_hero_quiz_check(llm_service=llm, samples=_both_samples())

        names = [c.kwargs["name"] for c in fake_trace.score.call_args_list]
        assert "faithfulness" not in names

    async def test_should_log_no_score_when_the_judge_call_fails(self, fake_trace: MagicMock):
        llm = _llm(RuntimeError("boom"))

        async with lc.pipeline_trace("vid123"):
            reports = await hq.run_hero_quiz_check(llm_service=llm, samples=_both_samples())

        assert (reports, fake_trace.score.call_count) == ({}, 0)

    async def test_should_make_one_attempt_per_call_when_the_judge_fails_transiently(self):
        # OSError is a transient LLM error: a retry budget would send it again.
        llm = _llm(OSError("network down"))

        await hq.run_hero_quiz_check(llm_service=llm, samples=_both_samples())

        assert llm.call_llm_fast.await_count + llm.call_llm.await_count == 2


# ─── The launch from the pipeline ───────────────────────────────────────
def _ctx(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "youtube_id": "vid123",
        "row_deleted": False,
        "assembled_meta": {"keyTakeaways": _TAKEAWAYS},
        "assembled_tabs": [_quiz_tab([_question(300)])],
        "prompt_segments": _segments(),
        "transcript_data": None,
        "visual_annotations": "",
        "source_language_code": None,
        "llm_service": _judge_llm(_reply(True, True, True), _reply(True)),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestLaunch:
    async def test_should_make_the_two_judge_calls_in_one_background_task(self):
        ctx = _ctx()

        task = pipeline_faithfulness._launch_hero_quiz_check(ctx)  # type: ignore[arg-type]
        assert task is not None
        await task

        assert ctx.llm_service.call_llm_fast.await_count == 2

    async def test_should_attribute_the_judge_calls_to_the_faithfulness_feature(self):
        seen: list[str | None] = []

        async def answer(prompt: str, **_kwargs: Any) -> str:
            seen.append(llm_feature_var.get())
            return _reply(True, True, True)

        ctx = _ctx()
        ctx.llm_service.call_llm_fast = AsyncMock(side_effect=answer)
        token = llm_feature_var.set("summarize:assembly")
        try:
            task = pipeline_faithfulness._launch_hero_quiz_check(ctx)  # type: ignore[arg-type]
            assert task is not None
            await task
        finally:
            llm_feature_var.reset(token)

        assert seen == ["summarize:faithfulness", "summarize:faithfulness"]

    async def test_should_read_the_fetched_segments_when_the_run_has_no_prompt_segments(self):
        ctx = _ctx(prompt_segments=[], transcript_data=SimpleNamespace(segments=_segments()))

        task = pipeline_faithfulness._launch_hero_quiz_check(ctx)  # type: ignore[arg-type]
        assert task is not None
        await task

        assert ctx.llm_service.call_llm_fast.await_count == 2

    def test_should_not_launch_when_a_purge_deleted_the_row_mid_run(self):
        ctx = _ctx(row_deleted=True)

        assert pipeline_faithfulness._launch_hero_quiz_check(ctx) is None  # type: ignore[arg-type]

    def test_should_not_launch_when_the_doc_has_nothing_to_judge(self):
        ctx = _ctx(assembled_meta=None, assembled_tabs=None)

        assert pipeline_faithfulness._launch_hero_quiz_check(ctx) is None  # type: ignore[arg-type]

    def test_should_not_launch_or_raise_when_the_transcript_is_malformed(self):
        ctx = _ctx(prompt_segments=[{"text": "a line", "start": "not a number"}])

        assert pipeline_faithfulness._launch_hero_quiz_check(ctx) is None  # type: ignore[arg-type]


# ─── Cost, bounded by construction ──────────────────────────────────────
_HUGE = "word " * 2_000


def _oversized_samples() -> hq.HeroQuizSamples:
    """Every input past its cap: 12 takeaways of 10 K chars, a 200-char segment every
    second, a dense screen, 8 quiz questions of 10 K chars with 10 options of 10 K."""
    takeaways = [f"{_HUGE} [{i}:00]" for i in range(1, 13)]
    segments = [{"text": f"{i} " + "x" * 200, "start": i} for i in range(0, 1200)]
    screen = "\n".join(f"[{s // 60}:{s % 60:02d}] {'y' * 300}" for s in range(0, 1200, 2))
    question = {
        "question": _HUGE,
        "options": [_HUGE] * 10,
        "correctIndex": 3,
        "timestamp": 600,
    }
    samples = hq.hero_quiz_samples(
        youtube_id="vid123",
        meta={"keyTakeaways": takeaways},
        tabs=[_quiz_tab([question] * 8)],
        evidence=hq.EvidenceSource.build(
            segments, f"<visual_annotations>\n{screen}\n</visual_annotations>"
        ),
        sample_rate=0.2,
    )
    assert samples is not None
    return samples


class TestCostBound:
    def test_should_keep_the_hero_prompt_under_its_ceiling_when_every_input_is_oversized(self):
        assert len(_oversized_samples().hero.prompt()) <= hq.HERO_PROMPT_MAX_CHARS

    def test_should_keep_the_quiz_prompt_under_its_ceiling_when_every_input_is_oversized(self):
        assert len(_oversized_samples().quiz.prompt()) <= hq.QUIZ_PROMPT_MAX_CHARS

    def test_should_cost_at_most_three_tenths_of_a_cent_a_run_at_two_characters_a_token(self):
        luna = litellm.model_cost["gpt-6-luna"]
        prompt_tokens = (hq.HERO_PROMPT_MAX_CHARS + hq.QUIZ_PROMPT_MAX_CHARS) / 2
        output_tokens = hq.HERO_MAX_TOKENS + hq.QUIZ_MAX_TOKENS

        worst = (
            prompt_tokens * luna["input_cost_per_token"]
            + output_tokens * luna["output_cost_per_token"]
        )

        assert worst <= 0.003

    async def test_should_send_each_call_its_output_cap(self):
        llm = _judge_llm(_reply(True), _reply(True))

        await hq.run_hero_quiz_check(llm_service=llm, samples=_oversized_samples())

        caps = sorted(c.kwargs["max_tokens"] for c in llm.call_llm_fast.call_args_list)
        assert caps == sorted([hq.HERO_MAX_TOKENS, hq.QUIZ_MAX_TOKENS])
