"""`chat_turn_finished` (observability D12): one line per chat turn for the Grafana Chat dashboard."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services import turn_events
from src.services.turn_events import observe_turn


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _stream(*events: dict):
    for event in events:
        yield _sse(event)


async def _drain(stream) -> list[str]:
    return [sse async for sse in stream]


def _finished(events: MagicMock) -> dict:
    assert events.info.call_args.args == ("chat_turn_finished",)
    return events.info.call_args.kwargs


async def test_should_report_a_completed_turn_with_its_tool_runs():
    events = MagicMock()
    stream = _stream(
        {"type": "tool", "metadata": {"status": "start", "action": "create_folder"}},
        {"type": "tool", "metadata": {"status": "done", "action": "create_folder"}},
        {"type": "text", "content": "Done."},
        {"type": "done"},
    )
    with patch.object(turn_events, "_events", events):
        out = await _drain(observe_turn(stream, surface="video", session_id="s-1", turn=2))

    assert len(out) == 4
    fields = _finished(events)
    assert (fields["status"], fields["tool_calls"], fields["turn"], fields["surface"]) == (
        "completed",
        1,
        2,
        "video",
    )
    assert fields["session_id"] == "s-1"
    assert isinstance(fields["duration_ms"], int)


async def test_should_report_error_when_the_turn_streamed_an_error_event():
    events = MagicMock()
    with patch.object(turn_events, "_events", events):
        await _drain(
            observe_turn(
                _stream({"type": "error", "content": "x"}, {"type": "done"}),
                surface="library",
                session_id=None,
                turn=1,
            )
        )

    assert _finished(events)["status"] == "error"


async def test_should_report_failed_and_reraise_when_the_turn_raises():
    events = MagicMock()

    async def boom():
        yield _sse({"type": "text", "content": "a"})
        raise RuntimeError("provider down")

    with patch.object(turn_events, "_events", events), pytest.raises(RuntimeError):
        await _drain(observe_turn(boom(), surface="video", session_id="s-1", turn=1))

    assert _finished(events)["status"] == "failed"


async def test_should_report_interrupted_when_the_client_leaves_mid_turn():
    events = MagicMock()
    with patch.object(turn_events, "_events", events):
        stream = observe_turn(
            _stream({"type": "text", "content": "a"}, {"type": "done"}),
            surface="video",
            session_id="s-1",
            turn=1,
        )
        await stream.__anext__()
        await stream.aclose()

    assert _finished(events)["status"] == "interrupted"


def test_should_ignore_lines_that_are_not_json_events():
    assert turn_events._event_kind("data: not json\n\n") == (None, False)
    assert turn_events._event_kind(": keep-alive\n\n") == (None, False)
