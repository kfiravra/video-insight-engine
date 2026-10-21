"""The judge's hero + quiz calls (output-quality 2.4, D13 / R10).

Both samples are judged in two background calls on the judge model and scored as
``faithfulness_hero`` / ``faithfulness_quiz``; the launch reads the assembled doc and
the cost is bounded by construction. What is sampled is in ``test_p2_4_judge_hero_quiz``.
Fake LLM service only (conftest blanks ``LLM_JUDGE_MODEL``, so the judge runs on the
service's fast tier); no network.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest
from llm_common.context import llm_feature_var

from src.config import settings
from src.routes import pipeline_faithfulness, pipeline_orchestration
from src.services.observability import langfuse_client as lc
from src.services.pipeline import faithfulness
from src.services.pipeline import faithfulness_hero_quiz as hq
from tests.test_p2_4_judge_hero_quiz import (
    _TAKEAWAYS,
    _question,
    _quiz_tab,
    _samples,
    _segments,
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
    cast(MagicMock, lc._client).trace.return_value = trace
    return trace


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
        "synthesis_dict": {"brief": "The hero rewrite wrote this brief."},
        "extraction_data": {"key_points": [{"text": "a claim long enough to be judged"}]},
        "clean_text": "we loop while low is at most high",
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


def _recording_llm(prompts: list[str]) -> MagicMock:
    """A fake judge model that keeps every prompt it was asked."""

    async def answer(prompt: str, **_kwargs: Any) -> str:
        prompts.append(prompt)
        return _reply(True, True, True)

    return _llm(answer)


def _calls_by_sample(prompts: list[str]) -> tuple[int, int]:
    """(hero calls, quiz calls) among the judge's prompts."""
    hero = sum("key takeaway" in prompt for prompt in prompts)
    return hero, len(prompts) - hero


async def _judge_run(ctx: SimpleNamespace) -> tuple[bool, list[str]]:
    """Launch the hero + quiz sample, await it; (launched, prompts the judge saw)."""
    prompts: list[str] = []
    ctx.llm_service = _recording_llm(prompts)
    task = pipeline_faithfulness._launch_hero_quiz_check(ctx)  # type: ignore[arg-type]
    if task is not None:
        await task
    return task is not None, prompts


# ─── The flags are the rollback levers (B7) ─────────────────────────────
class TestFlagOffRunsMakeNoExtraJudgeCalls:
    async def test_should_judge_the_hero_and_the_quiz_when_both_late_calls_wrote_them(self):
        _, prompts = await _judge_run(_ctx())

        assert _calls_by_sample(prompts) == (1, 1)

    async def test_should_not_judge_the_hero_when_the_hero_rewrite_did_not_write_it(self):
        # Flag off or the rewrite failed: the page shows memory's takeaways (no brief).
        _, prompts = await _judge_run(_ctx(synthesis_dict={"keyTakeaways": ["Memory's point"]}))

        assert _calls_by_sample(prompts) == (0, 1)

    async def test_should_not_judge_the_quiz_when_quiz_v2_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)

        _, prompts = await _judge_run(_ctx())

        assert _calls_by_sample(prompts) == (1, 0)

    async def test_should_launch_nothing_when_both_late_calls_are_flagged_off(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)

        launched, prompts = await _judge_run(_ctx(synthesis_dict={}))

        assert (launched, prompts) == (False, [])

    async def test_should_not_launch_the_judge_in_the_orchestration_when_both_are_flagged_off(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)
        ctx = _ctx(synthesis_dict={})
        spawned: list[Any] = []

        pipeline_orchestration._launch_judges(ctx, spawned, "assembly")  # type: ignore[arg-type]

        assert (spawned, ctx.llm_service.call_llm_fast.await_count) == ([], 0)


# ─── Launching the judges from the orchestration (B8) ───────────────────
class TestLaunchJudges:
    async def test_should_track_the_extraction_judge_once_extraction_has_data(self):
        ctx = _ctx()
        spawned: list[Any] = []

        with patch.object(faithfulness, "run_faithfulness_check", AsyncMock()) as check:
            pipeline_orchestration._launch_judges(ctx, spawned, "extraction")  # type: ignore[arg-type]
            await asyncio.gather(*spawned)

        assert (len(spawned), check.await_count) == (1, 1)

    def test_should_track_nothing_when_extraction_has_no_data(self):
        ctx = _ctx(extraction_data=None)
        spawned: list[Any] = []

        pipeline_orchestration._launch_judges(ctx, spawned, "extraction")  # type: ignore[arg-type]

        assert spawned == []

    async def test_should_track_the_hero_and_quiz_judge_once_the_doc_is_assembled(self):
        ctx = _ctx()
        spawned: list[Any] = []

        pipeline_orchestration._launch_judges(ctx, spawned, "assembly")  # type: ignore[arg-type]
        await asyncio.gather(*spawned)

        assert (len(spawned), ctx.llm_service.call_llm_fast.await_count) == (1, 2)

    async def test_should_leave_the_extraction_judge_to_its_own_stage_when_the_doc_is_assembled(
        self,
    ):
        ctx = _ctx()
        spawned: list[Any] = []

        with patch.object(faithfulness, "run_faithfulness_check", AsyncMock()) as check:
            pipeline_orchestration._launch_judges(ctx, spawned, "assembly")  # type: ignore[arg-type]
            await asyncio.gather(*spawned)

        assert check.await_count == 0


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
