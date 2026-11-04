"""``chat_turn_finished``: one structured log line per chat turn (observability D12).

The Grafana Chat dashboard counts turns, errors and tool calls with LogQL over
these lines; no Prometheus client library is involved.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator

from src.logging_config import get_logger

_events = get_logger("assistant.events")


def _event_kind(sse: str) -> tuple[str | None, bool]:
    """``(event type, finished tool run)`` of one ``data: {json}`` SSE string."""
    if not sse.startswith("data: "):
        return None, False
    try:
        event = json.loads(sse[len("data: ") :])
    except json.JSONDecodeError:
        return None, False
    if not isinstance(event, dict):
        return None, False
    metadata = event.get("metadata")
    kind = event.get("type")
    done = isinstance(metadata, dict) and metadata.get("status") == "done"
    return kind, kind == "tool" and done


async def observe_turn(
    stream: AsyncIterator[str], *, surface: str, session_id: str | None, turn: int
) -> AsyncGenerator[str, None]:
    """Pass a turn's SSE events through and log ``chat_turn_finished`` when it ends.

    ``status``: ``completed``, ``error`` (the turn streamed an error event),
    ``failed`` (it raised) or ``interrupted`` (the client went away first).
    """
    started = time.monotonic()
    status = "interrupted"
    tool_calls = 0
    errored = False
    try:
        async for sse in stream:
            kind, tool_done = _event_kind(sse)
            errored = errored or kind == "error"
            tool_calls += int(tool_done)
            yield sse
        status = "error" if errored else "completed"
    except Exception:
        status = "failed"
        raise
    finally:
        _events.info(
            "chat_turn_finished",
            session_id=session_id,
            surface=surface,
            turn=turn,
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            tool_calls=tool_calls,
        )
