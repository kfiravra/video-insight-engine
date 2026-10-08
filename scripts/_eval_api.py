"""vie-api client for the golden eval: eval-user auth + one pipeline run.

Moved out of ``run_eval.py`` (which re-exports ``authenticate`` and
``run_pipeline`` for ``baseline_golden_dataset.py``). Targets any vie-api
base URL — local compose or the prod API (``EVAL_API_URL``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger("run_eval.api")

# POST /api/videos requires JWT auth as a dedicated eval user. Login comes
# first; registration is a dev-only fallback (EVAL_ALLOW_REGISTER) because the
# prod register route is limited to 5/h/IP and every re-login would burn it.
# Credentials come from the env — there is intentionally NO committed default
# password, because a committed default would mean every checkout that
# registers shares a known-credential user.
_EVAL_EMAIL_DEFAULT = "eval@vie.local"
_EVAL_NAME_DEFAULT = "Eval Runner"
_TERMINAL_EVENTS = frozenset({"done", "complete", "error", "stream_error", "cached"})
_STREAM_CAP_S = 1800
_DOC_POLLS = 24
_DOC_POLL_WAIT_S = 5

# Given the token that just got a 401, return a fresh one.
Refresh = Callable[[str], Awaitable[str]]


def _resolve_eval_credentials() -> tuple[str, str, str]:
    """Return ``(email, password, name)`` for the eval user.

    Requires ``EVAL_USER_PASSWORD`` in the environment — refuses to fall back
    to a committed default so we never silently provision an account whose
    credentials are checked into the repo. Set it once in your local ``.env``
    (8+ chars, upper/lower/digit to clear the API's Zod regex); add
    ``EVAL_ALLOW_REGISTER=1`` locally to have the first run create the user.
    """
    email = os.environ.get("EVAL_USER_EMAIL", _EVAL_EMAIL_DEFAULT)
    name = os.environ.get("EVAL_USER_NAME", _EVAL_NAME_DEFAULT)
    password = os.environ.get("EVAL_USER_PASSWORD")
    if not password:
        raise RuntimeError(
            "EVAL_USER_PASSWORD is not set. Pick a local-only password "
            "(8+ chars, upper/lower/digit, e.g. EvalRunner2026!), add it to "
            "your .env (plus EVAL_ALLOW_REGISTER=1 to create the user on a "
            "local stack), and re-run. The script intentionally does NOT carry a "
            "committed default so eval users are never created with a known "
            "password baked into the repo."
        )
    return email, password, name


def _register_allowed() -> bool:
    return os.environ.get("EVAL_ALLOW_REGISTER", "").strip().lower() in {"1", "true", "yes"}


async def _login(
    client: httpx.AsyncClient, api_url: str, email: str, password: str
) -> httpx.Response:
    return await client.post(
        f"{api_url}/api/auth/login", json={"email": email, "password": password}
    )


async def authenticate(api_url: str) -> str:
    """Return a JWT access token for the dedicated eval user.

    Logs in first. Only when login is rejected (401 — no such user) AND
    ``EVAL_ALLOW_REGISTER`` is truthy (local dev convenience) does it register;
    a 403 (registration closed), 409 (email exists) or 429 (register rate
    limit) from register falls through to one more login. Raises on any other
    failure so the eval never runs unauthenticated.
    """
    email, password, name = _resolve_eval_credentials()

    async with httpx.AsyncClient(timeout=30.0) as client:
        login = await _login(client, api_url, email, password)
        if login.status_code == 401 and _register_allowed():
            register = await client.post(
                f"{api_url}/api/auth/register",
                json={"email": email, "password": password, "name": name},
            )
            if register.status_code == 201:
                logger.info("Auth: registered new eval user %s", email)
                return register.json()["accessToken"]
            if register.status_code not in (403, 409, 429):
                register.raise_for_status()  # surface unexpected failures
            login = await _login(client, api_url, email, password)
        login.raise_for_status()
        logger.info("Auth: logged in as eval user %s", email)
        return login.json()["accessToken"]


@dataclass(frozen=True)
class Submission:
    user_video_id: str
    video_summary_id: str | None
    submitted_at: datetime


class SubmittedUnauthorizedError(RuntimeError):
    """A 401 after the POST succeeded — the run exists, so resume it, never re-submit."""

    def __init__(self, submission: Submission, stage: str) -> None:
        super().__init__(f"401 on the {stage} of video {submission.user_video_id}")
        self.submission = submission


class StreamUnauthorizedError(SubmittedUnauthorizedError):
    """The token expired before the SSE stream opened — re-attach to the same stream."""

    def __init__(self, submission: Submission) -> None:
        super().__init__(submission, "stream")


class DocPollUnauthorizedError(SubmittedUnauthorizedError):
    """The token expired while polling the doc — the pipeline already ran."""

    def __init__(self, submission: Submission) -> None:
        super().__init__(submission, "doc poll")


async def _consume_stream(client: httpx.AsyncClient, api_url: str, submission: Submission) -> None:
    # CRITICAL: the pipeline only runs while a client consumes the SSE
    # stream — POST /summarize merely registers the request (see the
    # NOTE in summarizer main.py). Poll-only clients wait forever on a
    # 'pending' row (2026-07-14: 20/20 entries timed out this way).
    # So: hold the stream open until a terminal event, THEN read the
    # persisted doc. Keepalives bound the read gaps; 30 min total cap.
    stream_timeout = httpx.Timeout(30.0, read=300.0)
    video_summary_id = submission.video_summary_id
    async with client.stream(
        "GET", f"{api_url}/api/videos/{video_summary_id}/stream", timeout=stream_timeout
    ) as stream:
        if stream.status_code == 401:
            raise StreamUnauthorizedError(submission)
        stream.raise_for_status()
        deadline = time.monotonic() + _STREAM_CAP_S
        async for line in stream.aiter_lines():
            if time.monotonic() > deadline:
                raise TimeoutError(f"Video {video_summary_id} did not complete within 30 min")
            if line.startswith("event:") and line.split(":", 1)[1].strip() in _TERMINAL_EVENTS:
                return


async def _submit(
    client: httpx.AsyncClient, api_url: str, url: str, bypass_cache: bool, cold: bool = False
) -> Submission:
    submitted_at = datetime.now(UTC)
    body_in: dict[str, Any] = {"url": url, "bypassCache": bypass_cache}
    if cold:
        body_in["cold"] = True
    resp = await client.post(f"{api_url}/api/videos", json=body_in)
    resp.raise_for_status()
    body = resp.json()
    # POST /api/videos returns {"video": {"id", "videoSummaryId", ...}, "cached": bool}.
    # `video.id` is the userVideo id used by GET /api/videos/:id. Fall back to
    # flat keys for forward-compat in case the response shape ever flattens.
    video = body.get("video") or {}
    user_video_id = video.get("id") or body.get("id")
    if not user_video_id:
        raise RuntimeError(f"No video.id in response: {body}")
    video_summary_id = video.get("videoSummaryId") or body.get("videoSummaryId")
    return Submission(user_video_id, video_summary_id, submitted_at)


async def _await_completed_doc(
    client: httpx.AsyncClient, api_url: str, submission: Submission
) -> dict[str, Any]:
    vid = submission.user_video_id
    for _ in range(_DOC_POLLS):
        r = await client.get(f"{api_url}/api/videos/{vid}")
        if r.status_code == 401:
            raise DocPollUnauthorizedError(submission)
        if r.status_code != 404:
            data = r.json()
            if data.get("status") == "completed":
                return data
            if data.get("status") == "failed":
                raise RuntimeError(f"Video {vid} pipeline failed: {data.get('error') or 'unknown'}")
        await asyncio.sleep(_DOC_POLL_WAIT_S)
    raise TimeoutError(f"Video {vid} stream ended but doc never reached completed")


def to_output(data: dict[str, Any], submission: Submission) -> dict[str, Any]:
    return {
        "meta": data.get("meta") or data.get("assembledMeta") or {},
        "tabs": data.get("tabs") or data.get("assembledTabs") or [],
        "duration": data.get("duration"),
        "youtubeId": data.get("youtubeId"),
        "videoSummaryId": data.get("videoSummaryId") or submission.video_summary_id,
        "submittedAt": submission.submitted_at.isoformat(),
    }


def _client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=600.0, headers={"Authorization": f"Bearer {token}"})


async def _finish(
    client: httpx.AsyncClient, api_url: str, submission: Submission, *, attach: bool
) -> dict[str, Any]:
    """Hold the run's SSE stream (when ``attach``) then return its completed doc."""
    if attach and submission.video_summary_id:
        await _consume_stream(client, api_url, submission)
    return await _await_completed_doc(client, api_url, submission)


async def run_pipeline(
    api_url: str, url: str, token: str, bypass_cache: bool = False, cold: bool = False
) -> dict[str, Any]:
    """POST the video, hold the SSE stream to a terminal event, return the doc.

    Returns ``meta`` + ``tabs`` (the scored payload) plus ``duration``,
    ``youtubeId``, ``videoSummaryId`` and ``submittedAt`` (used to find the
    run's Langfuse trace). ``bypass_cache=True`` forces a fresh pipeline run;
    ``cold=True`` (admin/eval only) also skips the summarizer's media caches.
    """
    async with _client(token) as client:
        submission = await _submit(client, api_url, url, bypass_cache, cold)
        return to_output(await _finish(client, api_url, submission, attach=True), submission)


class SharedToken:
    """The eval user's token, shared by concurrent runs.

    When several in-flight runs hit a 401 at once, only the first re-logs in;
    the rest find the token already replaced and reuse it — one login, not N
    (the login route is rate-limited too).
    """

    def __init__(self, api_url: str, token: str = "") -> None:
        self.api_url = api_url
        self.token = token
        self._lock = asyncio.Lock()

    async def refresh(self, stale: str) -> str:
        async with self._lock:
            if self.token == stale:
                self.token = await authenticate(self.api_url)
            return self.token


async def run_with_reauth(
    api_url: str,
    url: str,
    token: str,
    bypass_cache: bool,
    refresh: Refresh | None = None,
    cold: bool = False,
) -> tuple[dict[str, Any], str]:
    """Run one video, re-authenticating once on a 401. Returns (doc, token).

    Access tokens don't survive multi-hour runs (2026-07-14: 18/20 entries
    401'd after ~2h). A 401 on the POST re-submits; a 401 after it (stream
    open or doc poll) resumes the SAME submission — re-attaching to its stream
    if that is where it failed — because re-submitting would queue (and pay
    for) a second bypassCache pipeline. ``refresh`` (default: log in again)
    lets concurrent runs share one re-login via ``SharedToken.refresh``.
    """
    renew = refresh or (lambda _stale: authenticate(api_url))
    try:
        return await run_pipeline(api_url, url, token, bypass_cache=bypass_cache, cold=cold), token
    except SubmittedUnauthorizedError as exc:
        logger.info("Token expired mid-run (%s) — re-authenticating to resume %s", exc, url)
        token = await renew(token)
        attach = isinstance(exc, StreamUnauthorizedError)
        async with _client(token) as client:
            data = await _finish(client, api_url, exc.submission, attach=attach)
        return to_output(data, exc.submission), token
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            raise
        logger.info("Token expired — re-authenticating, retrying %s", url)
        token = await renew(token)
        return await run_pipeline(api_url, url, token, bypass_cache=bypass_cache, cold=cold), token
