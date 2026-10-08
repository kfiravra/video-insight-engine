"""Tests for the pipeline entry + phase orchestration (src.routes.pipeline_runner,
src.routes.pipeline_orchestration).

Focused on the faithfulness fire-and-forget spawn and the transcriptMeta
provenance contract (single write point + run-start clears) — the rest of
the runner is exercised through integration tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, ExitStack
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode, ProcessingStatus
from src.routes import pipeline_faithfulness, pipeline_orchestration, pipeline_runner
from src.config import settings
from src.services.pipeline.pipeline_helpers import (
    TranscriptData,
    TranscriptTrail,
    run_parallel_phases,
)


def _phase_stub(label: str):
    """Build a single-event async-generator stand-in for a pipeline phase."""

    async def _gen(*_args: object, **_kwargs: object):
        yield f"data: {label}\n\n"

    return _gen


def _non_english_ctx() -> SimpleNamespace:
    """Minimal ctx for run_pipeline_phases driving the non-English path."""
    return SimpleNamespace(
        youtube_id="yt1",
        clean_text="",  # skip the visual-inject block
        frame_descriptions=None,
        scene_frames_all=None,
        transcript_data=None,
        extraction_data={},  # skip the faithfulness spawn
        source_language_code="he",
        row_deleted=False,
        phase_times={},
        plan_result=object(),
        enrichment_data={"a": 1},
        triage=SimpleNamespace(tabs=[]),
        transcript_ready=asyncio.Event(),
        tier_probe_task=None,
        content_format=None,
    )


def _patched_phases():
    """Patch every pipeline phase to a trivial stub so the orchestration —
    specifically the terminal-event ordering — can be tested in isolation."""

    async def _parallel_stub(_phases, _ctx):
        yield "data: parallel\n\n"

    return [
        patch.object(pipeline_orchestration, "run_phase_metadata", _phase_stub("metadata")),
        patch.object(pipeline_orchestration, "run_parallel_phases", _parallel_stub),
        patch.object(pipeline_orchestration, "run_phase_extraction", _phase_stub("extraction")),
        patch.object(pipeline_orchestration, "run_phase_assembly", _phase_stub("assembly")),
    ]


def _english_ctx(extraction_data: dict) -> SimpleNamespace:
    """Minimal ctx for run_pipeline_phases driving the English path."""
    return SimpleNamespace(
        youtube_id="yt1",
        clean_text="",  # skip the visual-inject block and faithfulness spawn
        frame_descriptions=None,
        scene_frames_all=None,
        transcript_data=None,
        extraction_data=extraction_data,
        source_language_code=None,
        row_deleted=False,
        phase_times={},
        plan_result=object(),
        enrichment_data={"a": 1},
        triage=SimpleNamespace(tabs=[]),
        transcript_ready=asyncio.Event(),
        tier_probe_task=None,
        content_format=None,
    )


_TAIL_NAMES = ("run_phase_extraction",)


async def _parallel_groups(ctx: SimpleNamespace) -> list[list[str]]:
    """The phases the orchestration hands the parallel runner, by name, in order."""
    groups: list[list] = []

    async def _capture_parallel(phases, _ctx):
        groups.append(list(phases))
        yield "data: parallel\n\n"

    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_parallel_phases", _capture_parallel)
        )
        names = {getattr(pipeline_orchestration, name): name for name in _TAIL_NAMES}
        _ = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx, MagicMock(), "vsid", _timer()
            )
        ]
    return [[names.get(phase, getattr(phase, "__name__", "?")) for phase in g] for g in groups]


@pytest.mark.asyncio
async def test_should_go_straight_to_assembly_after_extraction() -> None:
    """1d.1: the quiz never blocks tabs — it runs inside assembly's late group."""
    groups = await _parallel_groups(_english_ctx({}))

    assert len(groups) == 2


@pytest.mark.asyncio
async def test_should_render_visual_annotations_before_extraction_starts() -> None:
    """1c.2: extraction reads ctx.visual_annotations, rendered after the phase-2 group."""
    ctx = _english_ctx({})
    seen_at_extraction: list[str] = []

    async def _capture_parallel(phases, run_ctx):
        if phases == [pipeline_orchestration.run_phase_extraction]:
            seen_at_extraction.append(run_ctx.visual_annotations)
        yield "data: parallel\n\n"

    rendered = "<visual_annotations>\n[1:05] a whiteboard\n</visual_annotations>"
    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_parallel_phases", _capture_parallel)
        )
        stack.enter_context(
            patch.object(pipeline_orchestration, "render_visual_annotations", lambda *_a: rendered)
        )
        _ = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx,  # type: ignore[arg-type]
                MagicMock(),
                "vsid",
                _timer(),
            )
        ]

    assert seen_at_extraction == [rendered]


