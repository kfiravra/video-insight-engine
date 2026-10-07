"""Replay driver: run the real ``stream_summarization`` on a cassette.

The SSE stream DRIVES the pipeline (nothing runs unless it is consumed), so
the driver iterates it to the end exactly like the broker does, stamping each
event's offset. Everything external is faked for the duration of the run:
LLM completions (``fake_llm``), media/transcript/status/Qdrant (``stubs``),
Mongo (``repository``) and the network itself (``network_guard``), with the
settings the recorded run used plus the harness invariants below. Process-wide
state the run warms (prompt caches, lazy globals) is restored afterwards
(``process_state``) so a replay never leaks into the tests that follow it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from src.config import settings
from src.routes.pipeline_runner import stream_summarization
from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from tests.replay.cassette import Cassette
from tests.replay.fake_llm import replay_llm
from tests.replay.network_guard import block_network
from tests.replay.process_state import preserved_process_state
from tests.replay.repository import InMemoryRepository
from tests.replay.stubs import external_stubs

_RUNNER_LOGGER = "src.routes.pipeline_runner"
_DONE_MARKER = "[pipeline] DONE"
_SSE_PREFIX = "data: "

# Applied on top of the cassette's settings: no external services, no
# observability exports, no audio fallbacks, no faithfulness judge (it is off
# the critical path and its sampled calls are not part of the cassette).
# ``YOUTUBE_PROXY_URL`` comes from the cassette (the recorded runs were
# proxied; downloads read it for their subprocess env).
_HARNESS_SETTINGS: dict[str, Any] = {
    "REDIS_ENABLED": False,
    "QDRANT_ENABLED": True,
    "LANGFUSE_PUBLIC_KEY": None,
    "LANGFUSE_SECRET_KEY": None,
    "LANGFUSE_FAITHFULNESS_SAMPLE_RATE": 0.0,
    "WHISPER_ENABLED": False,
    "GEMINI_API_KEY": None,
    "SCENE_EXTRACTION_ENABLED": True,
}


@dataclass(frozen=True)
class SseRecord:
    offset_ms: int
    event: str


@dataclass
class ReplayResult:
    """Everything a replay produced — the assertions and the report read this."""

    video_id: str
    speed: float
    wall_ms: int
    events: list[SseRecord]
    timing: dict[str, Any] | None
    done_line: str | None
    saved_result: dict[str, Any] | None
    statuses: list[str]
    llm_calls_served: int
    llm_misses: list[str]
    llm_unused: list[str]
    llm_model_mismatches: list[str]
    network_attempts: list[str]
    qdrant_stores: list[str] = field(default_factory=list)
    unexpected_commands: list[str] = field(default_factory=list)
    frame_manifest: dict[str, Any] | None = None

    def event_names(self) -> list[str]:
        return [record.event for record in self.events]

    def downloads(self) -> list[tuple[str, str]]:
        """(kind, purpose) of every download ``pipeline.timing`` recorded."""
        rows = (self.timing or {}).get("downloads", [])
        return [(d["kind"], d["purpose"]) for d in rows]

    def phase_walls(self) -> dict[str, int]:
        """Top-level ``pipeline.timing`` phases (last write wins per name)."""
        if not self.timing:
            return {}
        return {p["name"]: int(p["wallMs"]) for p in self.timing["phases"]}


def _event_name(chunk: str) -> str:
    if not chunk.startswith(_SSE_PREFIX):
        return "?"
    body = chunk[len(_SSE_PREFIX) :].strip()
    if body == "[DONE]":
        return "[DONE]"
    try:
        return str(json.loads(body).get("event", "?"))
    except json.JSONDecodeError:
        return "?"


class _DoneLineHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(_DONE_MARKER):
            self.lines.append(message)


@contextmanager
def _capture_done_line() -> Iterator[_DoneLineHandler]:
    runner_logger = logging.getLogger(_RUNNER_LOGGER)
    handler = _DoneLineHandler()
    previous_level = runner_logger.level
    runner_logger.addHandler(handler)
    if runner_logger.getEffectiveLevel() > logging.INFO:
        runner_logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        runner_logger.removeHandler(handler)
        runner_logger.setLevel(previous_level)


@contextmanager
def replay_settings(cassette: Cassette) -> Iterator[None]:
    """Patch ``settings`` with the recorded run's knobs + harness invariants."""
    overrides = {**cassette.settings, **_HARNESS_SETTINGS}
    unknown = sorted(name for name in overrides if not hasattr(settings, name))
    if unknown:
        raise ValueError(f"Cassette {cassette.video_id} sets unknown settings: {unknown}")
    with ExitStack() as stack:
        for name, value in overrides.items():
            stack.enter_context(patch.object(settings, name, value))
        yield


async def _consume(
    cassette: Cassette, repository: InMemoryRepository, started: float
) -> list[SseRecord]:
    provider = LLMProvider(model=settings.llm_model, fast_model=settings.llm_fast_model)
    entry = {
        "youtubeId": cassette.video_id,
        "status": "pending",
        "language": cassette.video.language,
    }
    records: list[SseRecord] = []
    async for chunk in stream_summarization(
        f"replay-{cassette.video_id}",
        entry,
        repository,  # pyright: ignore[reportArgumentType] -- duck-typed in-memory stand-in
        LLMService(provider),
        force_refresh=True,
    ):
        offset = int((time.monotonic() - started) * 1000)
        records.append(SseRecord(offset_ms=offset, event=_event_name(chunk)))
    return records


async def _drain_background(timeout: float) -> None:
    """Let fire-and-forget tasks (Qdrant stores) finish before the stubs unpatch."""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=timeout)


async def run_replay(cassette: Cassette, *, speed: float = 1.0) -> ReplayResult:
    """Replay ``cassette`` at ``speed`` (1 = recorded walls, 0 = no sleeps)."""
    repository = InMemoryRepository()
    with (
        preserved_process_state(),
        block_network() as attempts,
        replay_settings(cassette),
        external_stubs(cassette, speed) as stubs,
        replay_llm(cassette.llm, speed=speed, video_id=cassette.video_id) as fake,
        _capture_done_line() as done,
    ):
        started = time.monotonic()
        events = await _consume(cassette, repository, started)
        wall_ms = int((time.monotonic() - started) * 1000)
        await _drain_background(timeout=1.0 + cassette.sleeps["qdrantStore"] * speed)
    return ReplayResult(
        video_id=cassette.video_id,
        speed=speed,
        wall_ms=wall_ms,
        events=events,
        timing=repository.pipeline_timing,
        done_line=done.lines[-1] if done.lines else None,
        saved_result=repository.saved_result,
        statuses=[s.status for s in repository.statuses],
        llm_calls_served=len(fake.used),
        llm_misses=list(fake.misses),
        llm_unused=fake.unused_keys(),
        llm_model_mismatches=list(fake.model_mismatches),
        network_attempts=list(attempts),
        qdrant_stores=list(stubs.qdrant_stores),
        unexpected_commands=list(stubs.media.unexpected_commands),
        frame_manifest=stubs.media.scene_manifest(),
    )
