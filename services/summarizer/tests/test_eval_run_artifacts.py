"""Eval-user runs stay private (D25, pipeline-1min 1d.8 — summarizer half).

The API stamps ``evalRun: true`` on an eval user's ``videoSummaryCache`` row.
The run flags ``ctx.eval_run`` (and its Langfuse trace) from it, and then
writes neither the shared Redis response cache (assembly, translation) nor
the Qdrant collection — those serve every user of the video.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routes import pipeline_runner
from src.services.pipeline.phases import assembly as assembly_phase
from src.services.pipeline.phases import translation as translation_phase


@pytest.fixture(autouse=True)
def _stub_status_callbacks():
    with (
        patch.object(assembly_phase, "send_video_status_background", MagicMock()),
        patch.object(translation_phase, "send_video_status_background", MagicMock()),
    ):
        yield


def _assembly_ctx(eval_run: bool) -> SimpleNamespace:
    repo = MagicMock()
    repo.save_structured_result = MagicMock(return_value=True)
    return SimpleNamespace(
        video_data=SimpleNamespace(
            title="t", channel="c", duration=10, chapters=None, thumbnail_url="https://x/t.jpg"
        ),
        triage=SimpleNamespace(tabs=[]),
        triage_dict={},
        plan_result=None,
        extraction_data={},
        enrichment_data={},
        synthesis_dict=None,
        description_analysis=None,
        scene_frames_for_assembly=None,
        scene_frames_gallery=None,
        scene_frames_all=None,
        frame_descriptions=None,
        assembled_tabs=None,
        assembled_meta=None,
        video_summary_id="vsid",
        youtube_id="ytid",
        language="en",
        is_rtl=False,
        source_language_code=None,
        clean_text="transcript text",
        repository=repo,
        timer=MagicMock(elapsed=MagicMock(return_value=1.0)),
        transcript_data=None,
        audio_path=None,
        eval_run=eval_run,
    )


async def _assemble(ctx: SimpleNamespace) -> dict[str, Any]:
    """Run assembly with Redis + Qdrant on; returns the store mocks."""
    stores = {
        "redis": AsyncMock(return_value=True),
        "transcript": AsyncMock(),
        "output": AsyncMock(),
    }
    settings = SimpleNamespace(REDIS_ENABLED=True, QDRANT_ENABLED=True, PIPELINE_VERSION="vtest")
    with (
        patch.object(assembly_phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(assembly_phase, "settings", settings),
        patch.object(assembly_phase, "response_cache") as cache,
        patch.object(assembly_phase, "store_transcript_chunks", stores["transcript"]),
        patch.object(assembly_phase, "store_default_output_chunks", stores["output"]),
        patch("src.routes.cached_response.build_frontend_response", return_value={}),
    ):
        cache.set_response = stores["redis"]
        async for _ in assembly_phase.run_phase_assembly(ctx):  # type: ignore[arg-type]
            pass
    return stores


class TestAssembly:
    async def test_should_skip_the_shared_redis_response_for_an_eval_run(self) -> None:
        stores = await _assemble(_assembly_ctx(eval_run=True))

        stores["redis"].assert_not_called()

    async def test_should_skip_qdrant_for_an_eval_run(self) -> None:
        stores = await _assemble(_assembly_ctx(eval_run=True))

        assert (stores["transcript"].call_count, stores["output"].call_count) == (0, 0)

    async def test_should_still_save_the_eval_row_itself(self) -> None:
        ctx = _assembly_ctx(eval_run=True)

        await _assemble(ctx)

        ctx.repository.save_structured_result.assert_called_once()

    async def test_should_write_redis_and_qdrant_for_a_normal_run(self) -> None:
        stores = await _assemble(_assembly_ctx(eval_run=False))

        assert [stores[k].call_count for k in ("redis", "transcript", "output")] == [1, 1, 1]


def _translation_ctx(eval_run: bool) -> SimpleNamespace:
    return SimpleNamespace(
        source_language_code="he",
        source_language=None,
        llm_service=MagicMock(),
        assembled_tabs=[{"id": "overview"}],
        assembled_meta={"contentTags": ["podcast"]},
        video_data=SimpleNamespace(title="t", channel="c", duration=10, thumbnail_url="u"),
        youtube_id="ytid",
        eval_run=eval_run,
    )


async def _translate(ctx: SimpleNamespace) -> AsyncMock:
    translated = {"tabs": [], "meta": {}, "sourceLanguage": {"code": "he", "tabs": [], "meta": {}}}
    set_response = AsyncMock(return_value=True)
    with (
        patch.object(translation_phase, "translate_to_source", AsyncMock(return_value=translated)),
        patch.object(translation_phase, "translate_text", AsyncMock(return_value="title")),
        patch.object(translation_phase, "settings", SimpleNamespace(REDIS_ENABLED=True)),
        patch.object(translation_phase, "response_cache") as cache,
        patch("src.routes.cached_response.build_frontend_response", return_value={}),
    ):
        cache.set_response = set_response
        async for _ in translation_phase.run_phase_translation(ctx, MagicMock(), "vsid"):  # type: ignore[arg-type]
            pass
    return set_response


class TestTranslation:
    async def test_should_skip_the_shared_redis_response_for_an_eval_run(self) -> None:
        set_response = await _translate(_translation_ctx(eval_run=True))

        set_response.assert_not_called()

    async def test_should_write_the_redis_response_for_a_normal_run(self) -> None:
        set_response = await _translate(_translation_ctx(eval_run=False))

        set_response.assert_called_once()


class TestRunnerFlag:
    async def _run(self, entry: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        seen: dict[str, Any] = {}

        async def _phases(ctx: Any, *_args: object):
            seen["ctx"] = ctx
            yield "data: phases\n\n"

        trace = MagicMock()
        trace.return_value.__aenter__ = AsyncMock()
        trace.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", False),
            patch.object(pipeline_runner, "run_pipeline_phases", _phases),
            patch.object(pipeline_runner, "send_video_status", AsyncMock()),
            patch.object(pipeline_runner, "clear_override", MagicMock()),
            patch.object(pipeline_runner, "pipeline_trace", trace),
        ):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsid", entry, MagicMock(), MagicMock()
                )
            ]
        return seen["ctx"], trace.call_args.kwargs["metadata"]

    async def test_should_flag_the_context_from_the_rows_eval_stamp(self) -> None:
        ctx, _metadata = await self._run({"youtubeId": "yt1", "evalRun": True})

        assert ctx.eval_run is True

    async def test_should_mark_the_trace_of_an_eval_run(self) -> None:
        _ctx, metadata = await self._run({"youtubeId": "yt1", "evalRun": True})

        assert metadata["evalRun"] is True

    async def test_should_leave_a_normal_run_unflagged(self) -> None:
        ctx, metadata = await self._run({"youtubeId": "yt1"})

        assert (ctx.eval_run, "evalRun" in metadata) == (False, False)