@pytest.mark.asyncio
async def test_should_run_extraction_through_the_heartbeat_runner() -> None:
    groups = await _parallel_groups(_english_ctx({}))

    assert groups[1] == ["run_phase_extraction"]


@pytest.mark.asyncio
async def test_should_send_heartbeats_while_extraction_is_silent() -> None:
    """1d.5: one extraction call can be silent for minutes (API gateway idle timeout)."""

    async def _slow_extraction(_ctx):
        await asyncio.sleep(0.05)
        yield "data: extraction\n\n"

    async def _phase_two(_ctx, *_args):
        yield "data: phase two\n\n"

    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        for name, value in (
            ("run_parallel_phases", run_parallel_phases),
            ("_run_phase_two", _phase_two),
            ("run_phase_extraction", _slow_extraction),
        ):
            stack.enter_context(patch.object(pipeline_orchestration, name, value))
        stack.enter_context(patch.object(settings, "SSE_HEARTBEAT_SECONDS", 0.01))
        events = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                _english_ctx({}), MagicMock(), "vsid", _timer()
            )
        ]

    extraction_at = events.index("data: extraction\n\n")
    assert any('"heartbeat"' in ev for ev in events[:extraction_at])


@pytest.mark.asyncio
async def test_done_emitted_after_translation_for_non_english() -> None:
    """For non-English videos assembly defers the terminal event; the runner
    emits done/[DONE] only AFTER the translation phase, so the FE refetch sees a
    completed doc with the sourceLanguage toggle."""
    ctx = _non_english_ctx()
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)

    async def _translation_stub(_ctx, _repo, _vsid):
        yield "data: translation\n\n"

    patches = _patched_phases()
    patches.append(
        patch("src.services.pipeline.phases.translation.run_phase_translation", _translation_stub)
    )
    for p in patches:
        p.start()
    try:
        events = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx, MagicMock(), "vsid", timer
            )
        ]
    finally:
        for p in patches:
            p.stop()

    assert any("translation" in ev for ev in events)
    assert any("[DONE]" in ev for ev in events)
    translation_idx = next(i for i, ev in enumerate(events) if "translation" in ev)
    done_idx = next(i for i, ev in enumerate(events) if "[DONE]" in ev)
    assert translation_idx < done_idx, "done must come after the translation phase"


@pytest.mark.asyncio
async def test_no_done_when_translation_raises() -> None:
    """If translation raises, the doc stays "processing" (retriable): the runner
    swallows the error as non-critical and emits NO terminal event."""
    ctx = _non_english_ctx()
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)

    async def _translation_raises(_ctx, _repo, _vsid):
        raise RuntimeError("boom")
        yield  # pragma: no cover — makes this an async generator

    patches = _patched_phases()
    patches.append(
        patch("src.services.pipeline.phases.translation.run_phase_translation", _translation_raises)
    )
    for p in patches:
        p.start()
    try:
        events = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx, MagicMock(), "vsid", timer
            )
        ]
    finally:
        for p in patches:
            p.stop()

    assert not any("[DONE]" in ev for ev in events)
    assert not any("videoSummaryId" in ev for ev in events)


