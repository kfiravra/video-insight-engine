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
    """A session's Langfuse page (P2: a video row's attempts), or ``None``."""
    base = _project_url()
    return f"{base}/sessions/{session_id}" if base and session_id else None


def latest_trace_ids_by_request(runs: Iterable[Any] | None) -> dict[str, str]:
    """``requestId`` → ``traceId`` of the newest ``runs[]`` entry for it.

    Retries of one request share its id; the newest attempt is the one to open.
    Entries without both ids (pre-P0b rows, GDPR-stripped ones) are skipped.
    """
    out: dict[str, str] = {}
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        request_id, trace_id = run.get("requestId"), run.get("traceId")
        if isinstance(request_id, str) and isinstance(trace_id, str) and request_id and trace_id:
            out[request_id] = trace_id  # later entries are newer
    return out
