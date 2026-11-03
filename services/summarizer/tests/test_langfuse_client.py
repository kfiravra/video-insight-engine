"""Tests for the summarizer's Langfuse wiring (``src.services.observability`` + the runner).

The shared client itself is tested in ``packages/llm-common/tests``; this file
covers what the summarizer adds on top:
- ``init_langfuse()`` reads the summarizer's settings (keys, base URL, environment)
- the package re-exports the shared helpers (call sites and patches target it)
- one run = one trace ``pipeline:<videoSummaryId>`` in session
  ``pipeline-<videoSummaryId>``, tagged with its entry path; the raw user id
  never lands in trace metadata
- a Redis cache hit opens no trace; a failed run marks its root span failed
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common import observability as shared
from llm_common.observability import client as shared_client
from llm_common.observability import trace_id_for_run

from src.config import settings
from src.routes import pipeline_runner
from src.services.pipeline.run_registry import RunIdentity
from src.services import observability
from llm_common.observability.testing import LangfuseCapture

# ─── init_langfuse ──────────────────────────────────────────────────────


def test_init_should_return_none_when_keys_are_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", None)
    assert observability.init_langfuse() is None
    assert observability.is_enabled() is False


def test_init_should_build_the_client_from_summarizer_settings_when_keys_are_set(
    monkeypatch: pytest.MonkeyPatch,
):
    sdk = MagicMock(name="Langfuse")
    monkeypatch.setattr(shared_client, "_load_sdk", lambda: (sdk, MagicMock()))
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk_test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    assert observability.init_langfuse() is sdk.return_value
    assert sdk.call_args.kwargs == {
        "public_key": "pk_test",
        "secret_key": "sk_test",
        "base_url": "https://cloud.langfuse.com",
        "environment": "production",
    }


@pytest.mark.parametrize("name", observability.__all__)
def test_package_should_re_export_the_shared_helper_when_imported(name: str):
    if name == "init_langfuse":
        pytest.skip("bound to the summarizer's settings")
    assert getattr(observability, name) is getattr(shared, name)


# ─── Trace attributes ───────────────────────────────────────────────────


_RUN = RunIdentity(run_id="run-1", trace_id=trace_id_for_run("run-1"), attempt=2, retry_of="run-0")


def test_run_trace_args_should_name_the_run_and_its_session_when_given_a_row():
    args = pipeline_runner._run_trace_args(
        {"language": "en", "userId": "u1"},
        youtube_id="yt1",
        video_summary_id="vsid1",
        user_id="u1",
        request_id="req-1",
        run_path="worker",
        run=_RUN,
        bypass=True,
    )
    assert args["trace_id"] == trace_id_for_run("run-1")
    assert args["name"] == "pipeline:yt1"
    assert args["session_id"] == "pipeline-vsid1"
    assert args["user_id"] == "u1"
    assert args["tags"] == [
        "youtubeId:yt1",
        "videoSummaryId:vsid1",
        "path:worker",
        "run:run-1",
        "attempt:2",
        f"pipelineVersion:{settings.PIPELINE_VERSION}",
        "bypass:true",
        "retryOf:run-0",
        "requestId:req-1",
    ]
    assert args["input"] == {"youtubeId": "yt1", "videoSummaryId": "vsid1", "runId": "run-1"}
    # The user id goes through LANGFUSE_USER_ID_MODE; never raw into metadata.
    assert "userId" not in args["metadata"]
    assert args["metadata"]["requestId"] == "req-1"


def test_run_trace_args_should_leave_out_request_and_flags_when_absent():
    args = pipeline_runner._run_trace_args(
        {},
        youtube_id="yt1",
        video_summary_id="v",
        user_id=None,
        request_id=None,
        run_path="sse",
        run=RunIdentity(run_id="r", trace_id=trace_id_for_run("r"), attempt=1),
        bypass=False,
    )
    assert not any(tag.startswith("requestId:") for tag in args["tags"])
    assert {"requestId", "evalRun", "coldMedia"}.isdisjoint(args["metadata"])


# ─── The runner ─────────────────────────────────────────────────────────


def _valid_cached(youtube_id: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "youtubeId": youtube_id,
        "tabs": [{"id": "t1"}],
        "meta": {"title": "x"},
    }


async def _drain(entry: dict[str, Any], phases: Any, **kwargs: Any) -> list[str]:
    with (
        patch.object(pipeline_runner, "run_pipeline_phases", phases),
        patch.object(pipeline_runner, "send_video_status", AsyncMock()),
        patch.object(pipeline_runner, "clear_override", MagicMock()),
    ):
        return [
            ev
            async for ev in pipeline_runner.stream_summarization(
                "vsid", entry, MagicMock(), MagicMock(), **kwargs
            )
        ]


async def _one_phase(*_args: object):
    yield "data: phases\n\n"


async def test_runner_should_open_one_trace_in_the_rows_session_when_the_pipeline_runs(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: LangfuseCapture
):
    monkeypatch.setattr(pipeline_runner.settings, "REDIS_ENABLED", False)

    entry = {
        "youtubeId": "yt1",
        "userId": "u1",
        "currentRunId": "run-a",
        "runs": [{"runId": "run-a", "status": "dispatched", "attempt": 1}],
    }
    await _drain(entry, _one_phase, run_path="worker")

    root = langfuse_capture.named("pipeline:yt1")
    assert root.trace_id == trace_id_for_run("run-a")
    assert root.parent is None
    assert root.attributes["session.id"] == "pipeline-vsid"
    assert root.attributes["user.id"] == "u1"
    assert "path:worker" in root.attributes["langfuse.trace.tags"]


async def test_runner_should_open_no_trace_when_redis_serves_the_video(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: LangfuseCapture
):
    monkeypatch.setattr(pipeline_runner.settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(
        pipeline_runner.response_cache, "get_response", AsyncMock(return_value=_valid_cached("yt1"))
    )
    complete = AsyncMock()
    monkeypatch.setattr(pipeline_runner, "_complete_from_cache", complete)

    async def _cached_events(*_args: object):
        yield "data: cached\n\n"

    monkeypatch.setattr(pipeline_runner, "_stream_cached_structured", _cached_events)
    phases = MagicMock()

    events = await _drain({"youtubeId": "yt1", "status": "pending"}, phases)

    assert events == ["data: cached\n\n"]
    phases.assert_not_called()
    assert langfuse_capture.observations() == []
    complete.assert_awaited_once()


async def test_runner_should_mark_the_trace_failed_when_the_run_fails(
    monkeypatch: pytest.MonkeyPatch, langfuse_capture: LangfuseCapture
):
    monkeypatch.setattr(pipeline_runner.settings, "REDIS_ENABLED", False)

    async def _boom(*_args: object):
        raise RuntimeError("extraction exploded")
        yield  # pragma: no cover — makes this an async generator

    events = await _drain({"youtubeId": "yt1"}, _boom)

    assert any("error" in ev for ev in events)
    (root,) = [
        s for s in langfuse_capture.exporter.get_finished_spans() if s.name == "pipeline:yt1"
    ]
    assert root.status.status_code.name == "ERROR"
