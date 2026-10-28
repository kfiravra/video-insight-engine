#!/usr/bin/env python3
"""Re-run the demo library on the current pipeline version (pipeline-1min 3.9).

After a ``PIPELINE_VERSION`` bump the library keeps serving each video's
old-version doc until the video is submitted again (docs/IDEMPOTENCY.md).
This script is that re-submission, for every video of the demo library.

Where the list comes from (one source):

- default: the demo user's library in MongoDB (``userVideos`` of the user
  whose email is ``--demo-email``, default ``$DEMO_USER_EMAIL``): every entry
  whose doc is completed and stamped with an older ``pipelineVersion``.
  Entries already on the current version are skipped, so a stopped run resumes
  by running the same command again. Entries whose doc is not completed are
  listed and left alone.
- ``--all-cache``: every completed, stamped, older-version doc in
  ``videoSummaryCache`` (``demo-export.sh``'s selection at the old version)
  without eval-user rows (never served, D25) and without videos already
  completed at the current version; an owner column shows whose library holds
  each one. Submitting them adds every one to the demo user's library.
- ``--urls FILE``: a ``demo-export.sh`` ``urls.txt`` (URL, TAB, title per line;
  a bare YouTube id works too), for an API whose Mongo isn't reachable (prod).
  What a run leaves undone (not run, refused, failed) is written to
  ``FILE.left.txt`` in the same format; a run that never ended is named to
  check instead, because resubmitting it could pay for a second run.

The Mongo sources read the local stack, so a non-local ``--api-url`` is refused
with them unless ``--mongo-uri`` names the matching database.
``--only ID`` (repeatable) and ``--limit N`` cut the list, e.g. to ONE video for
the first prod pass.

DRY RUN by default: prints the list (minutes per video) and the cost band,
≈ $0.15–0.33 per video up to 40 min. Longer videos run chunked and their cost
is unmeasured. It submits nothing and does not log in.

``--apply`` logs in as the demo user (``{"demo": true}``: vie-api holds the
credentials, so this script never handles a password) and submits each video
with ``bypassCache`` through ``scripts/_eval_api``, one at a time, holding its
SSE stream to the end because the stream drives the pipeline. The demo user,
not the eval user, submits because an eval run is never promoted or served, so
it would leave the library on the old version. A ``bypassCache`` POST inserts a
new version row and re-points the demo user's library entry at it at once; the
row is served when it completes. If the summarizer reports the run failed,
vie-api hands the entry back to the version still served. A run that never
ends (stream cap, dropped connection, killed script) leaves the entry on the
unfinished row, so the summary names those entries to check. Stops at the first
failure unless ``--keep-going``.

The demo user is a free-tier user: ``USER_COST_LIMIT_FREE`` (USD per UTC day;
the admin panel can grant a day's credit) and ``VIDEO_DAILY_LIMIT``
(submissions per 24 h, 5 in ``.env.production.example``; no admin override)
apply, so a long list may need several days. The refusal's error code
(``RATE_LIMITED``, ``DAILY_LIMIT_REACHED``) is printed and the run stops.

Usage::

    python3 scripts/demo_rerun.py                                  # dry run, demo library
    python3 scripts/demo_rerun.py --all-cache                      # dry run, whole cache
    python3 scripts/demo_rerun.py --urls demo-data/<ts>/urls.txt   # dry run, export list
    python3 scripts/demo_rerun.py --apply                          # local stack, on Kfir's go
    python3 scripts/demo_rerun.py --urls demo-data/<ts>/urls.txt --apply --limit 1 \\
        --api-url https://<domain>                                 # prod, on Kfir's go

Exit codes: 0 dry run printed / every run completed / nothing to re-run;
1 the demo login or a run failed; 2 the list could not be read or the
source and the API don't match.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from _demo_rerun_sources import (
    DemoVideo,
    Selection,
    SourceError,
    current_pipeline_version,
    load_from_mongo,
    parse_urls_file,
)
from _eval_api import run_with_reauth

# One run's cost band on the 2026-10 dev + prod ledger (captions; Whisper adds ≈ $0.14).
COST_PER_VIDEO_USD = (0.15, 0.33)
# Above this a video is extracted in chunks (pipeline-1min 3.7); no cost measured yet.
CHUNKED_ABOVE_MIN = 40
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# How one run can end badly (HTTP error, failed doc, 30-min cap, bad JSON body);
# anything else is a bug in this script and should surface.
_RUN_ERRORS = (httpx.HTTPError, RuntimeError, TimeoutError, ValueError)


@dataclass(frozen=True)
class RunOutcome:
    """How one ``--apply`` submission ended and where it left the library entry.

    ``entry`` is ``"new version"`` (completed), ``"unchanged"`` (the POST was
    refused), ``"restored"`` (failed; vie-api re-pointed the entry at the served
    version) or ``"check"`` (the entry may sit on an unfinished row).
    """

    video: DemoVideo
    entry: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.entry == "new version"


# ─── Report ───


def _is_chunked(video: DemoVideo) -> bool:
    return video.duration_s is not None and video.duration_s > CHUNKED_ABOVE_MIN * 60


def _row(index: int, video: DemoVideo, *, owners: bool) -> str:
    minutes = "-" if video.duration_s is None else f"{video.duration_s / 60:.0f} min"
    owner = f"  {video.owner or '-':<8}" if owners else ""
    chunked = "  [chunked, band unmeasured]" if _is_chunked(video) else ""
    version = video.version or "-"
    return (
        f"{index:>4}  {video.youtube_id}  {version:>4}  {minutes:>8}{owner}  {video.title}{chunked}"
    )


def _cost_line(videos: list[DemoVideo]) -> str:
    chunked = sum(1 for video in videos if _is_chunked(video))
    measured = len(videos) - chunked
    low, high = (measured * cost for cost in COST_PER_VIDEO_USD)
    line = (
        f"{len(videos)} videos: {measured} × ≈ ${COST_PER_VIDEO_USD[0]:.2f}–"
        f"{COST_PER_VIDEO_USD[1]:.2f} = ≈ ${low:.2f}–{high:.2f} (concurrency 1)"
    )
    if chunked:
        line += f" + {chunked} chunked (> {CHUNKED_ABOVE_MIN} min), band unmeasured"
    return line


def render_plan(selection: Selection, current: str, api_url: str, *, apply: bool) -> str:
    """The list, the count and the cost band, as printed before anything runs."""
    mode = "APPLY" if apply else "DRY RUN (nothing submitted)"
    owners = any(video.owner is not None for video in selection.videos)
    lines = [
        f"=== demo re-run to {current} — {mode} ===",
        f"source: {selection.source}",
        f"api: {api_url}",
    ]
    lines += [_row(i, v, owners=owners) for i, v in enumerate(selection.videos, start=1)]
    lines.append(_cost_line(selection.videos))
    lines += selection.notes
    if not apply:
        lines.append("Nothing was submitted. --apply submits them as the demo user.")
    return "\n".join(lines)


def render_outcomes(outcomes: list[RunOutcome], left: list[DemoVideo]) -> str:
    """Totals, the ids still to re-run and the library entries to check."""
    failed = [outcome for outcome in outcomes if not outcome.ok]
    lines = [f"Done: {len(outcomes) - len(failed)} completed, {len(failed)} failed."]
    if left:
        lines.append(f"Left to re-run: {' '.join(video.youtube_id for video in left)}")
    check = [outcome.video.youtube_id for outcome in failed if outcome.entry == "check"]
    if check:
        lines.append(
            f"Library entries to check (may point at an unfinished version): {' '.join(check)}"
        )
    return "\n".join(lines)


def write_left_file(urls: Path, left: list[DemoVideo]) -> Path:
    """``<urls>.left.txt`` in the urls.txt format, so ``--urls`` can resume from it."""
    path = Path(f"{urls}.left.txt")
    path.write_text("".join(f"{v.url}\t{v.title}\n" for v in left), encoding="utf-8")
    return path


# ─── Apply ───


async def demo_login(api_url: str) -> str:
    """The demo user's access token (vie-api swaps in the account it holds)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{api_url}/api/auth/login", json={"demo": True})
        response.raise_for_status()
        return str(response.json()["accessToken"])


