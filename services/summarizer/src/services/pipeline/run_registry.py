"""Run ids for pipeline attempts (P-1, P-2): which id an attempt runs under, and its ``runs[]`` entry.

vie-api mints a ``runId`` per dispatch and pushes a ``dispatched`` entry on the
row. The summarizer also starts runs the api never dispatched (worker retries,
DLQ replays, ``POST /regenerate``, legacy messages), so the rule is: an id is
used by one attempt only. A ``dispatched`` entry is taken over; a missing id, or
one whose entry already ran (``running`` or terminal), gets a fresh uuid with
``retryOf`` naming the previous attempt. One attempt = one runId = one trace.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llm_common.observability import trace_id_for_run

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────
DISPATCHED = "dispatched"
RUNNING = "running"
RUNS_KEPT = 50  # vie-api's RUNS_KEPT: the row keeps the newest entries only


# ─── Value objects ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunDispatch:
    """What a queue message says about its attempt (absent on the SSE / dev paths)."""

    run_id: str | None = None
    attempt: int = 1
    chat_session_id: str | None = None


@dataclass(frozen=True)
class RunIdentity:
    """The attempt this run is: its ids and whether the api already holds its entry."""

    run_id: str
    trace_id: str
    attempt: int
    retry_of: str | None = None
    chat_session_id: str | None = None
    # True when the row already has a ``dispatched`` entry for ``run_id``.
    dispatched_entry: bool = False


# ─── Resolution ─────────────────────────────────────────────────────────
def _entries(entry: dict[str, Any]) -> list[dict[str, Any]]:
    runs = entry.get("runs")
    return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []


def _fresh(previous: dict[str, Any] | None, retry_of: str | None, attempt: int) -> RunIdentity:
    run_id = str(uuid.uuid4())
    prev_attempt = int(previous.get("attempt") or 0) if previous else 0
    return RunIdentity(
        run_id=run_id,
        trace_id=trace_id_for_run(run_id),
        attempt=max(attempt, prev_attempt + 1),
        retry_of=retry_of,
        chat_session_id=(previous or {}).get("chatSessionId"),
    )


def resolve_run(entry: dict[str, Any], dispatch: RunDispatch | None = None) -> RunIdentity:
    """The run id this attempt runs under (no write; see :func:`run_entry`).

    The queue message's ``runId`` wins over the row's ``currentRunId`` (the SSE
    and dev paths only have the row). A reused id keeps the api's entry; a fresh
    one points back at the attempt it follows.
    """
    dispatch = dispatch or RunDispatch()
    candidate = dispatch.run_id or entry.get("currentRunId")
    runs = _entries(entry)
    if not candidate:
        last = runs[-1] if runs else None
        return _fresh(last, last.get("runId") if last else None, dispatch.attempt)
    existing = next((r for r in reversed(runs) if r.get("runId") == candidate), None)
    if existing is None:
        # A legacy row, an entry capped out of ``runs``, or an HTTP-only stamp:
        # the id is unused, so this attempt takes it and registers its entry.
        return RunIdentity(
            run_id=candidate,
            trace_id=trace_id_for_run(candidate),
            attempt=dispatch.attempt,
            chat_session_id=dispatch.chat_session_id,
        )
    if existing.get("status") != DISPATCHED:
        return _fresh(existing, candidate, dispatch.attempt)
    stored_trace = existing.get("traceId")
    trace_id = trace_id_for_run(candidate)
    if stored_trace and stored_trace != trace_id:
        logger.warning(
            "run_trace_id_mismatch runId=%s stored=%s derived=%s", candidate, stored_trace, trace_id
        )
    return RunIdentity(
        run_id=candidate,
        trace_id=trace_id,
        attempt=max(dispatch.attempt, int(existing.get("attempt") or 1)),
        chat_session_id=existing.get("chatSessionId") or dispatch.chat_session_id,
        dispatched_entry=True,
    )


# ─── Entry and tags ─────────────────────────────────────────────────────
def run_entry(
    run: RunIdentity,
    *,
    request_id: str | None,
    path: str,
    bypass: bool,
    pipeline_version: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The ``runs[]`` entry of a starting attempt (``running``), as the summarizer writes it."""
    doc: dict[str, Any] = {
        "runId": run.run_id,
        "traceId": run.trace_id,
        "attempt": run.attempt,
        "pipelineVersion": pipeline_version,
        "bypass": bypass,
        "path": path,
        "status": RUNNING,
        "startedAt": now or datetime.now(UTC),
    }
    if request_id:
        doc["requestId"] = request_id
    if run.chat_session_id:
        doc["chatSessionId"] = run.chat_session_id
    if run.retry_of:
        doc["retryOf"] = run.retry_of
    return doc


def run_tags(run: RunIdentity, *, pipeline_version: str, bypass: bool) -> list[str]:
    """The D4 trace tags of an attempt (``retryOf`` / ``chatSession`` only when set)."""
    tags = [
        f"run:{run.run_id}",
        f"attempt:{run.attempt}",
        f"pipelineVersion:{pipeline_version}",
        f"bypass:{str(bypass).lower()}",
    ]
    if run.chat_session_id:
        tags.append(f"chatSession:{run.chat_session_id}")
    if run.retry_of:
        tags.append(f"retryOf:{run.retry_of}")
    return tags
