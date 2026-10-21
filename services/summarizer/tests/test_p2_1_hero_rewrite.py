"""The hero rewrite call (output-quality 2.1): request, cache blocks, retry, fallback, meta."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.config import Settings, settings
from src.services.llm_model_rules import LLMRefusalError
from src.services.pipeline import hero_rewrite
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import head_block, prefix_block, with_group_rules
from src.services.pipeline.extraction_prompt import ExtractionPromptInput, build_extraction_prompt
from src.services.pipeline.hero_rewrite import (
    GroupBlocks,
    HeroRequest,
    hero_meta,
    hero_request,
    reading_minutes,
    run_hero_rewrite,
)

_MEMORY = "<video_memory>\noutline: 0:00 Opening — the Kodak Brownie\n</video_memory>"
_TABS = [
    {"id": "overview", "label": "Overview", "component": "overview"},
    {"id": "ideas", "label": "💡 Key Ideas", "component": "spot_explorer"},
    {"id": "moments", "label": "🎬 Moments", "component": "moment_track"},
]
_EXTRACTION: dict[str, Any] = {
    "learning": {
        "summary": "Alice argues 37signals went pencils down in 2025 and wrote 150,000 lines.",
        "keyIdeas": [
            {
                "claim": "Hand-written code no longer pays",
                "quote": "It no longer pays",
                "timestamp": 60,
            },
            {"claim": "Every app needs a CLI by next Friday", "timestamp": 245},
            {"claim": "Output grew to 150,000 lines in a month", "timestamp": 400},
        ],
        "timestamps": [
            {"seconds": 30, "time": "0:30", "label": "Opening", "description": "The Brownie."},
            {"seconds": 500, "time": "8:20", "label": "Closing call", "description": "Optimism."},
        ],
    }
}
_GOOD: dict[str, Any] = {
    "tldr": "Hand-written code stopped paying in 2025, so let agents write it.",
    "brief": (
        "Alice opens on the Kodak Brownie and the painters it displaced. "
        "She argues that 37signals went pencils down in 2025. "
        "Her output grew to 150,000 lines in a month. "
        "The close asks every app for a CLI."
    ),
    "keyTakeaways": [
        {"text": "Hand-written code no longer pays.", "ref": "K1"},
        {"text": "Ship a CLI for your app by next Friday.", "ref": "K2"},
        {"text": "Agents grew one programmer's output to 150,000 lines.", "ref": "K3"},
    ],
    "worthItFor": "Engineering leads deciding how far to let agents write their code.",
    "skipIf": "You want a how-to: no code is written on stage.",
    "seoDescription": "Alice on why hand-written code stopped paying.",
}


def _prompt() -> Any:
    return with_group_rules(
        build_extraction_prompt(
            ExtractionPromptInput(
                ["learning"],
                [],
                "quality rules text",
                primary_tag="learning",
                title="Alice on agents",
                duration_seconds=600,
                tabs=_TABS,
                video_memory=_MEMORY,
            )
        )
    )


_CHUNK = ExtractionChunk(0, "[0:00] hello [1:00] it no longer pays")
_CHUNKS = (ExtractionChunk(0, "[0:00] first half"), ExtractionChunk(1, "[5:00] second half"))


def _request(*, chunks: tuple[ExtractionChunk, ...] | None = (_CHUNK,)) -> HeroRequest:
    return HeroRequest(
        title="Alice on agents",
        duration=600,
        extraction=_EXTRACTION,
        tab_labels=["💡 Key Ideas", "🎬 Moments"],
        video_memory=_MEMORY,
        blocks=GroupBlocks(_prompt(), chunks) if chunks is not None else None,
    )


def _answer(**changes: Any) -> str:
    return json.dumps({**_GOOD, **changes})


@pytest.fixture
def llm() -> Iterator[AsyncMock]:
    """``call_llm_with_retry`` faked: replies in order; records the feature per call."""
    features: list[str] = []

    async def answer(*_args: Any, **_kwargs: Any) -> str | None:
        features.append(llm_feature_var.get())
        reply = answer.replies.pop(0) if answer.replies else _answer()  # type: ignore[attr-defined]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    answer.replies = []  # type: ignore[attr-defined]
    mock = AsyncMock(side_effect=answer)
    mock.replies = answer.replies  # type: ignore[attr-defined]
    mock.features = features  # type: ignore[attr-defined]
    with patch.object(hero_rewrite, "call_llm_with_retry", mock):
        yield mock


def _blocks(mock: AsyncMock, call: int = 0) -> list[dict[str, Any]]:
    return mock.await_args_list[call].args[1]


class TestRequestShape:
    async def test_should_send_blocks_1_and_2_byte_identical_to_the_group_calls_when_one_chunk(
        self, llm: AsyncMock
    ) -> None:
        request = _request()
        await run_hero_rewrite(object(), request)  # type: ignore[arg-type]

        sent = _blocks(llm)
        prompt = request.blocks.prompt  # type: ignore[union-attr]
        assert sent[:2] == [prefix_block(prompt), head_block(prompt, _CHUNK)]

    async def test_should_order_the_1h_breakpoint_before_the_5m_one_and_leave_the_job_uncached(
        self, llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        markers = [block.get("cache_control") for block in _blocks(llm)]
        assert markers == [{"type": "ephemeral", "ttl": "1h"}, {"type": "ephemeral"}, None]

    async def test_should_read_block_1_only_and_carry_video_and_memory_when_the_run_is_chunked(
        self, llm: AsyncMock
    ) -> None:
        request = _request(chunks=_CHUNKS)
        await run_hero_rewrite(object(), request)  # type: ignore[arg-type]

        sent = _blocks(llm)
        job = sent[1]["text"]
        prompt = request.blocks.prompt  # type: ignore[union-attr]
        carried = ("Title: Alice on agents" in job, _MEMORY in job)
        assert (len(sent), sent[0], carried) == (2, prefix_block(prompt), (True, True))

    async def test_should_leave_video_and_memory_to_block_2_when_it_is_read(
        self, llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        job = _blocks(llm)[-1]["text"]
        assert ("Title: Alice on agents" in job, _MEMORY in job) == (False, False)

    async def test_should_send_the_job_alone_without_cache_markers_when_extraction_is_not_split(
        self, llm: AsyncMock
    ) -> None:
        result = await run_hero_rewrite(object(), _request(chunks=None))  # type: ignore[arg-type]

        sent = _blocks(llm)
        assert (len(sent), "cache_control" in sent[0], result is not None) == (1, False, True)

    async def test_should_call_the_extraction_model_as_json_with_no_system_prompt(
        self, llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        kwargs = llm.await_args_list[0].kwargs
        sent = {k: kwargs.get(k) for k in ("stage_name", "json_mode", "max_retries")}
        assert (sent, "system_prompt" in kwargs, kwargs["model_override"]) == (
            {"stage_name": "hero_rewrite", "json_mode": True, "max_retries": 0},
            False,
            settings.get_stage_model("extraction"),
        )

    async def test_should_tag_the_call_with_the_hero_rewrite_feature(self, llm: AsyncMock) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert llm.features == ["summarize:hero_rewrite"]  # type: ignore[attr-defined]

    async def test_should_list_the_tab_labels_without_emoji_in_the_job(
        self, llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert "- Key Ideas\n- Moments" in _blocks(llm)[-1]["text"]


class TestStageModel:
    def test_should_route_hero_rewrite_to_the_extraction_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_EXTRACTION_MODEL", "anthropic/claude-sonnet-5-5")
        monkeypatch.setenv("LLM_MEMORY_MODEL", "anthropic/claude-haiku-5-5")

        assert Settings().get_stage_model("hero_rewrite") == "anthropic/claude-sonnet-5-5"


class TestParsing:
    async def test_should_copy_each_takeaways_stamp_from_the_item_it_cites(
        self, llm: AsyncMock
    ) -> None:
        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert result is not None
        assert [t.timestamp for t in result.key_takeaways] == [60, 245, 400]

    async def test_should_read_an_answer_wrapped_in_a_markdown_fence(self, llm: AsyncMock) -> None:
        llm.replies.append(f"```json\n{_answer()}\n```")  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert result is not None and result.tldr == _GOOD["tldr"]

    async def test_should_accept_a_transcript_clock_when_a_takeaway_cites_no_id(
        self, llm: AsyncMock
    ) -> None:
        takeaways = [
            *_GOOD["keyTakeaways"][:2],
            {"text": "Optimism is the play.", "timestamp": "8:00"},
        ]
        llm.replies.append(_answer(keyTakeaways=takeaways))  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert result is not None and result.key_takeaways[2].timestamp == 480


class TestRetry:
    async def test_should_retry_once_carrying_the_failed_checks(self, llm: AsyncMock) -> None:
        long_line = "Engineering leads " + "and their teams " * 8
        llm.replies.extend([_answer(worthItFor=long_line), _answer()])  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        retry_job = _blocks(llm, 1)[-1]["text"]
        assert (llm.await_count, "worthItFor has 26 words" in retry_job) == (2, True)
        assert result is not None and result.worth_it_for == _GOOD["worthItFor"]

    async def test_should_keep_blocks_1_and_2_identical_on_the_retry(self, llm: AsyncMock) -> None:
        llm.replies.extend(["not json", _answer()])  # type: ignore[attr-defined]

        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert _blocks(llm, 0)[:2] == _blocks(llm, 1)[:2]

    async def test_should_tell_the_retry_the_answer_was_not_json(self, llm: AsyncMock) -> None:
        llm.replies.extend(["sorry, no", _answer()])  # type: ignore[attr-defined]

        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert "was not a JSON object" in _blocks(llm, 1)[-1]["text"]

    async def test_should_repeat_the_request_as_it_was_after_a_timeout(
        self, llm: AsyncMock
    ) -> None:
        llm.replies.extend([None, _answer()])  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert (_blocks(llm, 0) == _blocks(llm, 1), result is not None) == (True, True)

    async def test_should_not_retry_a_refusal(self, llm: AsyncMock) -> None:
        llm.replies.append(LLMRefusalError("anthropic/claude-sonnet-5-5", None))  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert (result, llm.await_count) == (None, 1)


class TestFallback:
    async def test_should_drop_only_a_failing_optional_line_after_the_retry(
        self, llm: AsyncMock
    ) -> None:
        judging = "Fans of the host, who rambles but is a great presenter."
        llm.replies.extend([_answer(skipIf=judging), _answer(skipIf=judging)])  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert result is not None
        assert (result.skip_if, result.worth_it_for) == ("", _GOOD["worthItFor"])

    async def test_should_return_none_when_the_takeaway_set_fails_twice(
        self, llm: AsyncMock
    ) -> None:
        short = _GOOD["keyTakeaways"][:2]
        llm.replies.extend([_answer(keyTakeaways=short), _answer(keyTakeaways=short)])  # type: ignore[attr-defined]

        assert await run_hero_rewrite(object(), _request()) is None  # type: ignore[arg-type]

    async def test_should_return_none_when_the_tldr_states_a_number_not_in_the_video(
        self, llm: AsyncMock
    ) -> None:
        tldr = "Since 1946, hand-written code stopped paying."
        llm.replies.extend([_answer(tldr=tldr), _answer(tldr=tldr)])  # type: ignore[attr-defined]

        assert await run_hero_rewrite(object(), _request()) is None  # type: ignore[arg-type]

    async def test_should_keep_the_first_answer_when_the_retry_brings_nothing(
        self, llm: AsyncMock
    ) -> None:
        seo_with_number = "Alice in 1999 on why hand-written code stopped paying."
        llm.replies.extend([_answer(seoDescription=seo_with_number), None])  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert result is not None and result.seo_description == ""

    async def test_should_return_none_instead_of_raising_when_the_call_errors(
        self, llm: AsyncMock
    ) -> None:
        llm.replies.append(RuntimeError("provider exploded"))  # type: ignore[attr-defined]

        assert await run_hero_rewrite(object(), _request()) is None  # type: ignore[arg-type]

    async def test_should_skip_the_call_when_nothing_was_extracted(self, llm: AsyncMock) -> None:
        request = HeroRequest(title="t", duration=600, extraction={})

        assert (await run_hero_rewrite(object(), request), llm.await_count) == (None, 0)  # type: ignore[arg-type]


class TestMeta:
    async def test_should_fill_master_summary_with_the_brief_and_stamp_each_takeaway(
        self, llm: AsyncMock
    ) -> None:
        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]
        assert result is not None

        meta = hero_meta(result, minutes=4)

        assert (meta["masterSummary"], meta["keyTakeaways"][0], meta["readingMinutes"]) == (
            meta["brief"],
            {"text": "Hand-written code no longer pays.", "timestamp": 60},
            4,
        )


class TestReadingMinutes:
    def test_should_count_the_hero_and_every_tab_but_the_overview(self) -> None:
        meta = {"tldr": "one two", "brief": "three", "keyTakeaways": [{"text": "four five"}]}
        tabs = [
            {"component": "overview", "props": {"data": {"masterSummary": "x " * 5000}}},
            {"component": "spot_explorer", "label": "Ideas", "props": {"items": ["w " * 470]}},
        ]

        assert reading_minutes(meta, tabs) == 2  # 5 + 1 label + 470 words = 476 → 2 min

    def test_should_skip_urls_media_keys_and_frame_metadata(self) -> None:
        item = {"s3Key": "a b c", "frameCaption": "a b c", "frameOcr": "x " * 300}
        tabs = [{"component": "moment_track", "props": {"items": [item]}}]
        meta = {"tldr": "https://example.com/a b"}

        assert reading_minutes(meta, tabs) == 1


class TestHeroRequestFromContext:
    def test_should_take_the_planned_tab_labels_without_the_overview_and_the_runs_blocks(
        self,
    ) -> None:
        run = SimpleNamespace(prompt=_prompt(), chunks=(_CHUNK,))
        ctx = SimpleNamespace(
            video_data=SimpleNamespace(title="Alice on agents", duration=600),
            plan_result=SimpleNamespace(tabs=_TABS),
            triage=None,
            video_memory=_MEMORY,
        )

        request = hero_request(ctx, _EXTRACTION, run)  # type: ignore[arg-type]

        assert (request.tab_labels, request.reads_transcript) == (
            ["💡 Key Ideas", "🎬 Moments"],
            True,
        )

    def test_should_read_no_group_blocks_without_a_split_run(self) -> None:
        ctx = SimpleNamespace(
            video_data=SimpleNamespace(title="t", duration=600),
            plan_result=SimpleNamespace(tabs=_TABS),
            triage=None,
            video_memory="",
        )

        assert hero_request(ctx, _EXTRACTION).blocks is None  # type: ignore[arg-type]
