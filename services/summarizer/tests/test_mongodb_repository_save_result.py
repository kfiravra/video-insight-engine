"""save_structured_result reports whether the row still existed.

A global video purge can delete the row while a run is in flight; the phases
use this flag to stop re-creating Redis, Qdrant and S3 artifacts for it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.repositories.mongodb_repository import MongoDBVideoRepository

_VIDEO_SUMMARY_ID = "0" * 24


def _build_repo(matched_count: int) -> MongoDBVideoRepository:
    collection = MagicMock()
    collection.update_one.return_value = MagicMock(matched_count=matched_count)
    database = MagicMock()
    database.videoSummaryCache = collection
    return MongoDBVideoRepository(database)


def test_should_return_true_when_the_row_was_updated() -> None:
    repo = _build_repo(matched_count=1)

    assert repo.save_structured_result(_VIDEO_SUMMARY_ID, {"status": "completed"}) is True


def test_should_return_false_when_the_row_no_longer_exists() -> None:
    repo = _build_repo(matched_count=0)

    assert repo.save_structured_result(_VIDEO_SUMMARY_ID, {"status": "completed"}) is False