@pytest.mark.asyncio
async def test_faithfulness_task_overrides_inherited_feature_var():
    """Regression: the spawned judge task must label its LLM calls as
    "summarize:faithfulness", not inherit "summarize:extraction" from the
    parent task that spawned it.

    Without this contract, every faithfulness LLM call rolls up into the
    extraction cost row in dashboards, hiding both stages' real spend.
    """
    captured: dict[str, str | None] = {"feature": None}

    async def fake_run_check(**_kwargs: object) -> None:
        # Snapshot the contextvar as observed inside the spawned task.
        captured["feature"] = llm_feature_var.get()

    ctx = MagicMock()
    ctx.extraction_data = {"key_points": [{"text": "claim long enough to be used"}]}
    ctx.clean_text = "some transcript text"
    ctx.visual_annotations = ""
    ctx.youtube_id = "abc123"
    ctx.llm_service = MagicMock()

    # Set the parent context to simulate being inside the extraction phase
    # — this is what the bug case looked like before the fix.
    llm_feature_var.set("summarize:extraction")

    with patch(
        "src.services.pipeline.faithfulness.run_faithfulness_check",
        new=fake_run_check,
    ):
        task = pipeline_faithfulness._launch_faithfulness_check(ctx)
        assert task is not None
        await task

    assert captured["feature"] == "summarize:faithfulness", (
        "Spawned task must override the inherited feature var to "
        "'summarize:faithfulness' so cost tracking attributes the LLM calls "
        "correctly. Inherited the parent's extraction label instead."
    )


@pytest.mark.asyncio
async def test_faithfulness_task_does_not_mutate_parent_feature_var():
    """The child task's contextvar set must NOT leak back to the parent.

    Python ContextVars are task-local by default — but this test pins the
    contract so a future refactor doesn't break attribution for the
    surrounding pipeline phases.
    """
    parent_token = llm_feature_var.set("summarize:extraction")
    try:
        ctx = MagicMock()
        ctx.extraction_data = {"key_points": [{"text": "claim long enough to be used"}]}
        ctx.clean_text = "transcript"
        ctx.visual_annotations = ""
        ctx.youtube_id = "abc123"
        ctx.llm_service = MagicMock()

        async def noop_run(**_kwargs: object) -> None:
            return None

        with patch(
            "src.services.pipeline.faithfulness.run_faithfulness_check",
            new=noop_run,
        ):
            task = pipeline_faithfulness._launch_faithfulness_check(ctx)
            assert task is not None
            await task

        # Parent must still see its own value after the child task completes.
        assert llm_feature_var.get() == "summarize:extraction"
    finally:
        llm_feature_var.reset(parent_token)


@pytest.mark.asyncio
async def test_launch_returns_none_when_extraction_data_missing():
    """Guard: if extraction failed, the judge has nothing to score."""
    ctx = MagicMock()
    ctx.extraction_data = {}
    ctx.clean_text = "transcript"
    ctx.youtube_id = "abc123"

    assert pipeline_faithfulness._launch_faithfulness_check(ctx) is None


@pytest.mark.asyncio
async def test_launch_returns_none_when_transcript_missing():
    """Guard: judge needs a transcript to compare claims against."""
    ctx = MagicMock()
    ctx.extraction_data = {"key_points": [{"text": "claim"}]}
    ctx.clean_text = ""
    ctx.youtube_id = "abc123"

    assert pipeline_faithfulness._launch_faithfulness_check(ctx) is None


@pytest.mark.asyncio
async def test_stream_sets_attribution_ctxvars_on_cache_hit():
    """Phase 0: the cost-tracking ctxvars (user/video/video_summary/request)
    must be set BEFORE the cache lookup, so even a Redis cache-hit run that
    makes a (rare) LLM call writes attributable `llm_usage` rows.

    Run grouping and per-user reconciliation match on these keys; if the fast
    path skipped them, cache-hit cost would be unattributable.
    """
    import structlog
    from llm_common.context import (
        llm_request_id_var,
        llm_user_id_var,
        llm_video_id_var,
        llm_video_summary_id_var,
    )

    cached = {
        "status": "completed",
        "youtubeId": "yt_cache_hit",
        "tabs": [{"id": "overview"}],
        "meta": {"contentTags": ["learning"]},
    }

    async def fake_get_response(_youtube_id: str) -> dict:
        return cached

    async def fake_cached_stream(_vsid: str, _cached: dict):
        yield "data: {}\n\n"

    captured: dict[str, str | None] = {}

    async def capture_then_stream(_vsid: str, _cached: dict):
        # Snapshot ctxvars at the point the cache-hit branch streams — they
        # must already be set by this point.
        captured["video_id"] = llm_video_id_var.get()
        captured["video_summary_id"] = llm_video_summary_id_var.get()
        captured["user_id"] = llm_user_id_var.get()
        captured["request_id"] = llm_request_id_var.get()
        async for ev in fake_cached_stream(_vsid, _cached):
            yield ev

    repository = MagicMock()
    entry = {"youtubeId": "yt_cache_hit", "userId": "user_77", "status": "processing"}

    structlog.contextvars.bind_contextvars(request_id="req_live_42")
    try:
        with (
            patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
            patch.object(pipeline_runner, "_stream_cached_structured", new=capture_then_stream),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
        ):
            events = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_55", entry, repository, MagicMock()
                )
            ]
    finally:
        structlog.contextvars.unbind_contextvars("request_id")

    assert events, "cache-hit path should stream at least one event"
    assert captured["video_id"] == "yt_cache_hit"
    assert captured["video_summary_id"] == "vsum_55"
    assert captured["user_id"] == "user_77"
    assert captured["request_id"] == "req_live_42"


