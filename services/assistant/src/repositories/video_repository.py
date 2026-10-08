"""MongoDB repository for reading video context data."""

from __future__ import annotations

from dataclasses import dataclass, field

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class VideoContext:
    """Extracted video context for the assistant."""

    id: str
    youtube_id: str
    title: str
    creator: str
    summary: str
    takeaways: list[str] = field(default_factory=list)
    tabs: list[dict] = field(default_factory=list)
    output_data: dict | None = None
    language: str = "en"
    source_language: dict | None = None


class MongoVideoRepository:
    """Reads video data from the videoSummaryCache collection."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db["videoSummaryCache"]

    async def get_video_context(self, video_id: str) -> VideoContext | None:
        """Load video context by video ID.

        Tries youtubeId first, falls back to _id lookup. A youtubeId resolves
        to the served version (``isLatest``, else the newest user version) and
        never to an eval run (D25): eval rows share the youtubeId but are
        reachable only by their own _id.

        Args:
            video_id: YouTube video ID or document _id.

        Returns:
            VideoContext if found, None otherwise.
        """
        try:
            doc = await self._collection.find_one(
                {"youtubeId": video_id, "evalRun": {"$ne": True}},
                sort=[("isLatest", -1), ("version", -1)],
            )
            if not doc:
                doc = await self._find_by_id(video_id)
            if not doc:
                return None
            return self._to_entity(doc)
        except Exception:
            logger.exception("failed_to_load_video_context", video_id=video_id)
            return None

    async def _find_by_id(self, video_id: str) -> dict | None:
        """Find by `_id`, accepting either ObjectId-hex or string ids.

        videoSummaryCache uses ObjectId `_id` in production, but tests and
        legacy callers may pass plain strings. Try ObjectId first when the
        input looks like a 24-char hex; fall back to the raw string.
        """
        if ObjectId.is_valid(video_id):
            doc = await self._collection.find_one({"_id": ObjectId(video_id)})
            if doc:
                return doc
        return await self._collection.find_one({"_id": video_id})

    def _to_entity(self, doc: dict) -> VideoContext:
        """Convert a MongoDB document to a VideoContext dataclass.

        Reads three schema shapes:
        - new: ``tabs`` + ``meta`` at top level
        - v2:  ``assembledTabs`` + ``synthesis`` at top level
        - v1:  ``triage.tabs`` + ``synthesis``/``summary``
        """
        tabs = _resolve_tabs(doc)
        summary = _resolve_summary(doc)
        takeaways = _resolve_takeaways(doc)

        output_data = doc.get("output")
        language = doc.get("language", "en") or "en"
        source_language = doc.get("sourceLanguage")

        return VideoContext(
            id=str(doc.get("_id", "")),
            youtube_id=doc.get("youtubeId", ""),
            title=doc.get("title", ""),
            creator=doc.get("creator", ""),
            summary=summary,
            takeaways=takeaways,
            tabs=tabs,
            output_data=output_data,
            language=language,
            source_language=source_language,
        )


# ─── Schema shape resolvers ───
# Cover three known shapes (new / v2 / v1) without forcing a data migration.


def _resolve_tabs(doc: dict) -> list[dict]:
    """Resolve tab list from any known shape."""
    for candidate in (doc.get("tabs"), doc.get("assembledTabs")):
        if isinstance(candidate, list) and candidate:
            return candidate

    triage = doc.get("triage")
    if isinstance(triage, dict):
        triage_tabs = triage.get("tabs")
        if isinstance(triage_tabs, list) and triage_tabs:
            return triage_tabs

    pipeline = doc.get("pipeline")
    if isinstance(pipeline, dict):
        pipeline_triage = pipeline.get("triage")
        if isinstance(pipeline_triage, dict):
            pt_tabs = pipeline_triage.get("tabs")
            if isinstance(pt_tabs, list) and pt_tabs:
                return pt_tabs

    return []


def _resolve_summary(doc: dict) -> str:
    """Resolve a summary string from any known shape."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else None
    synthesis = doc.get("synthesis") if isinstance(doc.get("synthesis"), dict) else None
    summary_block = doc.get("summary") if isinstance(doc.get("summary"), dict) else None

    for source, keys in (
        (meta, ("masterSummary", "tldr")),
        (synthesis, ("masterSummary", "tldr", "summary")),
        (summary_block, ("masterSummary", "tldr")),
    ):
        if source is None:
            continue
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _resolve_takeaways(doc: dict) -> list[str]:
    """Resolve takeaways list from any known shape."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else None
    synthesis = doc.get("synthesis") if isinstance(doc.get("synthesis"), dict) else None
    summary_block = doc.get("summary") if isinstance(doc.get("summary"), dict) else None

    for source, keys in (
        (meta, ("keyTakeaways",)),
        (synthesis, ("keyTakeaways", "takeaways")),
        (summary_block, ("keyTakeaways",)),
    ):
        if source is None:
            continue
        for key in keys:
            value = source.get(key)
            if isinstance(value, list) and value:
                return [str(t) for t in value if t]
    return []
