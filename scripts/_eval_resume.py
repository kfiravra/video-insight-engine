"""Resume a stopped eval pass without re-paying: score the eval user's finished runs.

``run_eval.py --resume-since <ISO-8601 with offset>`` applies to the FIRST
pass only (the noise mode's r2 always runs fresh). Before the pass starts:

1. List the eval user's videos (``GET /api/videos``, newest first). A
   bypassCache POST replaces the user's previous userVideo for that
   youtubeId, so the newest row per youtubeId is the latest run.
2. Keep rows created at/after ``since`` whose status is ``completed`` and
   whose youtubeId is in the pass, then read each doc (``GET
   /api/videos/:id``) — the same payload ``run_pipeline`` returns, with
   ``submittedAt`` = the row's ``createdAt`` so the Langfuse trace lookup
   still finds the run.

Those entries are scored from the stored doc and never POSTed; anything not
completed (failed, still running, older than ``since``, missing) runs
normally. The pass then writes the same report files as a fresh pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from _eval_api import Submission, to_output

logger = logging.getLogger("run_eval.resume")

_PAGE = 100  # GET /api/videos max page size


@dataclass(frozen=True)
class PriorRun:
    user_video_id: str
    video_summary_id: str
    youtube_id: str
    status: str
    created_at: datetime


def youtube_id(url: str) -> str | None:
    """The video id of a ``youtube.com/watch?v=`` or ``youtu.be/`` URL."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() == "youtu.be":
        return parts.path.strip("/") or None
    return (parse_qs(parts.query).get("v") or [None])[0]


def parse_since(value: str) -> datetime:
    """Parse ``--resume-since``; an offset is required (local vs UTC is ambiguous)."""
    since = datetime.fromisoformat(value)
    if since.tzinfo is None:
        raise ValueError(f"--resume-since {value!r} needs a UTC offset, e.g. +03:00 or Z")
    return since


def _prior_run(row: dict[str, Any]) -> PriorRun:
    return PriorRun(
        user_video_id=row["id"],
        video_summary_id=row["videoSummaryId"],
        youtube_id=row["youtubeId"],
        status=row.get("status") or "",
        created_at=datetime.fromisoformat(row["createdAt"].replace("Z", "+00:00")),
    )


async def list_prior_runs(
    client: httpx.AsyncClient, api_url: str, since: datetime
) -> dict[str, PriorRun]:
    """Newest run per youtubeId created at/after ``since`` (any status)."""
    runs: dict[str, PriorRun] = {}
    offset = 0
    while True:
        resp = await client.get(f"{api_url}/api/videos", params={"limit": _PAGE, "offset": offset})
        resp.raise_for_status()
        rows = resp.json().get("videos") or []
        for run in map(_prior_run, rows):
            if run.created_at < since:  # rows are sorted newest first
                return runs
            runs.setdefault(run.youtube_id, run)
        if len(rows) < _PAGE:
            return runs
        offset += _PAGE


async def _read_completed(
    client: httpx.AsyncClient, api_url: str, run: PriorRun
) -> dict[str, Any] | None:
    resp = await client.get(f"{api_url}/api/videos/{run.user_video_id}")
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "completed":
        return None
    submission = Submission(run.user_video_id, run.video_summary_id, run.created_at)
    return to_output(data, submission)


async def load_reusable_runs(
    api_url: str, token: str, since: datetime, youtube_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """youtubeId → scored-payload of every completed run eligible for reuse."""
    headers = {"Authorization": f"Bearer {token}"}
    reusable: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        runs = await list_prior_runs(client, api_url, since)
        for yt_id, run in sorted(runs.items()):
            if yt_id not in youtube_ids or run.status != "completed":
                continue
            output = await _read_completed(client, api_url, run)
            if output is not None:
                reusable[yt_id] = output
    logger.info(
        "Resume: %d completed run(s) since %s will be scored, not re-run: %s",
        len(reusable),
        since.isoformat(),
        ", ".join(sorted(reusable)) or "none",
    )
    return reusable