def _describe(exc: Exception) -> str:
    """The API's error code and message for an HTTP refusal, else the exception."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__}: {exc}"
    try:
        body = exc.response.json()
    except ValueError:
        body = {}
    code = body.get("error", "-") if isinstance(body, dict) else "-"
    message = body.get("message", "") if isinstance(body, dict) else ""
    return f"HTTP {exc.response.status_code} {code}: {str(message)[:200]}"


# ``_eval_api._await_completed_doc``'s message when the doc reports ``failed``.
_FAILED_DOC = " pipeline failed: "


def _entry_after(exc: Exception) -> str:
    """Where a failed run left the library entry (see ``RunOutcome``)."""
    if isinstance(exc, httpx.HTTPStatusError):
        request = exc.request
        if request.method == "POST" and request.url.path.endswith("/api/videos"):
            return "unchanged"
    # The run ended failed: vie-api hands the entry back to the served version.
    if isinstance(exc, RuntimeError) and _FAILED_DOC in str(exc):
        return "restored"
    return "check"


async def _run_one(api_url: str, video: DemoVideo, token: str) -> tuple[RunOutcome, str]:
    """One bypassCache submission held to its end; returns the outcome and the live token."""
    try:
        output, token = await run_with_reauth(
            api_url, video.url, token, bypass_cache=True, refresh=lambda _: demo_login(api_url)
        )
    except _RUN_ERRORS as exc:
        return RunOutcome(video, _entry_after(exc), _describe(exc)), token
    summary_id = str(output.get("videoSummaryId"))
    # The entry still on its old doc = vie-api handed a failed run back (restoreServedVersion).
    if video.summary_id is not None and summary_id == video.summary_id:
        return RunOutcome(video, "restored", "run failed; entry back on the served version"), token
    return RunOutcome(video, "new version", f"summary {summary_id}"), token


async def rerun(videos: list[DemoVideo], api_url: str, *, keep_going: bool) -> list[RunOutcome]:
    """Submit each video in turn; stop at the first failure unless ``keep_going``.

    Raises ``httpx.HTTPError`` when the demo login fails (nothing was submitted).
    """
    token = await demo_login(api_url)
    outcomes: list[RunOutcome] = []
    for index, video in enumerate(videos, start=1):
        outcome, token = await _run_one(api_url, video, token)
        outcomes.append(outcome)
        status = "ok" if outcome.ok else f"FAILED ({outcome.entry})"
        print(f"[{index}/{len(videos)}] {video.youtube_id} {status} — {outcome.detail}")
        if not outcome.ok and not keep_going:
            break
    return outcomes


# ─── CLI ───


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--urls", type=Path, help="a demo-export.sh urls.txt instead of Mongo")
    source.add_argument("--all-cache", action="store_true", help="every stale doc, not the library")
    parser.add_argument("--demo-email", default=os.environ.get("DEMO_USER_EMAIL", ""))
    parser.add_argument("--mongo-uri", help="default $MONGODB_URI, else the local stack")
    parser.add_argument("--mongo-db", default=os.environ.get("MONGODB_DB", "video-insight-engine"))
    parser.add_argument("--api-url", default="http://localhost:3000", help="vie-api base URL")
    parser.add_argument("--only", action="append", default=[], metavar="ID", help="repeatable")
    parser.add_argument("--limit", type=int, help="submit at most N videos")
    parser.add_argument("--apply", action="store_true", help="submit the videos (spends money)")
    parser.add_argument("--keep-going", action="store_true", help="don't stop at a failed run")
    return parser.parse_args(argv)


def _check_targets(args: argparse.Namespace) -> None:
    """A Mongo list must come from the database behind the API it is sent to."""
    host = urlsplit(args.api_url).hostname or ""
    if args.urls is None and args.mongo_uri is None and host not in _LOCAL_HOSTS:
        raise SourceError(
            f"the list would come from the local Mongo but go to {args.api_url}; "
            "pass --urls or --mongo-uri"
        )


def _load(args: argparse.Namespace, current: str) -> Selection:
    _check_targets(args)
    if args.urls is None:
        uri = args.mongo_uri or os.environ.get("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
        return load_from_mongo(
            uri, args.mongo_db, current, demo_email=args.demo_email, all_cache=args.all_cache
        )
    try:
        text = args.urls.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceError(f"cannot read {args.urls}: {exc.strerror}") from exc
    return Selection(parse_urls_file(text), str(args.urls))


def _cut(videos: list[DemoVideo], only: list[str], limit: int | None) -> list[DemoVideo]:
    picked = [video for video in videos if not only or video.youtube_id in only]
    return picked if limit is None else picked[: max(limit, 0)]


def _apply(args: argparse.Namespace, selection: Selection, videos: list[DemoVideo]) -> int:
    try:
        outcomes = asyncio.run(rerun(videos, args.api_url.rstrip("/"), keep_going=args.keep_going))
    except httpx.HTTPError as exc:
        print(
            f"demo_rerun: demo login failed, nothing submitted ({_describe(exc)})", file=sys.stderr
        )
        return 1
    # A run that never ended may still be going: resubmitting it could pay twice,
    # so it is named as an entry to check, not listed to re-run.
    settled = {o.video.youtube_id for o in outcomes if o.entry in ("new version", "check")}
    left = [video for video in selection.videos if video.youtube_id not in settled]
    print(render_outcomes(outcomes, left))
    if args.urls is not None and left:
        print(f"Wrote {write_left_file(args.urls, left)} — resume with --urls on it.")
    return 0 if all(outcome.ok for outcome in outcomes) else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    current = current_pipeline_version()
    try:
        selection = _load(args, current)
    except SourceError as exc:
        print(f"demo_rerun: {exc}", file=sys.stderr)
        return 2
    videos = _cut(selection.videos, args.only, args.limit)
    shown = Selection(videos, selection.source, selection.notes)
    print(render_plan(shown, current, args.api_url, apply=args.apply))
    if not args.apply or not videos:
        return 0
    return _apply(args, selection, videos)


if __name__ == "__main__":
    raise SystemExit(main())
