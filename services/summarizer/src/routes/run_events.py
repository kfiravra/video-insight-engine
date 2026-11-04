"""Structured run events (observability D12): one JSON line per attempt start and end.

The Grafana Pipeline dashboard and the failure-rate alert read them with LogQL
(`pipeline_run_started`, `pipeline_run_finished`); no Prometheus client library.
"""

from __future__ import annotations

import structlog

from src.services.pipeline.run_registry import RunIdentity

_events = structlog.get_logger("pipeline.events")


def log_run_started(
    run: RunIdentity, *, video_summary_id: str, youtube_id: str, run_path: str, bypass: bool
) -> None:
    """``pipeline_run_started``: the attempt registered and its trace is about to open."""
    _events.info(
        "pipeline_run_started",
        run_id=run.run_id,
        video_summary_id=video_summary_id,
        youtube_id=youtube_id,
        path=run_path,
        attempt=run.attempt,
        bypass=bypass,
        retry_of=run.retry_of,
    )


def log_run_finished(
    status: str,
    *,
    run_id: str | None,
    video_summary_id: str,
    youtube_id: str | None,
    duration_s: float,
    run_path: str,
    error_code: str | None = None,
) -> None:
    """``pipeline_run_finished``: completed | failed | cache_hit | interrupted, and how long it took."""
    _events.info(
        "pipeline_run_finished",
        run_id=run_id,
        video_summary_id=video_summary_id,
        youtube_id=youtube_id,
        path=run_path,
        status=status,
        cache_hit=status == "cache_hit",
        duration_ms=int(duration_s * 1000),
        error_code=error_code,
    )
