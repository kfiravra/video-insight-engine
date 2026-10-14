"""A refusal as its callers see it (pipeline-1min 4.0b, review R4b) — zero spend.

Nobody may re-send a refused call to the model that refused it: not the split
extraction's lost-answer re-ask, not the chunked extractor's rate-limit path,
not the retry wrapper when the call's deadline hits the one re-send — nor
frame vision or the description analysis when theirs does. Real plumbing and
real LiteLLM; only the HTTP send is faked (``tests.llm_wire``).
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.media import frame_analyzer
from src.services.media.frame_analyzer import analyze_frames_with_vision
from src.services.pipeline import extraction_groups, pipeline_timing
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import GroupCall, SplitRun, run_group_call
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.services.video import description_analyzer
from src.services.video.description_analyzer import analyze_description
from src.utils.llm_retry import call_llm_with_retry
from tests.llm_wire import (
    anthropic_message,
    anthropic_stream,
    as_json,
    as_stream,
    offline_http,
    openai_message,
    set_fake_keys,
    text,
)

HAIKU_45 = "anthropic/claude-haiku-4-5-20251001"
HAIKU_55 = "anthropic/claude-haiku-5-5"
SONNET_46 = "anthropic/claude-sonnet-4-6"
SONNET_55 = "anthropic/claude-sonnet-5-5"
MINI = "openai/gpt-4o-mini"
LUNA = "openai/gpt-6-luna"
# A 1x1 PNG: the frame vision reads from disk and sends as a data URL.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "pfZFQAAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, fresh_litellm_callbacks: None) -> None:
    set_fake_keys(monkeypatch)


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _service() -> LLMService:
    return LLMService(LLMProvider(model=HAIKU_55, fallback_models=["openai/gpt-4o"]))


def _refusal(body: dict[str, Any]) -> httpx.Response:
    blocks = [text("I won't")]
    if body.get("stream"):
        return as_stream(anthropic_stream(blocks, stop_reason="refusal", model=body["model"]))
    return as_json(anthropic_message(blocks, stop_reason="refusal", model=body["model"]))


def _rate_limited_then_refused() -> Any:
    """Haiku 5.5: a 429 (retry-after 0), then refusals; every other model refuses."""
    sent: list[str] = []

    def reply(body: dict[str, Any]) -> httpx.Response:
        sent.append(body["model"])
        if sent.count("claude-haiku-5-5") == 1 and body["model"] == "claude-haiku-5-5":
            error = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
            return httpx.Response(429, headers={"retry-after": "0"}, json=error)
        return _refusal(body)

    return reply


def _group_call() -> tuple[GroupCall, SplitRun]:
    request = FieldRequest("food", "text1", "list", "planned", (), 10, 5, 100, False)
    group = ExtractionGroup("text1", (request,), False, 500, ())
    job = GroupJob([], ["food"], "food")
    run = SplitRun(
        llm_service=_service(),
        prompt=None,  # type: ignore[arg-type]  # build_group_messages is patched
        job=job,
        chunks=(ExtractionChunk(0, "chunk"),),
        text_groups=(group,),
        content_tags=("food",),
        modifiers=(),
    )
    return GroupCall(group, run.chunks[0], job), run


class TestRefusedWarmUp:
    async def test_should_send_exactly_two_calls_when_the_warm_up_stream_is_refused(self) -> None:
        call, run = _group_call()
        released: list[int] = []
        blocks = [text("rules"), text("chunk"), text("job")]

        with (
            patch.object(extraction_groups, "build_group_messages", return_value=blocks),
            offline_http(_refusal) as wire,
        ):
            outcome = await asyncio.create_task(
                run_group_call(call, run, on_first_chunk=lambda: released.append(1))
            )

        assert (wire.models, outcome.status, released != []) == (
            ["claude-haiku-5-5", "claude-haiku-4-5-20251001"],
            "failed",
            True,
        )


class TestRefusalAfterARateLimit:
    async def test_should_not_raise_the_earlier_rate_limit_when_the_call_ends_on_a_refusal(
        self,
    ) -> None:
        with offline_http(_rate_limited_then_refused()) as wire:
            raw = await call_llm_with_retry(
                _service(), "hi", max_retries=2, stage_name="extraction", propagate_rate_limit=True
            )

        assert (raw, wire.models) == (
            None,
            ["claude-haiku-5-5", "claude-haiku-5-5", "claude-haiku-4-5-20251001"],
        )


class TestDeadlineDuringTheReSend:
    async def test_should_not_send_a_third_call_when_the_previous_model_outlasts_the_deadline(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_refusal, delays={"claude-haiku-4-5-20251001": 5.0}) as wire:
            raw = await call_llm_with_retry(_service(), "hi", timeout=0.3, max_retries=2)

        timed_out = recorder.llm_failures[-1]
        assert (raw, wire.models, timed_out["model"], timed_out["refusedModel"]) == (
            None,
            ["claude-haiku-5-5", "claude-haiku-4-5-20251001"],
            HAIKU_45,
            HAIKU_55,
        )

    async def test_should_count_the_refusal_as_unanswered_when_the_deadline_hits_the_re_send(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_refusal, delays={"claude-haiku-4-5-20251001": 5.0}):
            await call_llm_with_retry(_service(), "hi", timeout=0.3, max_retries=2)

        counts = recorder.to_document(tabs_planned=0, tabs_assembled=0)["counts"]
        assert (counts["refusals"], counts["refusalsUnanswered"], counts["llmFailures"]) == (
            1,
            1,
            2,
        )


def _frame(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "scene_0000.png"
    path.write_bytes(_PNG)
    return {"path": str(path), "index": 0, "timestamp": 10.0, "total_score": 1.0}


def _luna_refuses(body: dict[str, Any]) -> httpx.Response:
    refusal = "I can't help" if body["model"] == "gpt-6-luna" else None
    return as_json(openai_message("{}", refusal=refusal, model=body["model"]))


class TestVisionDeadlineDuringTheReSend:
    @pytest.fixture(autouse=True)
    def _short_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A 2 s call (the stage's timeout, one frame) with no safety net on top: past
        # the refused answer, even on a cold first request.
        monkeypatch.setattr(frame_analyzer, "_SECONDS_PER_FRAME", 0.1)
        monkeypatch.setattr(frame_analyzer, "_DEADLINE_MARGIN_SECONDS", 0.0)

    async def test_should_record_the_cut_re_send_under_the_previous_model(
        self, tmp_path: Path, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_refusal, delays={"claude-sonnet-4-6": 60.0}) as wire:
            descriptions = await analyze_frames_with_vision(
                [_frame(tmp_path)], LLMProvider(model=SONNET_55), timeout=2.0
            )

        timed_out = recorder.llm_failures[-1]
        assert (descriptions, wire.models, timed_out["model"], timed_out["refusedModel"]) == (
            [],
            ["claude-sonnet-5-5", "claude-sonnet-4-6"],
            SONNET_46,
            SONNET_55,
        )

    async def test_should_not_retry_the_batch_when_the_previous_model_refuses_too(
        self, tmp_path: Path
    ) -> None:
        with offline_http(_refusal) as wire:
            descriptions = await analyze_frames_with_vision(
                [_frame(tmp_path)], LLMProvider(model=SONNET_55), timeout=2.0
            )

        assert (descriptions, wire.models) == ([], ["claude-sonnet-5-5", "claude-sonnet-4-6"])


class TestDescriptionCapDuringTheReSend:
    @pytest.fixture(autouse=True)
    def _short_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The total cap lands inside the attempt's own (25 s) deadline, after the
        # refused answer (a first OpenAI request takes ~0.6 s in-process).
        monkeypatch.setattr(description_analyzer, "DESCRIPTION_TOTAL_SECONDS", 2.0)
        monkeypatch.setattr(description_analyzer, "load_prompt", lambda _name: "{description}")

    async def test_should_record_the_cut_re_send_under_the_previous_model(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_luna_refuses, delays={"gpt-4o-mini": 60.0}) as wire:
            analysis = await analyze_description("Chapters: 0:00 intro, 2:10 the sauce.", LUNA)

        cut = recorder.llm_failures[-1]
        assert (analysis.has_content, wire.models, cut["model"], cut["refusedModel"]) == (
            False,
            ["gpt-6-luna", "gpt-4o-mini"],
            MINI,
            LUNA,
        )

    async def test_should_count_the_refusal_as_unanswered_when_the_cap_hits_the_re_send(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        with offline_http(_luna_refuses, delays={"gpt-4o-mini": 60.0}):
            await analyze_description("Chapters: 0:00 intro, 2:10 the sauce.", LUNA)

        counts = recorder.to_document(tabs_planned=0, tabs_assembled=0)["counts"]
        assert (counts["refusals"], counts["refusalsUnanswered"], counts["llmFailures"]) == (
            1,
            1,
            2,
        )