@pytest.mark.asyncio
async def test_stream_reads_user_id_from_contextvars_when_entry_has_none():
    """Regression: the ``entry`` row is the cross-user ``videoSummaryCache`` doc
    and carries no per-run owner, so ``user_id`` must come from the structlog
    contextvar the worker binds from the queue payload — NOT ``entry``.

    Before the fix, ``entry.get("userId")`` was the only source, so every
    summarizer cost row landed with ``user_id=None`` and per-user reconciliation
    matched zero rows.
    """
    import structlog
    from llm_common.context import llm_user_id_var

    cached = {
        "status": "completed",
        "youtubeId": "yt_cross_user",
        "tabs": [{"id": "overview"}],
        "meta": {"contentTags": ["learning"]},
    }

    async def fake_get_response(_youtube_id: str) -> dict:
        return cached

    captured: dict[str, str | None] = {}

    async def capture_then_stream(_vsid: str, _cached: dict):
        captured["user_id"] = llm_user_id_var.get()
        yield "data: {}\n\n"

    repository = MagicMock()
    # No "userId" key on the cache doc — the cross-user case.
    entry = {"youtubeId": "yt_cross_user", "status": "processing"}

    structlog.contextvars.bind_contextvars(request_id="req_x", user_id="payload_user_99")
    try:
        with (
            patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
            patch.object(pipeline_runner, "_stream_cached_structured", new=capture_then_stream),
            patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
        ):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    "vsum_x", entry, repository, MagicMock()
                )
            ]
    finally:
        structlog.contextvars.unbind_contextvars("request_id", "user_id")

    assert captured["user_id"] == "payload_user_99"


# ─── Transcript provenance (D1 single write point + D2 clears) ───────────────


def _timer() -> MagicMock:
    """Timer stand-in for run_pipeline_phases (only read by the DONE log)."""
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)
    return timer


def _transcript_ok_ctx() -> SimpleNamespace:
    """English ctx as left by a successful transcript phase: data + trail +
    the caption fields the metadata phase stamps on video_data."""
    ctx = _english_ctx({})
    ctx.transcript_data = TranscriptData(
        segments=[{"start": 0.0, "duration": 1.0, "text": "hi"}],
        raw_text="hi",
        transcript_type="manual",
        source="ytdlp",
    )
    ctx.transcript_trail = TranscriptTrail(fetch_wall_ms=3)
    ctx.video_data = SimpleNamespace(
        caption_track="manual", caption_lang="en", caption_fetch_error=None
    )
    return ctx


async def _drain_phases(
    ctx: SimpleNamespace,
    repository: MagicMock,
    *extra_patches: AbstractContextManager[object],
) -> list[str]:
    """Drain run_pipeline_phases under _patched_phases() plus ``extra_patches``
    (entered last, so they override a default patch on the same attribute)."""
    with ExitStack() as stack:
        for p in [*_patched_phases(), *extra_patches]:
            stack.enter_context(p)
        return [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx, repository, "vsid", _timer()
            )
        ]


def _call_index(repository: MagicMock, method: str) -> int:
    """Position of the first ``method`` call in the repository's call log."""
    return [c[0] for c in repository.mock_calls].index(method)


def _sse_payloads(events: list[str]) -> list[dict]:
    """Decode the JSON body of every ``data:`` frame (skips ``[DONE]``)."""
    bodies = [ev.removeprefix("data: ").strip() for ev in events]
    return [json.loads(body) for body in bodies if body.startswith("{")]


