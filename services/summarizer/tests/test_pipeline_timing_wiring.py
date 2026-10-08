"""Wiring tests for ``pipeline.timing``: every recording seam lands in the
run's recorder and the runner persists the document (success and failure)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
)

from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes import pipeline_orchestration
from src.services.llm_provider import LLMProvider
from src.services.media import local_video
from src.services.pipeline import pipeline_timing
from src.services.pipeline.pipeline_helpers import run_parallel_phases, sse_event
from src.services.pipeline.pipeline_timing import PipelineTimingRecorder, start_run_timing
from src.utils.llm_retry import call_llm_with_retry

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture
def recorder() -> Iterator[PipelineTimingRecorder]:
    token = pipeline_timing._recorder_var.set(None)
    try:
        yield start_run_timing()
    finally:
        pipeline_timing._recorder_var.reset(token)


def _llm_response(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(finish_reason="stop", message=MagicMock(content=content))]
    response.usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=4,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    response.model = "claude-sonnet-4-6"
    return response


class TestProviderSeam:
    async def test_should_record_completion_with_retry_attempt(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        provider = LLMProvider(model="anthropic/claude-sonnet-4-6", fallback_models=[])
        with patch(
            "src.services.llm_provider.acompletion", AsyncMock(return_value=_llm_response())
        ):
            await provider.complete_with_messages(
                [{"role": "user", "content": "hi"}],
                span_name="plan",
                span_metadata={"attempt": 2},
            )

        assert (recorder.llm_calls[0]["span"], recorder.llm_calls[0]["attempt"]) == ("plan", 2)

    async def test_should_record_fast_model_completion(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        provider = LLMProvider(model="anthropic/claude-sonnet-4-6", fast_model="anthropic/fast")
        with patch(
            "src.services.llm_provider.acompletion", AsyncMock(return_value=_llm_response())
        ):
            await provider.complete_fast("hi", span_name="faithfulness")

        assert recorder.llm_calls[0]["model"] == "anthropic/fast"

    @pytest.mark.parametrize(
        "exc_type", [InternalServerError, APIConnectionError, BadRequestError, NotFoundError]
    )
    async def test_should_record_failure_when_provider_raises_unclassified_litellm_error(
        self, recorder: PipelineTimingRecorder, exc_type: type[Exception]
    ) -> None:
        # Regression (G0 p0.1 #1): Anthropic 500/529 and friends are not
        # litellm.APIError subclasses and used to bypass llmFailures.
        provider = LLMProvider(model="anthropic/claude-sonnet-4-6", fallback_models=[])
        error = exc_type(message="boom", llm_provider="anthropic", model="claude-sonnet-4-6")
        with (
            patch("src.services.llm_provider.acompletion", AsyncMock(side_effect=error)),
            pytest.raises(exc_type),
        ):
            await provider.complete_with_messages(
                [{"role": "user", "content": "hi"}], span_name="plan"
            )

        assert [(f["span"], f["error"]) for f in recorder.llm_failures] == [
            ("plan", exc_type.__name__)
        ]


class TestRetrySeam:
    async def test_should_record_outer_timeout_as_failure(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        service = MagicMock()
        service.model = "anthropic/claude-sonnet-4-6"
        service.call_llm = AsyncMock(side_effect=[asyncio.TimeoutError(), "{}"])

        with patch("src.utils.llm_retry.asyncio.sleep", AsyncMock()):
            await call_llm_with_retry(service, "p", stage_name="extraction", max_retries=1)

        assert [(f["span"], f["error"]) for f in recorder.llm_failures] == [
            ("extraction", "TimeoutError")
        ]


class TestRunnerPersistence:
    def _ctx(self) -> SimpleNamespace:
        return SimpleNamespace(
            youtube_id="yt1",
            clean_text="",
            frame_descriptions=None,
            scene_frames_all=None,
            transcript_data=None,
            extraction_data={},
            source_language_code=None,
            row_deleted=False,
            phase_times={},
            plan_result=object(),
            enrichment_data={"a": 1},
            triage=SimpleNamespace(tabs=[1, 2, 3]),
            assembled_tabs=[1, 2],
            transcript_ready=asyncio.Event(),
            tier_probe_task=None,
            content_format=None,
        )

    def _patches(self, assembly_events: list[str], assembly: object | None = None) -> list:
        async def _one(*_a: object, **_k: object):
            yield "data: x\n\n"

        async def _assembly(*_a: object, **_k: object):
            for event in assembly_events:
                yield event

        return [
            patch.object(pipeline_orchestration, "run_phase_metadata", _one),
            patch.object(pipeline_orchestration, "run_parallel_phases", lambda _p, _c: _one()),
            patch.object(pipeline_orchestration, "run_phase_extraction", _one),
            patch.object(pipeline_orchestration, "run_phase_enrichment", _one),
            patch.object(pipeline_orchestration, "run_phase_assembly", assembly or _assembly),
            patch.object(pipeline_orchestration, "needs_quiz", lambda *_a: False),
        ]

    async def _drive(
        self,
        ctx: SimpleNamespace,
        repository: MagicMock,
        events: list[str],
        assembly: object | None = None,
    ) -> None:
        timer = MagicMock(elapsed=MagicMock(return_value=1.0))
        patches = self._patches(events, assembly)
        for p in patches:
            p.start()
        try:
            async for _ in pipeline_orchestration.run_pipeline_phases(
                ctx, repository, "vsid", timer
            ):
                pass
        finally:
            for p in patches:
                p.stop()

    async def test_should_persist_timing_with_first_tab_milestone(self) -> None:
        repository = MagicMock()
        tab = sse_event("tab_ready", {"id": "a", "position": 0})

        await self._drive(self._ctx(), repository, [tab])

        _vsid, doc = repository.set_pipeline_timing.call_args.args
        assert "firstTabReadyMs" in doc["milestones"]

    async def test_should_persist_tab_counts(self) -> None:
        repository = MagicMock()
        tab = sse_event("tab_ready", {"id": "a", "position": 0})

        await self._drive(self._ctx(), repository, [tab])

        counts = repository.set_pipeline_timing.call_args.args[1]["counts"]
        assert (counts["tabsPlanned"], counts["tabsAssembled"], counts["tabsEmitted"]) == (3, 2, 1)

    async def test_should_persist_timing_when_a_phase_fails(self) -> None:
        repository = MagicMock()

        async def _boom(*_a: object, **_k: object):
            raise RuntimeError("assembly exploded")
            yield  # pragma: no cover

        with pytest.raises(RuntimeError):
            await self._drive(self._ctx(), repository, [], assembly=_boom)

        repository.set_pipeline_timing.assert_called_once()

    async def test_should_skip_persist_when_row_was_purged(self) -> None:
        repository = MagicMock()
        ctx = self._ctx()
        ctx.row_deleted = True

        await self._drive(ctx, repository, [])

        repository.set_pipeline_timing.assert_not_called()

    async def test_done_log_should_carry_tab_counts(self, caplog: pytest.LogCaptureFixture) -> None:
        tab = sse_event("tab_ready", {"id": "a", "position": 0})

        with caplog.at_level(logging.INFO, logger="src.routes.pipeline_orchestration"):
            await self._drive(self._ctx(), MagicMock(), [tab])

        assert "tabs planned=3 assembled=2 emitted=1" in caplog.text


class TestRepositoryWrite:
    def test_should_set_dotted_timing_without_touching_updated_at(self) -> None:
        collection = MagicMock()
        repo = MongoDBVideoRepository(MagicMock(videoSummaryCache=collection))

        repo.set_pipeline_timing("0" * 24, {"totalMs": 1})

        collection.update_one.assert_called_once_with(
            {"_id": ObjectId("0" * 24)}, {"$set": {"pipeline.timing": {"totalMs": 1}}}
        )


class TestDownloadSeam:
    async def test_720p_download_should_record_purpose_and_bytes(
        self, recorder: PipelineTimingRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_mkdtemp = local_video.tempfile.mkdtemp
        monkeypatch.setattr(
            local_video.tempfile,
            "mkdtemp",
            lambda prefix: real_mkdtemp(prefix=prefix, dir=tmp_path),
        )
        proc = MagicMock(returncode=0)
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def _spawn(*args: str, **_kwargs: object) -> MagicMock:
            Path(args[args.index("-o") + 1]).write_bytes(b"x" * 300)
            return proc

        with patch.object(local_video.asyncio, "create_subprocess_exec", side_effect=_spawn):
            result = await local_video.download_video_720p(VIDEO_ID, purpose="prefetch")
        assert result is not None
        local_video.cleanup_local_video(result[1])

        assert recorder.downloads[0] | {"startMs": 0, "wallMs": 0} == {
            "kind": "720p",
            "purpose": "prefetch",
            "startMs": 0,
            "wallMs": 0,
            "bytes": 300,
            "ok": True,
        }


class TestParallelPhaseSeam:
    async def test_should_record_each_parallel_phase(
        self, recorder: PipelineTimingRecorder
    ) -> None:
        async def run_phase_transcript(_ctx: object):
            yield "data: t\n\n"

        async def run_phase_frames(_ctx: object):
            yield "data: f\n\n"

        _ = [e async for e in run_parallel_phases([run_phase_transcript, run_phase_frames], None)]  # type: ignore[arg-type]

        assert sorted(p["name"] for p in recorder.phases) == ["frames", "transcript"]
