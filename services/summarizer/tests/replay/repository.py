"""In-memory stand-in for ``MongoDBVideoRepository`` (the pipeline's write surface)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from src.models.schemas import ErrorCode, ProcessingStatus


@dataclass
class StatusUpdate:
    status: str
    error_message: str | None
    error_code: str | None


@dataclass
class _RecordingCollection:
    """``_collection.update_one`` target (assembly's S3 ``rawTranscriptRef`` write)."""

    updates: list[dict[str, Any]] = field(default_factory=list)

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        self.updates.append(copy.deepcopy(update))


@dataclass
class InMemoryRepository:
    """Captures every write ``stream_summarization`` makes to the cache row."""

    _collection: _RecordingCollection = field(default_factory=_RecordingCollection)
    statuses: list[StatusUpdate] = field(default_factory=list)
    saved_result: dict[str, Any] | None = None
    transcript_meta: dict[str, Any] | None = None
    pipeline_timing: dict[str, Any] | None = None

    def update_status(
        self,
        video_summary_id: str,
        status: ProcessingStatus,
        error_message: str | None = None,
        error_code: ErrorCode | None = None,
    ) -> None:
        self.statuses.append(
            StatusUpdate(
                status=status.value,
                error_message=error_message,
                error_code=error_code.value if error_code else None,
            )
        )

    def save_structured_result(self, video_summary_id: str, result: dict[str, Any]) -> bool:
        self.saved_result = copy.deepcopy(result)
        return True

    def set_transcript_meta(self, video_summary_id: str, meta: dict[str, Any]) -> None:
        self.transcript_meta = copy.deepcopy(meta)

    def clear_transcript_meta(self, video_summary_id: str) -> None:
        self.transcript_meta = None

    def set_pipeline_timing(self, video_summary_id: str, timing: dict[str, Any]) -> None:
        self.pipeline_timing = copy.deepcopy(timing)