async def _stream_direct(
    repository: MagicMock, phases_stub: Callable[..., AsyncIterator[str]]
) -> list[str]:
    """Drive stream_summarization down the non-cached path with the phase
    orchestration replaced by ``phases_stub`` and every network side effect
    (status callback HTTP, override clear) stubbed out."""
    with (
        patch.object(pipeline_runner.settings, "REDIS_ENABLED", False),
        patch.object(pipeline_runner, "run_pipeline_phases", new=phases_stub),
        patch.object(pipeline_runner, "send_video_status", new=AsyncMock()),
        patch.object(pipeline_runner, "clear_override", new=MagicMock()),
    ):
        return [
            ev
            async for ev in pipeline_runner.stream_summarization(
                "vsum_9", {"youtubeId": "yt9", "status": "pending"}, repository, MagicMock()
            )
        ]


@pytest.mark.asyncio
async def test_records_transcript_meta_after_transcript_phase_success() -> None:
    """D1: once transcript+frames finish, the runner persists transcriptMeta
    through the dedicated $set-only repo method (never save_structured_result,
    which would consume forceRefresh) and mirrors the provenance keys onto the
    Langfuse trace."""
    ctx = _transcript_ok_ctx()
    repository = MagicMock()
    trace_meta = MagicMock()

    await _drain_phases(
        ctx, repository, patch.object(pipeline_orchestration, "update_trace_metadata", trace_meta)
    )

    repository.set_transcript_meta.assert_called_once()
    vsid, meta = repository.set_transcript_meta.call_args.args
    assert vsid == "vsid"
    assert (meta["outcome"], meta["source"], meta["type"]) == ("ok", "ytdlp", "manual")
    trace_updates = [
        c.args[0] for c in trace_meta.call_args_list if "transcriptOutcome" in c.args[0]
    ]
    assert len(trace_updates) == 1, "exactly one provenance update on the trace"
    assert trace_updates[0]["transcriptOutcome"] == "ok"
    assert trace_updates[0]["transcriptSource"] == "ytdlp"


@pytest.mark.asyncio
async def test_records_failed_transcript_meta_when_transcript_phase_raises() -> None:
    """D1: a TranscriptError from the fallback chain must still leave
    outcome="failed" + attempted + errorCode + fetchWallMs on the row — the
    record runs from a finally, after the phase stamped ctx.transcript_trail,
    and the error keeps propagating."""
    ctx = _english_ctx({})
    repository = MagicMock()

    async def _parallel_raises(_phases: list, run_ctx: SimpleNamespace) -> AsyncIterator[str]:
        # Mirrors the transcript phase's own finally: trail lands before the raise.
        run_ctx.transcript_trail = TranscriptTrail(
            attempted=["api", "whisper"], error_code="NO_TRANSCRIPT", fetch_wall_ms=7
        )
        raise TranscriptError("no", ErrorCode.NO_TRANSCRIPT)
        yield  # pragma: no cover — makes this an async generator

    with pytest.raises(TranscriptError):
        await _drain_phases(
            ctx,
            repository,
            patch.object(pipeline_orchestration, "run_parallel_phases", _parallel_raises),
        )

    repository.set_transcript_meta.assert_called_once()
    _vsid, meta = repository.set_transcript_meta.call_args.args
    assert meta["outcome"] == "failed"
    assert meta["errorCode"] == "NO_TRANSCRIPT"
    assert meta["attempted"] == ["api", "whisper"]
    assert meta["fetchWallMs"] == 7
    assert "transcript_frames" in ctx.phase_times, "phase timing still stamped on raise"


@pytest.mark.asyncio
async def test_skips_transcript_meta_when_transcript_phase_never_ran() -> None:
    """A ctx with neither transcript_data nor a trail means the transcript phase
    never executed (metadata failed first) — there is nothing to record and no
    row write must happen."""
    ctx = _english_ctx({})
    repository = MagicMock()

    await _drain_phases(ctx, repository)

    repository.set_transcript_meta.assert_not_called()


@pytest.mark.asyncio
async def test_transcript_meta_write_failure_does_not_abort_pipeline() -> None:
    """The provenance write is best-effort: a Mongo failure inside the finally
    must be logged and swallowed, never mask the phase result or stop the
    pipeline from reaching assembly."""
    ctx = _transcript_ok_ctx()
    repository = MagicMock()
    repository.set_transcript_meta.side_effect = RuntimeError("mongo down")

    events = await _drain_phases(ctx, repository)

    assert any("assembly" in ev for ev in events), "pipeline continued past the write failure"


