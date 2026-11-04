"""Langfuse deep links for admin rows, built from stored ids (P-6).

Admin owns the cost ledger; Langfuse owns per-call trace forensics. Every
pipeline attempt's trace id is stored on its ``videoSummaryCache.runs[]`` entry
(``traceId`` = ``sha256(runId)`` hex, first 32 chars, the id the summarizer seeds
the trace with), so a link needs no Langfuse call:
``{LANGFUSE_BASE_URL}/project/{LANGFUSE_PROJECT_ID}/traces/{trace_id}``.

``LANGFUSE_PROJECT_ID`` is required for links; without it every helper returns
``None`` and the UI renders no link. Nothing here does I/O or raises.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.config import settings


def _project_url() -> str | None:
    if not settings.LANGFUSE_PROJECT_ID:
        return None
    return f"{settings.LANGFUSE_BASE_URL.rstrip('/')}/project/{settings.LANGFUSE_PROJECT_ID}"


def trace_url(trace_id: str | None) -> str | None:
    """The trace's Langfuse page, or ``None`` without a trace id or project id."""
    base = _project_url()
    return f"{base}/traces/{trace_id}" if base and trace_id else None


def session_url(session_id: str | None) -> str | None:
    """A Langfuse session's page (``pipeline-<vsid>`` or ``chat-<sessionId>``), or ``None``."""
    base = _project_url()
    return f"{base}/sessions/{session_id}" if base and session_id else None


def run_entry(
    runs: Iterable[Any] | None, *, run_id: str | None, request_id: str | None
) -> dict[str, Any] | None:
    """The ``runs[]`` entry of one attempt (P-2): the entry with ``run_id``.

    A ledger row written before P1 has no run id; it falls back to the newest
    entry with its ``request_id`` (retries of one request share it, the newest
    attempt is the one to open). ``None`` when no entry matches (pre-P0b rows,
    entries capped out of ``runs``).
    """
    found: dict[str, Any] | None = None
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if run_id:
            if run.get("runId") == run_id:
                found = run
        elif request_id and run.get("requestId") == request_id:
            found = run  # later entries are newer
    return found
