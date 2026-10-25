"""Quiz v2 reads the extraction's prompt cache (output-quality 4.6e) — the hero's pattern.

Each call sends the split run's cached blocks first — block 1 (rules + schemas, 1 h) and
the head of its chunk (``<video>`` + that chunk's transcript + ``<video_memory>``, 5 min),
byte-identical to the group calls' — then the quiz job without a transcript, on the
extraction groups' model (the cache is per model). A chunked run's part k reads chunk
k's head; a one-chunk run's calls all read the one head; without the run's blocks the
transcript stays inline. The LLM is faked at the retry wrapper; one replay of the
reference cassette checks the wiring end to end.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.pipeline_types import EnrichmentData, PlanResult
from src.services.pipeline import extraction_groups, quiz_v2
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import head_block, prefix_block, with_group_rules
from src.services.pipeline.extraction_prompt import (
    ExtractionPrompt,
    ExtractionPromptInput,
    build_extraction_prompt,
)
from src.services.pipeline.hero_job import GroupBlocks
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2
from src.services.pipeline.quiz_v2_prompt import QuizV2Prompt, build_quiz_v2_prompts
from src.services.pipeline.quote_verify import QuoteIndex
from tests.replay.cassette import load_cassette
from tests.replay.conftest import REFERENCE_VIDEO
from tests.replay.driver import run_replay
from tests.replay.quiz_synth import request_text, transcript_lines

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
_SONNET_55 = "anthropic/claude-sonnet-5-5"
_TEN_MIN, _THIRTY_MIN = 600, 1800
_SEGMENTS = [
    {"start": 30, "text": "A card at 24% costs you more than any index fund returns at 7%."},
    {"start": 240, "text": "Keep one month of expenses in cash so a surprise never hits the card."},
    {"start": 420, "text": "Then, and only then, the same dollar goes to the index fund."},
    {"start": 700, "text": "Rebalance once a year, never after a bad week in the market."},
    {"start": 1500, "text": "Automate the transfer so the decision is made only once."},
]


def _lines(segments: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{s['start'] // 60}:{s['start'] % 60:02d}] {s['text']}" for s in segments)


_TRANSCRIPT = _lines(_SEGMENTS)
_ONE_CHUNK = (ExtractionChunk(0, _TRANSCRIPT),)
_TWO_CHUNKS = (
    ExtractionChunk(0, _lines(_SEGMENTS[:2]), end_s=300, video_end_s=_TEN_MIN),
    ExtractionChunk(1, _lines(_SEGMENTS[2:3]), start_s=300, video_end_s=_TEN_MIN),
)
_TWO_WINDOWS = ((0, 300), (300, _TEN_MIN))
_TABS = [{"id": "ideas", "label": "Key Ideas", "component": "spot_explorer"}]
_MEMORY = "<video_memory>\noutline: 0:00 Opening — the card first\n</video_memory>"


def _ideas(*stamps: int) -> dict[str, Any]:
    ideas = [
        {"claim": f"Idea at {stamp}", "kind": "argument", "timestamp": stamp} for stamp in stamps
    ]
    return {"learning": {"keyIdeas": ideas}}


def _prompt() -> ExtractionPrompt:
    return with_group_rules(
        build_extraction_prompt(
            ExtractionPromptInput(
                ["learning"],
                [],
                "quality rules text",
                primary_tag="learning",
                title="Pay the card first",
                duration_seconds=_TEN_MIN,
                tabs=_TABS,
                video_memory=_MEMORY,
            )
        )
    )


_PROMPT = _prompt()


def _request(
    duration_s: float = _TEN_MIN,
    chunks: tuple[ExtractionChunk, ...] | None = _ONE_CHUNK,
    **overrides: Any,
) -> QuizV2Request:
    fields: dict[str, Any] = {
        "primary_tag": "learning",
        "title": "Pay the card first",
        "duration_s": duration_s,
        "transcript": _TRANSCRIPT,
        "extraction": _ideas(30, 240),
        "hero": "title: Pay the card first",
        "blocks": GroupBlocks(_PROMPT, chunks) if chunks is not None else None,
    }
    fields.update(overrides)
    return QuizV2Request(**fields)


def _content(call: QuizV2Prompt) -> list[dict[str, Any]]:
    content = call.content
    assert isinstance(content, list)
    return [dict(block) for block in content]


def _heads(calls: list[QuizV2Prompt]) -> list[dict[str, Any]]:
    return [_content(call)[1] for call in calls]


def _reply() -> str:
    quiz = [
        {
            "idea": f"K{n + 1}",
            "question": f"Why does the speaker say this (#{n})?",
            "options": ["Interest beats returns", "Returns beat interest", "Cash beats both", "No"],
            "correctIndex": 0,
            "explanation": "The card costs more than the fund earns.",
            "quote": segment["text"],
            "timestamp": f"{segment['start'] // 60}:{segment['start'] % 60:02d}",
        }
        for n, segment in enumerate(_SEGMENTS[:3])
    ]
    return json.dumps({"quiz": quiz})


async def _quiz(*replies: str) -> AsyncMock:
    llm = AsyncMock(side_effect=list(replies))
    with patch(_PATCH_LLM, new=llm):
        await enrich_quiz_v2(MagicMock(), _request(), QuoteIndex.build(_SEGMENTS, None))
    return llm


# ─── The request ───


class TestOneChunkRun:
    def test_should_send_blocks_1_and_2_byte_identical_to_the_group_calls(self) -> None:
        (call,) = build_quiz_v2_prompts(_request())

        assert _content(call)[:2] == [prefix_block(_PROMPT), head_block(_PROMPT, _ONE_CHUNK[0])]

    def test_should_order_the_1h_breakpoint_before_the_5m_one_and_leave_the_job_uncached(
        self,
    ) -> None:
        (call,) = build_quiz_v2_prompts(_request())

        controls = [block.get("cache_control") for block in _content(call)]
        assert controls == [{"type": "ephemeral", "ttl": "1h"}, {"type": "ephemeral"}, None]

    def test_should_leave_the_transcript_out_of_the_job_when_the_head_carries_it(self) -> None:
        (call,) = build_quiz_v2_prompts(_request())

        job = _content(call)[2]["text"]
        assert _SEGMENTS[0]["text"] not in job and "<transcript>\n" not in job

    def test_should_read_the_one_head_from_every_third_when_written_by_stretch(self) -> None:
        calls = build_quiz_v2_prompts(_request(_THIRTY_MIN, extraction=_ideas(60, 700, 1500)))

        expected = head_block(_PROMPT, _ONE_CHUNK[0])
        assert len(calls) == 3 and _heads(calls) == [expected] * 3

    def test_should_keep_each_thirds_range_in_its_job_when_written_by_stretch(self) -> None:
        calls = build_quiz_v2_prompts(_request(_THIRTY_MIN, extraction=_ideas(60, 700, 1500)))

        jobs = [_content(call)[2]["text"] for call in calls]
        assert all("inside this stretch of the video" in job for job in jobs)


class TestChunkedRun:
    def test_should_read_each_parts_own_chunk_head_when_the_run_is_chunked(self) -> None:
        request = _request(chunks=_TWO_CHUNKS, windows=_TWO_WINDOWS, extraction=_ideas(30, 420))

        calls = build_quiz_v2_prompts(request)

        assert _heads(calls) == [head_block(_PROMPT, chunk) for chunk in _TWO_CHUNKS]

    def test_should_send_the_transcript_inline_when_a_chunked_run_has_no_windows(self) -> None:
        (call,) = build_quiz_v2_prompts(_request(chunks=_TWO_CHUNKS))

        assert call.content == call.text and "<transcript>" in call.text


class TestNoPrefix:
    def test_should_send_one_inline_message_when_the_run_has_no_blocks(self) -> None:
        (call,) = build_quiz_v2_prompts(_request(chunks=None))

        assert call.content == call.text and _SEGMENTS[0]["text"] in call.text

    def test_should_send_the_transcript_inline_when_the_template_lost_its_sections(
        self,
    ) -> None:
        broken = GroupBlocks(ExtractionPrompt("", "", "tail"), _ONE_CHUNK)

        (call,) = build_quiz_v2_prompts(_request(blocks=broken))

        assert call.content == call.text and "<transcript>" in call.text


# ─── The call ───


class TestTheCall:
    async def test_should_send_the_cached_blocks_then_the_job_to_the_extraction_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", _SONNET_55)

        llm = await _quiz(_reply())

        call = llm.await_args_list[-1]
        prompt, model = call.args[1], call.kwargs["model_override"]
        assert (prompt[:2], model) == (
            [prefix_block(_PROMPT), head_block(_PROMPT, _ONE_CHUNK[0])],
            _SONNET_55,
        )

    async def test_should_ask_again_over_the_same_cached_blocks_when_no_question_parsed(
        self,
    ) -> None:
        llm = await _quiz("no json here", _reply())

        first, second = (call.args[1] for call in llm.await_args_list)
        assert first == second and first[0].get("cache_control")


# ─── The phase hands the run's blocks on ───


def _ctx() -> Any:
    plan = PlanResult.model_validate(
        {
            "primaryTag": "learning",
            "contentTags": ["learning"],
            "tabs": [{"id": "quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"}],
        }
    )
    return SimpleNamespace(
        video_summary_id="vsid",
        plan_result=plan,
        content_format="lecture",
        extraction_data=_ideas(30),
        video_memory="",
        enrichment_data=None,
        llm_service=MagicMock(),
        quiz_task=None,
        video_data=SimpleNamespace(duration=_TEN_MIN, title="t"),
        memory=None,
        prompt_transcript=_TRANSCRIPT,
    )


async def _requested(ctx: Any, run: Any = None) -> QuizV2Request:
    v2 = AsyncMock(return_value=EnrichmentData(quiz=[]))
    with patch.object(enrichment_phase, "enrich_quiz_v2", v2):
        if run is not None:
            enrichment_phase.start_quiz_task(ctx, ctx.extraction_data, run)
        _ = [event async for event in enrichment_phase.run_phase_enrichment(ctx)]
    return v2.await_args_list[-1].args[1]


class TestPhaseWiring:
    @pytest.fixture(autouse=True)
    def _quiz_v2_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)

    async def test_should_hand_the_quiz_the_split_runs_blocks_when_its_groups_landed(
        self,
    ) -> None:
        run = SimpleNamespace(prompt=_PROMPT, chunks=_ONE_CHUNK)

        request = await _requested(_ctx(), run)

        assert request.blocks == GroupBlocks(_PROMPT, _ONE_CHUNK)

    async def test_should_hand_the_late_groups_own_call_no_blocks(self) -> None:
        request = await _requested(_ctx())

        assert request.blocks is None


# ─── The replay harness ───


class TestReplaySynth:
    def test_should_read_the_transcript_from_the_cached_head_when_the_call_reads_the_cache(
        self,
    ) -> None:
        (call,) = build_quiz_v2_prompts(_request())

        lines = transcript_lines(request_text([{"role": "user", "content": call.content}]))

        assert [seconds for seconds, _ in lines] == [s["start"] for s in _SEGMENTS]


@pytest.fixture(scope="module")
def replayed() -> tuple[list[Any], list[Any]]:
    """The reference cassette replayed: every quiz call's prompt, every group call's."""
    quiz_prompts: list[Any] = []
    group_prompts: list[Any] = []
    call_quiz, build_group = quiz_v2._call_quiz_v2_llm, extraction_groups.build_group_messages

    async def spy_call(llm_service: Any, prompt: Any, deadline: float) -> str | None:
        quiz_prompts.append(prompt)
        return await call_quiz(llm_service, prompt, deadline)

    def spy_build(*args: Any, **kwargs: Any) -> Any:
        group_prompts.append(build_group(*args, **kwargs))
        return group_prompts[-1]

    with (
        patch.object(quiz_v2, "_call_quiz_v2_llm", spy_call),
        patch.object(extraction_groups, "build_group_messages", spy_build),
    ):
        asyncio.run(run_replay(load_cassette(REFERENCE_VIDEO), speed=0))
    return quiz_prompts, group_prompts


@pytest.mark.skipif(not settings.EXTRACTION_PARALLEL, reason="the cache is the split path's")
class TestReplay:
    def test_should_send_every_quiz_call_the_prefix_the_group_calls_wrote_when_replayed(
        self, replayed: tuple[list[Any], list[Any]]
    ) -> None:
        quiz_prompts, group_prompts = replayed

        prefixes = [prompt[:2] for prompt in quiz_prompts]
        assert quiz_prompts and prefixes == [group_prompts[0][:2]] * len(quiz_prompts)
