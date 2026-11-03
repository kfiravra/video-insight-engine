"""Run ids per attempt (P0b, P-1 / P-2): resolution, the runs[] write, and where the id travels.

- ``resolve_run``: a ``dispatched`` entry is taken over; a missing id, or one that
  already ran (``running`` or terminal), gets a fresh uuid with ``retryOf``
- ``start_run``: takes over the api's entry in place, else pushes (capped)
- the runner binds the id so status callbacks carry it, also on a cache hit
- the worker message keeps ``runId`` through a republish; the stall sweeper sends
  the stalled row's ``currentRunId``
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from llm_common.observability import trace_id_for_run

from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes import pipeline_runner
from src.services import status_callback
from src.services.pipeline.run_registry import (
    RUNS_KEPT,
    RunDispatch,
    RunIdentity,
    resolve_run,
    run_entry,
    run_tags,
)
from src.services.run_context import bind_run_id, current_run_id
from src.worker.payload import VideoJobPayload

_VSID = "0" * 24


def _entry(*runs: dict[str, Any], current: str | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {"youtubeId": "yt1", "runs": list(runs)}
    if current:
        doc["currentRunId"] = current
    return doc


# ─── resolve_run ────────────────────────────────────────────────────────


class TestResolveRun:
    def test_should_take_over_the_api_entry_when_it_is_still_dispatched(self) -> None:
        run = resolve_run(_entry({"runId": "a", "status": "dispatched", "attempt": 1}, current="a"))

        assert (run.run_id, run.trace_id, run.attempt) == ("a", trace_id_for_run("a"), 1)
        assert (run.dispatched_entry, run.retry_of) == (True, None)

    def test_should_prefer_the_message_run_id_when_the_worker_sends_one(self) -> None:
        entry = _entry(
            {"runId": "old", "status": "completed"},
            {"runId": "msg", "status": "dispatched"},
            current="old",
        )

        assert resolve_run(entry, RunDispatch(run_id="msg")).run_id == "msg"

    @pytest.mark.parametrize("status", ["running", "completed", "failed", "cache_hit"])
    def test_should_mint_a_fresh_retry_when_the_id_already_ran(self, status: str) -> None:
        entry = _entry({"runId": "a", "status": status, "attempt": 1}, current="a")

        run = resolve_run(entry, RunDispatch(run_id="a", attempt=2))

        assert run.run_id != "a"
        assert (run.retry_of, run.attempt, run.dispatched_entry) == ("a", 2, False)
        assert run.trace_id == trace_id_for_run(run.run_id)

    def test_should_mint_and_point_at_the_last_attempt_when_no_id_arrives(self) -> None:
        run = resolve_run(_entry({"runId": "last", "status": "failed", "attempt": 3}))

        assert (run.retry_of, run.attempt) == ("last", 4)

    def test_should_mint_without_retry_of_when_the_row_has_no_runs(self) -> None:
        run = resolve_run({"youtubeId": "yt1"})

        assert (run.retry_of, run.attempt, run.dispatched_entry) == (None, 1, False)

    def test_should_keep_an_unknown_id_and_register_it_when_the_row_lacks_its_entry(self) -> None:
        run = resolve_run({"youtubeId": "yt1", "currentRunId": "legacy"})

        assert (run.run_id, run.dispatched_entry, run.retry_of) == ("legacy", False, None)

    def test_should_carry_the_chat_session_when_the_dispatch_had_one(self) -> None:
        entry = _entry({"runId": "a", "status": "dispatched", "chatSessionId": "s1"}, current="a")

        assert resolve_run(entry).chat_session_id == "s1"


class TestEntryAndTags:
    def test_should_build_a_running_entry_with_the_optional_ids_when_present(self) -> None:
        run = RunIdentity(run_id="r", trace_id="t", attempt=2, retry_of="p", chat_session_id="s")
        now = datetime(2026, 11, 3, tzinfo=UTC)

        doc = run_entry(
            run, request_id="req", path="worker", bypass=True, pipeline_version="v11", now=now
        )

        assert doc == {
            "runId": "r",
            "traceId": "t",
            "attempt": 2,
            "pipelineVersion": "v11",
            "bypass": True,
            "path": "worker",
            "status": "running",
            "startedAt": now,
            "requestId": "req",
            "chatSessionId": "s",
            "retryOf": "p",
        }

    def test_should_tag_the_run_and_leave_out_unset_ids_when_building_tags(self) -> None:
        tags = run_tags(
            RunIdentity(run_id="r", trace_id="t", attempt=1), pipeline_version="v11", bypass=False
        )

        assert tags == ["run:r", "attempt:1", "pipelineVersion:v11", "bypass:false"]


# ─── start_run ──────────────────────────────────────────────────────────


def _repo() -> tuple[MongoDBVideoRepository, MagicMock]:
    collection = MagicMock()
    database = MagicMock()
    database.videoSummaryCache = collection
    return MongoDBVideoRepository(database), collection


class TestStartRun:
    def test_should_update_the_api_entry_in_place_when_taking_over(self) -> None:
        repo, collection = _repo()
        collection.update_one.return_value = MagicMock(matched_count=1)

        repo.start_run(_VSID, {"runId": "a", "status": "running", "path": "sse"}, take_over=True)

        query, update = collection.update_one.call_args.args
        assert query == {"_id": ObjectId(_VSID), "runs.runId": "a"}
        assert update == {
            "$set": {"currentRunId": "a", "runs.$.status": "running", "runs.$.path": "sse"}
        }

    def test_should_push_the_entry_when_the_api_entry_is_gone(self) -> None:
        repo, collection = _repo()
        collection.update_one.side_effect = [MagicMock(matched_count=0), MagicMock(matched_count=1)]

        repo.start_run(_VSID, {"runId": "a", "status": "running"}, take_over=True)

        _, update = collection.update_one.call_args_list[1].args
        assert update["$push"] == {
            "runs": {"$each": [{"runId": "a", "status": "running"}], "$slice": -RUNS_KEPT}
        }
        assert update["$set"] == {"currentRunId": "a"}

    def test_should_push_without_a_lookup_when_the_attempt_is_new(self) -> None:
        repo, collection = _repo()

        repo.start_run(_VSID, {"runId": "b"}, take_over=False)

        collection.update_one.assert_called_once()
        assert "$push" in collection.update_one.call_args.args[1]

    def test_should_restamp_current_run_id_when_post_summarize_carries_one(self) -> None:
        repo, collection = _repo()

        repo.set_dispatch_context(_VSID, "u1", "req-1", "run-9")

        assert collection.update_one.call_args.args[1]["$set"]["currentRunId"] == "run-9"


# ─── Where the id travels ───────────────────────────────────────────────


class TestStatusCallbackRunId:
    @pytest.fixture
    def posted(self) -> Iterator[list[dict[str, Any]]]:
        bodies: list[dict[str, Any]] = []

        class _Client:
            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def post(self, _url: str, json: dict[str, Any], **_kw: Any) -> None:
                bodies.append(json["payload"])

        with patch.object(status_callback.httpx, "AsyncClient", _Client):
            yield bodies
        bind_run_id(None)

    async def test_should_send_the_bound_run_id_when_the_status_comes_from_a_run(
        self, posted
    ) -> None:
        bind_run_id("run-ctx")

        await status_callback.send_video_status(_VSID, None, "completed")

        assert posted[0]["runId"] == "run-ctx"

    async def test_should_prefer_an_explicit_run_id_when_one_is_passed(self, posted) -> None:
        bind_run_id("run-ctx")

        await status_callback.send_video_status(_VSID, None, "failed", run_id="run-row")

        assert posted[0]["runId"] == "run-row"

    async def test_should_send_no_run_id_when_outside_a_run(self, posted) -> None:
        await status_callback.send_video_status(_VSID, None, "failed")

        assert "runId" not in posted[0]


async def _phases(*_args: object):
    # The processing status went out before this phase: the id is bound by now.
    yield f"data: {json.dumps({'runId': current_run_id()})}\n\n"


class TestRunnerBindsTheRun:
    async def test_should_register_and_bind_the_run_when_the_pipeline_runs(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline_runner.settings, "REDIS_ENABLED", False)
        repository = MagicMock()
        entry = _entry({"runId": "a", "status": "dispatched", "attempt": 1}, current="a")
        with (
            patch.object(pipeline_runner, "run_pipeline_phases", _phases),
            patch.object(pipeline_runner, "send_video_status", AsyncMock()),
            patch.object(pipeline_runner, "clear_override", MagicMock()),
        ):
            events = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    _VSID, entry, repository, MagicMock()
                )
            ]

        start = repository.start_run.call_args
        assert start.args[1]["runId"] == "a" and start.args[1]["status"] == "running"
        assert start.kwargs == {"take_over": True}
        assert '"runId": "a"' in events[0]

    async def test_should_report_the_dispatched_run_and_register_nothing_when_redis_serves_it(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline_runner.settings, "REDIS_ENABLED", True)
        cached = {
            "status": "completed",
            "youtubeId": "yt1",
            "tabs": [{"id": "t"}],
            "meta": {"m": 1},
        }
        monkeypatch.setattr(
            pipeline_runner.response_cache, "get_response", AsyncMock(return_value=cached)
        )
        seen: list[str | None] = []

        async def _complete(*_args: object) -> None:
            seen.append(current_run_id())

        async def _cached_events(*_args: object):
            yield "data: cached\n\n"

        monkeypatch.setattr(pipeline_runner, "_complete_from_cache", _complete)
        monkeypatch.setattr(pipeline_runner, "_stream_cached_structured", _cached_events)
        repository = MagicMock()
        entry = {**_entry({"runId": "a", "status": "dispatched"}, current="a"), "status": "pending"}
        with patch.object(pipeline_runner, "clear_override", MagicMock()):
            _ = [
                ev
                async for ev in pipeline_runner.stream_summarization(
                    _VSID, entry, repository, MagicMock()
                )
            ]
            # The completion task runs after the stream returned.
            await asyncio.sleep(0)

        repository.start_run.assert_not_called()
        assert seen == ["a"]


class TestWorkerAndSweeper:
    def test_should_keep_the_run_id_when_the_message_is_republished(self) -> None:
        payload = VideoJobPayload.model_validate(
            {
                "videoSummaryId": _VSID,
                "youtubeId": "dQw4w9WgXcQ",
                "url": "https://youtu.be/dQw4w9WgXcQ",
                "priority": 5,
                "requestId": "req-1",
                "runId": "run-1",
                "pipelineVersion": "v11",
                "createdAt": "2026-11-03T00:00:00Z",
            }
        )
        retried = payload.model_copy(update={"attempt": payload.attempt + 1})

        body = json.loads(retried.model_dump_json(by_alias=True))
        assert (body["runId"], body["attempt"], body["pipelineVersion"]) == ("run-1", 2, "v11")

    async def test_should_send_the_rows_run_id_when_the_sweeper_fails_a_stalled_row(self) -> None:
        from src.services.stall_sweeper import StallSweeper

        repository = MagicMock()
        repository.mark_stalled_failed.return_value = True
        notify = AsyncMock()
        sweeper = StallSweeper(
            repository=repository,
            alerts_collection=MagicMock(),
            lock_held=AsyncMock(return_value=False),
            notify_status=notify,
            deliver_alert=MagicMock(return_value=True),
            threshold=timedelta(minutes=30),
        )
        now = datetime.now(UTC)
        row = {
            "_id": ObjectId(_VSID),
            "youtubeId": "yt1",
            "updatedAt": now,
            "currentRunId": "run-s",
        }

        await sweeper._fail_row(row, now, now)

        assert notify.call_args.kwargs["run_id"] == "run-s"