@pytest.mark.asyncio
async def test_redis_hit_clears_stale_transcript_meta_before_saving() -> None:
    """D2: a row completed from a Redis-served payload never ran the transcript
    phase, so a transcriptMeta block left by an earlier (possibly failed) run is
    $unset BEFORE the cached result is saved — such rows are recognised by
    "no transcriptMeta AND no pipelineVersion"."""
    cached = {
        "status": "completed",
        "youtubeId": "yt_cache_hit",
        "tabs": [{"id": "overview"}],
        "meta": {"contentTags": ["learning"]},
    }

    async def fake_get_response(_youtube_id: str) -> dict:
        return cached

    async def fake_cached_stream(_vsid: str, _cached: dict) -> AsyncIterator[str]:
        yield "data: {}\n\n"

    repository = MagicMock()
    entry = {"youtubeId": "yt_cache_hit", "status": "processing"}

    with (
        patch.object(pipeline_runner.response_cache, "get_response", new=fake_get_response),
        patch.object(pipeline_runner, "_stream_cached_structured", new=fake_cached_stream),
        patch.object(pipeline_runner.settings, "REDIS_ENABLED", True),
    ):
        _ = [
            ev
            async for ev in pipeline_runner.stream_summarization(
                "vsum_55", entry, repository, MagicMock()
            )
        ]
    # The persist runs in a worker thread via a fire-and-forget task; give the
    # executor a generous budget so a loaded CI box cannot turn this into a
    # cryptic "not in list" failure from _call_index below.
    for _ in range(500):
        if repository.save_structured_result.called:
            break
        await asyncio.sleep(0.01)
    assert repository.save_structured_result.called, "cache-hit persist task never ran"

    repository.clear_transcript_meta.assert_called_once_with("vsum_55")
    assert _call_index(repository, "clear_transcript_meta") < _call_index(
        repository, "save_structured_result"
    ), "stale block must be cleared before the cached payload completes the row"


@pytest.mark.asyncio
async def test_processing_transition_clears_transcript_meta() -> None:
    """D2: re-runs reuse the same _id, so the stale transcriptMeta block is
    $unset right after the row flips to processing — before the new transcript
    phase can write its own."""
    repository = MagicMock()

    async def _phases_stub(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
        yield "data: phases\n\n"

    await _stream_direct(repository, _phases_stub)

    repository.update_status.assert_called_once_with("vsum_9", ProcessingStatus.PROCESSING)
    repository.clear_transcript_meta.assert_called_once_with("vsum_9")
    assert _call_index(repository, "update_status") < _call_index(
        repository, "clear_transcript_meta"
    ), "clear follows the processing transition"


@pytest.mark.asyncio
async def test_transcript_meta_clear_failure_does_not_abort_run() -> None:
    """The start-of-run clear is best-effort: a Mongo hiccup on an
    observability-only field must not turn into a failed pipeline."""

    async def _phases_stub(*_args: object, **_kwargs: object):
        yield "data: phases\n\n"

    repository = MagicMock()
    repository.clear_transcript_meta.side_effect = RuntimeError("mongo down")
    with (
        patch.object(pipeline_runner.settings, "REDIS_ENABLED", False),
        patch.object(pipeline_runner, "run_pipeline_phases", new=_phases_stub),
        patch.object(pipeline_runner, "send_video_status", new=AsyncMock()),
        patch.object(pipeline_runner, "clear_override", new=MagicMock()),
    ):
        events = [
            ev
            async for ev in pipeline_runner.stream_summarization(
                "vsum_clear",
                {"youtubeId": "yt_clear", "status": "pending"},
                repository,
                MagicMock(),
            )
        ]

    assert any("phases" in ev for ev in events)
    assert not any('"event": "error"' in ev for ev in events)


@pytest.mark.asyncio
async def test_transcript_error_from_phases_still_marks_row_failed() -> None:
    """Regression guard for the finally-record wrap: a TranscriptError raised
    out of the phase orchestration must still reach the runner's classified
    handler — row marked failed with the code, and an SSE error frame emitted."""
    repository = MagicMock()

    async def _phases_raise(*_args: object, **_kwargs: object) -> AsyncIterator[str]:
        raise TranscriptError("no", ErrorCode.NO_TRANSCRIPT)
        yield  # pragma: no cover — makes this an async generator

    events = await _stream_direct(repository, _phases_raise)

    errors = [p for p in _sse_payloads(events) if p.get("event") == "error"]
    assert errors and errors[0]["code"] == "NO_TRANSCRIPT"
    repository.update_status.assert_any_call(
        "vsum_9", ProcessingStatus.FAILED, ANY, ErrorCode.NO_TRANSCRIPT
    )


@pytest.mark.asyncio
async def test_failed_run_closes_both_downloads_before_persisting_timing() -> None:
    """A run that dies before frames/assembly must not leak either download; a
    cancelled download is part of the timing record, so the closes come first."""
    ctx = _english_ctx({})
    order: list[str] = []
    ctx.lowres_video = MagicMock()
    ctx.lowres_video.close = AsyncMock(side_effect=lambda: order.append("close-lowres"))
    ctx.hires_video = MagicMock()
    ctx.hires_video.close = AsyncMock(side_effect=lambda: order.append("close-720p"))
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)

    async def _assembly_fails(_ctx):
        raise RuntimeError("assembly exploded")
        yield  # pragma: no cover — makes this an async generator

    async def _persist(*_args: object) -> None:
        order.append("persist")

    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_phase_assembly", _assembly_fails)
        )
        stack.enter_context(patch.object(pipeline_orchestration, "persist_run_timing", _persist))
        with pytest.raises(RuntimeError, match="assembly exploded"):
            _ = [
                ev
                async for ev in pipeline_orchestration.run_pipeline_phases(
                    ctx, MagicMock(), "vsid", timer
                )
            ]

    assert order == ["close-lowres", "close-720p", "persist"]


