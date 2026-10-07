"""MongoDB implementation of video repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection
from pymongo.database import Database

from src.models.schemas import ErrorCode, ProcessingStatus


def _utc_now() -> datetime:
    """Get current UTC time in timezone-aware format."""
    return datetime.now(timezone.utc)


class MongoDBVideoRepository:
    """MongoDB implementation of VideoRepository protocol."""

    def __init__(self, database: Database):
        self._db = database
        self._collection = database.videoSummaryCache

    @property
    def alerts_collection(self) -> Collection:
        """``llm_alerts`` — shared with llm-common's cost callback and the admin UI."""
        return self._db["llm_alerts"]

    def get_video_summary(self, video_summary_id: str) -> dict | None:
        """Get video summary cache entry."""
        return self._collection.find_one({"_id": ObjectId(video_summary_id)})

    def update_status(
        self,
        video_summary_id: str,
        status: ProcessingStatus,
        error_message: str | None = None,
        error_code: ErrorCode | None = None,
    ) -> None:
        """Update processing status."""
        update: dict[str, Any] = {
            "status": status.value,
            "updatedAt": _utc_now(),
        }

        if error_message:
            update["errorMessage"] = error_message
        if error_code:
            update["errorCode"] = error_code.value

        self._collection.update_one({"_id": ObjectId(video_summary_id)}, {"$set": update})

    def find_stalled_processing(self, older_than: datetime, limit: int = 50) -> list[dict]:
        """Rows still ``processing`` whose last write predates ``older_than``.

        Backs the stall sweeper: ``updatedAt`` is only bumped on status
        transitions and result saves, so a row untouched for longer than any
        legitimate run almost certainly belongs to a producer that died.
        Oldest first so a backlog drains in order; ``limit`` bounds one sweep.
        """
        cursor = (
            self._collection.find(
                {"status": ProcessingStatus.PROCESSING.value, "updatedAt": {"$lt": older_than}},
                projection={"_id": 1, "youtubeId": 1, "updatedAt": 1, "status": 1},
            )
            .sort("updatedAt", 1)
            .limit(limit)
        )
        return list(cursor)

    def mark_stalled_failed(
        self,
        video_summary_id: str,
        older_than: datetime,
        error_message: str,
        error_code: ErrorCode,
    ) -> bool:
        """Flip a stalled row to ``failed`` only if it is *still* stalled.

        Compare-and-set on ``status == processing`` and the stale ``updatedAt``:
        a producer that resumed (or a re-dispatch that restarted the run)
        between the sweeper's find and this write bumps ``updatedAt``, so the
        filter no longer matches and the live run is left alone. Returns
        ``True`` when the row was flipped.
        """
        result = self._collection.update_one(
            {
                "_id": ObjectId(video_summary_id),
                "status": ProcessingStatus.PROCESSING.value,
                "updatedAt": {"$lt": older_than},
            },
            {
                "$set": {
                    "status": ProcessingStatus.FAILED.value,
                    "errorMessage": error_message,
                    "errorCode": error_code.value,
                    "updatedAt": _utc_now(),
                }
            },
        )
        return result.matched_count == 1

    # Allowlist of fields the pipeline may write via save_structured_result.
    # New pipeline uses meta+tabs as the canonical shape.
    _ALLOWED_RESULT_KEYS = frozenset(
        {
            "meta",
            "tabs",
            "triage",
            "enrichment",
            "assembledMeta",
            "assembledTabs",
            "pipeline",
            # Canonical pipeline-version stamp (packages/shared/src/config/
            # pipeline-version.json) — read by the api's serve/regen check.
            "pipelineVersion",
            "status",
            "title",
            "creator",
            "duration",
            "thumbnailUrl",
            "youtubeId",
            "rawTranscriptRef",
            # Per-run transcript provenance block. The runner writes it via
            # set_transcript_meta; allow-listed so a future result-dict writer
            # is not silently dropped here.
            "transcriptMeta",
            "generation",
            # Degraded-run flag (dropped extraction batches / critical coverage) —
            # top-level mirror of meta.degraded for admin queries.
            "degraded",
            "descriptionAnalysis",
            "channel",
            "processedAt",
            "processingTimeMs",
            # Language support — sourceLanguage is the nested original-language block
            # written by the translation phase for non-English videos. Top-level
            # tabs/meta/synthesis are English-primary post-translation.
            "language",
            "isRTL",
            "sourceLanguage",
            # Backward compat: older pipeline shapes / Redis-cached docs may include these
            "output",
            "summary",
            "outputType",
            "context",
            "intent",
        }
    )

    def save_structured_result(self, video_summary_id: str, result: dict) -> bool:
        """Save structured pipeline result (triage-driven pipeline).

        Stores allowlisted fields from result — prevents injection of
        arbitrary fields like _id or userId. Returns False when no row
        matched: the row was deleted while the run was in flight (global
        purge), so callers must not re-create its artifacts.
        """
        filtered = {k: v for k, v in result.items() if k in self._ALLOWED_RESULT_KEYS}
        filtered["updatedAt"] = _utc_now()
        # A completed run consumes the API's bypassCache marker — clearing it
        # keeps future serves of this row on the normal cache path.
        outcome = self._collection.update_one(
            {"_id": ObjectId(video_summary_id)},
            {"$set": filtered, "$unset": {"forceRefresh": ""}},
        )
        return outcome.matched_count == 1

    def set_transcript_meta(self, video_summary_id: str, meta: dict[str, Any]) -> None:
        """Persist the per-run transcript provenance block.

        Written MID-run (after the transcript phase, before assembly), so it
        must not consume the API's bypassCache marker — save_structured_result
        also ``$unset``s ``forceRefresh`` and cannot be reused here.
        """
        self._collection.update_one(
            {"_id": ObjectId(video_summary_id)},
            {"$set": {"transcriptMeta": meta, "updatedAt": _utc_now()}},
        )

    def set_pipeline_timing(self, video_summary_id: str, timing: dict[str, Any]) -> None:
        """Persist the run's ``pipeline.timing`` block (phase walls, LLM calls,
        downloads, SSE milestones).

        Written AFTER assembly's ``save_structured_result`` (which replaces the
        whole ``pipeline`` object) and for failed runs too, so it is a dotted
        ``$set``. No ``updatedAt`` bump: that field is the stall sweeper's
        liveness signal and this write carries no progress.
        """
        self._collection.update_one(
            {"_id": ObjectId(video_summary_id)}, {"$set": {"pipeline.timing": timing}}
        )

    def clear_transcript_meta(self, video_summary_id: str) -> None:
        """Drop the previous run's transcript provenance block.

        Rows are re-run on the same ``_id`` (regen, failed retry, stall
        re-dispatch) and may complete via the Redis fast path, so an earlier
        failed run's block would otherwise survive. No ``updatedAt`` bump:
        that field is the stall sweeper's liveness signal, and the PROCESSING
        transition right before this call already bumped it.
        """
        self._collection.update_one(
            {"_id": ObjectId(video_summary_id)}, {"$unset": {"transcriptMeta": ""}}
        )

    def increment_retry(self, video_summary_id: str) -> int:
        """Increment retry count and return new value."""
        result = self._collection.find_one_and_update(
            {"_id": ObjectId(video_summary_id)}, {"$inc": {"retryCount": 1}}, return_document=True
        )
        return result.get("retryCount", 1) if result else 1

    def set_provider_config(
        self,
        video_summary_id: str,
        providers: dict[str, Any] | None = None,
    ) -> None:
        """Store provider config for dev tools override."""
        if providers:
            self._collection.update_one(
                {"_id": ObjectId(video_summary_id)},
                {"$set": {"providerConfig": providers, "updatedAt": _utc_now()}},
            )
