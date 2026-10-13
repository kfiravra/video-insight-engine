"""Video lists for ``demo_rerun.py``: the demo user's library, the whole cache, a urls.txt.

Split out of ``demo_rerun.py`` (the CLI, report and submit loop) so each file
stays small; nothing here talks to vie-api.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _REPO_ROOT / "packages" / "shared" / "src" / "config" / "pipeline-version.json"
_YOUTUBE_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_BARE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SUMMARY_FIELDS = {"youtubeId": 1, "title": 1, "pipelineVersion": 1, "duration": 1, "status": 1}


class SourceError(Exception):
    """The video list could not be read."""


@dataclass(frozen=True)
class DemoVideo:
    """One library video to re-run.

    ``summary_id`` is the doc the demo user's entry points at before the run
    (demo-library source only); ``owner`` labels whose library holds it
    (``--all-cache`` only).
    """

    youtube_id: str
    title: str
    version: str | None = None
    duration_s: float | None = None
    summary_id: str | None = None
    owner: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.youtube_id}"


@dataclass(frozen=True)
class Selection:
    """The videos to re-run, where they came from, and lines worth printing with them."""

    videos: list[DemoVideo]
    source: str
    notes: list[str] = field(default_factory=list)


class Collection(Protocol):
    """The pymongo ``Collection`` calls the selections need."""

    def find(
        self, filter: Mapping[str, Any], projection: Mapping[str, Any]
    ) -> Iterable[Mapping[str, Any]]: ...

    def find_one(
        self, filter: Mapping[str, Any], projection: Mapping[str, Any]
    ) -> Mapping[str, Any] | None: ...

    def distinct(self, key: str, filter: Mapping[str, Any]) -> list[Any]: ...


class Database(Protocol):
    def __getitem__(self, name: str) -> Collection: ...


def current_pipeline_version(path: Path = _VERSION_FILE) -> str:
    """The canonical version (the file the api and the summarizer both read)."""
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


# ─── urls.txt ───


def _youtube_id(url: str) -> str | None:
    if _BARE_ID.fullmatch(url.strip()):
        return url.strip()
    match = _YOUTUBE_ID.search(url)
    return match.group(1) if match else None


def parse_urls_file(text: str) -> list[DemoVideo]:
    """``demo-export.sh`` ``urls.txt`` lines (or bare ids) → videos, first of each id kept.

    Raises ``SourceError`` naming the line when a non-blank line has no YouTube id.
    """
    videos: dict[str, DemoVideo] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        url, _, title = line.partition("\t")
        youtube_id = _youtube_id(url)
        if youtube_id is None:
            raise SourceError(f"line {number}: no YouTube id in {url.strip()[:80]!r}")
        videos.setdefault(youtube_id, DemoVideo(youtube_id, title.strip()))
    return list(videos.values())


# ─── Mongo ───


def is_stale(doc: Mapping[str, Any], current: str) -> bool:
    """Completed and stamped with another version. Unstamped docs predate stamping
    and are served as current (the api's ``isStaleVersion``)."""
    version = doc.get("pipelineVersion")
    return doc.get("status") == "completed" and isinstance(version, str) and version != current


def stale_query(current: str) -> dict[str, Any]:
    """``is_stale`` as a Mongo filter, served (non-eval) rows only."""
    return {
        "status": "completed",
        "pipelineVersion": {"$type": "string", "$ne": current},
        "evalRun": {"$ne": True},
    }


def _video(doc: Mapping[str, Any], **extra: Any) -> DemoVideo:
    duration = doc.get("duration")
    return DemoVideo(
        youtube_id=str(doc.get("youtubeId") or ""),
        title=str(doc.get("title") or ""),
        version=doc.get("pipelineVersion"),
        duration_s=float(duration) if isinstance(duration, int | float) else None,
        **extra,
    )


def _demo_user_id(db: Database, email: str) -> Any:
    """The demo user's ``_id``; never echoes the address (PII)."""
    if not email.strip():
        raise SourceError("no demo user email: set DEMO_USER_EMAIL or pass --demo-email")
    user = db["users"].find_one({"email": email.strip().lower()}, {"_id": 1})
    if user is None:
        raise SourceError("no user has the demo email (DEMO_USER_EMAIL / --demo-email)")
    return user["_id"]


def _unfinished_label(summary_id: Any, doc: Mapping[str, Any] | None) -> str:
    if doc is None:
        return f"{summary_id} (missing)"
    return f"{doc.get('youtubeId')} ({doc.get('status')})"


def _split_entries(
    ids: list[Any], docs: Mapping[Any, Mapping[str, Any]], current: str
) -> tuple[list[DemoVideo], list[str]]:
    """Entries on a completed older-version doc (to re-run) and entries not completed."""
    videos: dict[str, DemoVideo] = {}
    unfinished: list[str] = []
    for summary_id in ids:
        doc = docs.get(summary_id)
        if doc is None or doc.get("status") != "completed":
            unfinished.append(_unfinished_label(summary_id, doc))
        elif is_stale(doc, current):
            videos.setdefault(str(doc["youtubeId"]), _video(doc, summary_id=str(summary_id)))
    return sorted(videos.values(), key=lambda video: video.youtube_id), unfinished


def select_demo_library(db: Database, email: str, current: str) -> Selection:
    """The demo user's entries on a completed older-version doc; unfinished ones as a note."""
    entries = db["userVideos"].find({"userId": _demo_user_id(db, email)}, {"videoSummaryId": 1})
    ids = [entry["videoSummaryId"] for entry in entries if entry.get("videoSummaryId") is not None]
    found = db["videoSummaryCache"].find({"_id": {"$in": ids}}, _SUMMARY_FIELDS)
    videos, unfinished = _split_entries(ids, {doc["_id"]: doc for doc in found}, current)
    notes = [f"{len(ids)} library entries; {len(videos)} on an older version."]
    if unfinished:
        notes.append(f"Not completed, left alone: {', '.join(unfinished)}")
    return Selection(videos, "mongo: the demo user's library (userVideos)", notes)


def _owner_label(users: set[Any], demo_id: Any) -> str:
    """``demo``, ``demo+N``, ``1 user``, ``N users`` or ``none``."""
    others = len(users - {demo_id})
    if demo_id in users:
        return f"demo+{others}" if others else "demo"
    if not others:
        return "none"
    return "1 user" if others == 1 else f"{others} users"


def _owner_labels(db: Database, youtube_ids: list[str], demo_id: Any) -> dict[str, str]:
    """Whose library holds each video, from ``userVideos``."""
    owners: dict[str, set[Any]] = {youtube_id: set() for youtube_id in youtube_ids}
    rows = db["userVideos"].find({"youtubeId": {"$in": youtube_ids}}, {"youtubeId": 1, "userId": 1})
    for row in rows:
        owners.setdefault(str(row.get("youtubeId")), set()).add(row.get("userId"))
    return {youtube_id: _owner_label(users, demo_id) for youtube_id, users in owners.items()}


def select_stale(db: Database, current: str, demo_id: Any = None) -> Selection:
    """Every served doc still on an older version, by YouTube id, with its owners."""
    collection = db["videoSummaryCache"]
    done = set(
        collection.distinct(
            "youtubeId",
            {"status": "completed", "pipelineVersion": current, "evalRun": {"$ne": True}},
        )
    )
    docs: dict[str, Mapping[str, Any]] = {}
    for doc in collection.find(stale_query(current), _SUMMARY_FIELDS):
        youtube_id = str(doc.get("youtubeId") or "")
        if youtube_id and youtube_id not in done:
            docs.setdefault(youtube_id, doc)
    labels = _owner_labels(db, sorted(docs), demo_id)
    videos = [_video(docs[youtube_id], owner=labels[youtube_id]) for youtube_id in sorted(docs)]
    notes = ["Every listed video joins the demo user's library when submitted."]
    return Selection(videos, "mongo: videoSummaryCache (stale, served)", notes)


@contextmanager
def open_database(uri: str, name: str) -> Iterator[Database]:
    # Imported here so --urls runs with httpx alone (no pymongo on the host).
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    try:
        with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
            yield client[name]
    except PyMongoError as exc:
        # The type only: the URI (and so the message) may carry credentials.
        raise SourceError(f"MongoDB read failed ({type(exc).__name__}); use --urls") from exc


def load_from_mongo(
    uri: str, name: str, current: str, *, demo_email: str, all_cache: bool
) -> Selection:
    """The demo library (default) or the whole stale cache (``all_cache``)."""
    with open_database(uri, name) as db:
        if not all_cache:
            return select_demo_library(db, demo_email, current)
        demo_id = _demo_user_id(db, demo_email) if demo_email.strip() else None
        return select_stale(db, current, demo_id)