@pytest.mark.asyncio
async def test_failed_run_cancels_the_unjoined_t0_tasks() -> None:
    ctx = _english_ctx({})
    ctx.caption_task = asyncio.create_task(asyncio.sleep(60))
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)

    async def _metadata_fails(_ctx):
        raise RuntimeError("metadata exploded")
        yield  # pragma: no cover — makes this an async generator

    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_phase_metadata", _metadata_fails)
        )
        stack.enter_context(patch.object(pipeline_orchestration, "persist_run_timing", AsyncMock()))
        with pytest.raises(RuntimeError):
            _ = [
                ev
                async for ev in pipeline_orchestration.run_pipeline_phases(
                    ctx, MagicMock(), "vsid", timer
                )
            ]
    await asyncio.gather(ctx.caption_task, return_exceptions=True)

    assert ctx.caption_task.cancelled()


@pytest.mark.asyncio
async def test_phase_two_runs_the_text_branch_with_frames_only() -> None:
    ctx = _english_ctx({"key_points": [{"text": "a claim long enough"}]})
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)
    groups: list[list[str]] = []

    async def _capture_parallel(phases, _ctx):
        groups.append([phase.__name__ for phase in phases])
        yield "data: parallel\n\n"

    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_parallel_phases", _capture_parallel)
        )
        _ = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx, MagicMock(), "vsid", timer
            )
        ]

    # The description analysis is not a member: extraction never waits for it.
    assert groups[0] == ["run_phase_text", "run_phase_frames"]


@pytest.mark.asyncio
async def test_should_start_the_tier_probe_with_phase_two() -> None:
    """1b.1: the probe task exists from phase 2 on (it waits for transcript-ready)."""
    ctx = _english_ctx({"key_points": [{"text": "a claim long enough"}]})
    started: list[object] = []

    async def _capture_parallel(_phases, run_ctx):
        started.append(run_ctx.tier_probe_task)
        yield "data: parallel\n\n"

    with ExitStack() as stack:
        for p in _patched_phases():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(pipeline_orchestration, "run_parallel_phases", _capture_parallel)
        )
        _ = [
            ev
            async for ev in pipeline_orchestration.run_pipeline_phases(
                ctx, MagicMock(), "vsid", _timer()
            )
        ]

    assert started[0] is not None


@pytest.mark.asyncio
async def test_run_end_should_cancel_a_probe_still_waiting_for_the_transcript() -> None:
    ctx = _english_ctx({})

    await _drain_phases(ctx, MagicMock())
    await asyncio.gather(ctx.tier_probe_task, return_exceptions=True)

    assert ctx.tier_probe_task.cancelled()
