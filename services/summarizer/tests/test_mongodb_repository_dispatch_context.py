"""Dispatch-context persistence (billing-roles P0.4 / B2).

``set_dispatch_context`` stamps the dispatching user and request on the shared
``videoSummaryCache`` row so the SSE producer can attribute ``llm_usage``. It
always overwrites: a field the new dispatch lacks is ``$unset`` so a previous
dispatcher's id never lingers, and it never bumps ``updatedAt`` (the stall
sweeper's liveness signal).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bson import ObjectId

from src.repositories.mongodb_repository import MongoDBVideoRepository

_VIDEO_SUMMARY_ID = "0" * 24


def _build_repo() -> tuple[MongoDBVideoRepository, MagicMock]:
    """Repository over a MagicMock collection so update_one args can be read back."""
    collection = MagicMock()
    database = MagicMock()
    database.videoSummaryCache = collection
    return MongoDBVideoRepository(database), collection


def _update(collection: MagicMock) -> dict:
    collection.update_one.assert_called_once()
    return collection.update_one.call_args.args[1]


def test_set_dispatch_context_targets_row_by_object_id() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, "user_1", "req-1")

    assert collection.update_one.call_args.args[0] == {"_id": ObjectId(_VIDEO_SUMMARY_ID)}


def test_set_dispatch_context_sets_both_ids_when_present() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, "user_1", "req-1")

    assert _update(collection) == {"$set": {"userId": "user_1", "requestId": "req-1"}}


def test_set_dispatch_context_unsets_user_id_when_absent() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, None, "req-1")

    assert _update(collection) == {"$set": {"requestId": "req-1"}, "$unset": {"userId": ""}}


def test_set_dispatch_context_unsets_request_id_when_absent() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, "user_1", None)

    assert _update(collection) == {"$set": {"userId": "user_1"}, "$unset": {"requestId": ""}}


def test_set_dispatch_context_unsets_both_when_neither_is_present() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, None, None)

    assert _update(collection) == {"$unset": {"userId": "", "requestId": ""}}


def test_set_dispatch_context_treats_an_empty_string_as_absent() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, "", "req-1")

    assert _update(collection)["$unset"] == {"userId": ""}


def test_set_dispatch_context_does_not_bump_updated_at() -> None:
    repo, collection = _build_repo()

    repo.set_dispatch_context(_VIDEO_SUMMARY_ID, "user_1", "req-1")

    assert "updatedAt" not in _update(collection)["$set"]
