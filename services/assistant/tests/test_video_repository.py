"""Tests for MongoVideoRepository — schema-shape fallback chain."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.repositories.video_repository import (
    MongoVideoRepository,
    VideoContext,
    _resolve_summary,
    _resolve_tabs,
    _resolve_takeaways,
)


def _make_repo() -> tuple[MongoVideoRepository, MagicMock]:
    """Build a repo wrapping a stub Motor collection. Returns (repo, collection_mock)."""
    db = MagicMock()
    collection = MagicMock()
    collection.find_one = AsyncMock()
    db.__getitem__.return_value = collection
    return MongoVideoRepository(db), collection


class TestResolveTabs:
    """Tab fallback chain: new (tabs) > v2 (assembledTabs) > v1 (triage.tabs) > pipeline.triage.tabs."""

    def test_should_prefer_top_level_tabs_when_new_shape(self):
        doc = {
            "tabs": [{"id": "overview", "label": "Overview"}],
            "assembledTabs": [{"id": "should_not_use"}],
            "triage": {"tabs": [{"id": "also_not"}]},
        }
        assert _resolve_tabs(doc) == [{"id": "overview", "label": "Overview"}]

    def test_should_use_assembled_tabs_when_v2(self):
        doc = {
            "assembledTabs": [{"id": "key_points", "label": "Key Points"}],
            "triage": {"tabs": [{"id": "stale"}]},
        }
        assert _resolve_tabs(doc) == [{"id": "key_points", "label": "Key Points"}]

    def test_should_use_triage_tabs_when_v1(self):
        doc = {"triage": {"tabs": [{"id": "concepts"}]}}
        assert _resolve_tabs(doc) == [{"id": "concepts"}]

    def test_should_use_pipeline_triage_tabs_when_only_present(self):
        doc = {"pipeline": {"triage": {"tabs": [{"id": "fallback"}]}}}
        assert _resolve_tabs(doc) == [{"id": "fallback"}]

    def test_should_return_empty_when_no_shape_matches(self):
        assert _resolve_tabs({}) == []
        assert _resolve_tabs({"tabs": None}) == []
        assert _resolve_tabs({"triage": "not a dict"}) == []

    def test_should_skip_empty_lists_in_fallback_chain(self):
        doc = {
            "tabs": [],
            "assembledTabs": [],
            "triage": {"tabs": [{"id": "real"}]},
        }
        assert _resolve_tabs(doc) == [{"id": "real"}]


class TestResolveSummary:
    """Summary fallback: meta.masterSummary > synthesis.masterSummary > legacy synthesis.summary."""

    def test_should_prefer_meta_master_summary(self):
        doc = {
            "meta": {"masterSummary": "from meta", "tldr": "ignored"},
            "synthesis": {"masterSummary": "ignored"},
        }
        assert _resolve_summary(doc) == "from meta"

    def test_should_fall_back_to_meta_tldr(self):
        doc = {"meta": {"tldr": "from tldr"}}
        assert _resolve_summary(doc) == "from tldr"

    def test_should_fall_back_to_synthesis_master_summary(self):
        doc = {"synthesis": {"masterSummary": "from synthesis"}}
        assert _resolve_summary(doc) == "from synthesis"

    def test_should_read_legacy_synthesis_summary_field(self):
        doc = {"synthesis": {"summary": "old style"}}
        assert _resolve_summary(doc) == "old style"

    def test_should_return_empty_when_nothing_present(self):
        assert _resolve_summary({}) == ""
        assert _resolve_summary({"synthesis": "not a dict"}) == ""
        assert _resolve_summary({"meta": {"masterSummary": "  "}}) == ""


class TestResolveTakeaways:
    """Takeaways fallback: meta.keyTakeaways > synthesis.keyTakeaways > legacy synthesis.takeaways."""

    def test_should_prefer_meta_key_takeaways(self):
        doc = {
            "meta": {"keyTakeaways": ["a", "b"]},
            "synthesis": {"keyTakeaways": ["x"]},
        }
        assert _resolve_takeaways(doc) == ["a", "b"]

    def test_should_fall_back_to_synthesis_key_takeaways(self):
        doc = {"synthesis": {"keyTakeaways": ["one", "two"]}}
        assert _resolve_takeaways(doc) == ["one", "two"]

    def test_should_read_legacy_takeaways_field(self):
        doc = {"synthesis": {"takeaways": ["legacy1"]}}
        assert _resolve_takeaways(doc) == ["legacy1"]

    def test_should_skip_empty_values(self):
        doc = {"synthesis": {"keyTakeaways": ["valid", "", None, "also valid"]}}
        assert _resolve_takeaways(doc) == ["valid", "also valid"]

    def test_should_return_empty_when_nothing_present(self):
        assert _resolve_takeaways({}) == []
        assert _resolve_takeaways({"synthesis": {"keyTakeaways": []}, "meta": {}}) == []


class TestGetVideoContext:
    """End-to-end get_video_context with various schema shapes."""

    async def test_should_return_none_when_not_found(self):
        repo, collection = _make_repo()
        collection.find_one.return_value = None
        result = await repo.get_video_context("missing")
        assert result is None

    async def test_should_resolve_youtube_id_to_served_non_eval_version(self):
        # D25: eval rows share the youtubeId; a youtubeId lookup must land on
        # the served (isLatest, else newest) user version, never an eval run.
        repo, collection = _make_repo()
        collection.find_one.return_value = None

        await repo.get_video_context("abc123")

        first_call = collection.find_one.call_args_list[0]
        assert (first_call.args[0], first_call.kwargs["sort"]) == (
            {"youtubeId": "abc123", "evalRun": {"$ne": True}},
            [("isLatest", -1), ("version", -1)],
        )

    async def test_should_map_new_shape_doc_to_video_context(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            {
                "_id": "id1",
                "youtubeId": "abc123",
                "title": "Test Video",
                "creator": "Test Creator",
                "meta": {
                    "masterSummary": "New shape summary",
                    "keyTakeaways": ["takeaway 1", "takeaway 2"],
                },
                "tabs": [{"id": "overview", "label": "Overview", "emoji": "\U0001f4dd"}],
                "language": "en",
            },
        ]
        ctx = await repo.get_video_context("abc123")

        assert ctx is not None
        assert isinstance(ctx, VideoContext)
        assert ctx.youtube_id == "abc123"
        assert ctx.summary == "New shape summary"
        assert ctx.takeaways == ["takeaway 1", "takeaway 2"]
        assert ctx.tabs[0]["id"] == "overview"

    async def test_should_populate_source_language_from_doc(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            {
                "_id": "id_src",
                "youtubeId": "translated",
                "title": "Translated Video",
                "creator": "Creator",
                "meta": {"masterSummary": "English promoted summary"},
                "language": "en",
                "sourceLanguage": {
                    "code": "he",
                    "name": "Hebrew",
                    "isRTL": True,
                    "meta": {"masterSummary": "תקציר בעברית"},
                    "tabs": [{"id": "key_points", "label": "נקודות מפתח"}],
                },
            },
        ]
        ctx = await repo.get_video_context("translated")

        assert ctx is not None
        assert ctx.language == "en"
        assert ctx.source_language is not None
        assert ctx.source_language["code"] == "he"
        assert ctx.source_language["meta"]["masterSummary"] == "תקציר בעברית"

    async def test_should_leave_source_language_none_when_absent(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            {
                "_id": "id_nosrc",
                "youtubeId": "english",
                "title": "English Video",
                "creator": "Creator",
                "meta": {"masterSummary": "ok"},
            },
        ]
        ctx = await repo.get_video_context("english")

        assert ctx is not None
        assert ctx.source_language is None

    async def test_should_map_v2_shape_doc_with_synthesis_master_summary(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            {
                "_id": "id2",
                "youtubeId": "v2vid",
                "title": "V2 Video",
                "creator": "V2 Creator",
                "synthesis": {
                    "masterSummary": "V2 shape master summary",
                    "keyTakeaways": ["v2 takeaway"],
                },
                "assembledTabs": [{"id": "key_points", "label": "Key Points"}],
            },
        ]
        ctx = await repo.get_video_context("v2vid")

        assert ctx is not None
        assert ctx.summary == "V2 shape master summary"
        assert ctx.takeaways == ["v2 takeaway"]
        assert ctx.tabs[0]["id"] == "key_points"

    async def test_should_map_v1_legacy_shape_via_triage_and_summary(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            {
                "_id": "id3",
                "youtubeId": "v1vid",
                "title": "Legacy",
                "creator": "Old",
                "synthesis": {
                    "summary": "legacy summary",
                    "takeaways": ["legacy takeaway"],
                },
                "triage": {"tabs": [{"id": "concepts", "label": "Concepts"}]},
            },
        ]
        ctx = await repo.get_video_context("v1vid")

        assert ctx is not None
        assert ctx.summary == "legacy summary"
        assert ctx.takeaways == ["legacy takeaway"]
        assert ctx.tabs[0]["id"] == "concepts"

    async def test_should_fall_back_to_id_lookup_when_youtube_id_misses(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            None,
            {
                "_id": "raw_id",
                "youtubeId": "any",
                "title": "Found by _id",
                "creator": "C",
                "meta": {"masterSummary": "ok"},
            },
        ]
        ctx = await repo.get_video_context("raw_id")
        assert ctx is not None
        assert ctx.title == "Found by _id"
        # 2 calls: youtubeId lookup, then string-_id lookup (input is not a 24-hex id)
        assert collection.find_one.call_count == 2

    async def test_should_lookup_by_objectid_when_input_is_24hex(self):
        """Regression: 24-char hex strings must be converted to ObjectId for _id lookup.

        Production `_id` fields are stored as ObjectId, not string. A naive string
        lookup misses every document — manifested as `NotFoundError: Video not
        found` despite the row existing.
        """
        from bson import ObjectId  # local import keeps test runner-friendly

        oid_hex = "69e485654aebac28044d54ca"
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            None,  # youtubeId miss
            {
                "_id": ObjectId(oid_hex),
                "youtubeId": "WkHdkwDQJ5o",
                "title": "Found by ObjectId",
                "creator": "C",
                "meta": {"masterSummary": "ok"},
            },
        ]
        ctx = await repo.get_video_context(oid_hex)
        assert ctx is not None
        assert ctx.title == "Found by ObjectId"

        # First call: youtubeId. Second call: ObjectId-typed _id.
        assert collection.find_one.call_count == 2
        second_call_filter = collection.find_one.await_args_list[1].args[0]
        assert isinstance(second_call_filter["_id"], ObjectId)
        assert str(second_call_filter["_id"]) == oid_hex

    async def test_should_fall_back_to_string_id_when_objectid_lookup_misses(self):
        """If ObjectId lookup returns None, still attempt a raw-string `_id` query."""
        from bson import ObjectId

        oid_hex = "69e485654aebac28044d54ca"
        repo, collection = _make_repo()
        collection.find_one.side_effect = [
            None,  # youtubeId
            None,  # ObjectId(_id)
            {
                "_id": oid_hex,
                "youtubeId": "x",
                "title": "Found via string id",
                "creator": "C",
                "meta": {"masterSummary": "ok"},
            },
        ]
        ctx = await repo.get_video_context(oid_hex)
        assert ctx is not None
        assert ctx.title == "Found via string id"
        assert collection.find_one.call_count == 3

        types = [
            call.args[0]["_id"].__class__.__name__
            for call in collection.find_one.await_args_list[1:]
        ]
        assert types == ["ObjectId", "str"]

    async def test_should_return_none_when_query_raises(self):
        repo, collection = _make_repo()
        collection.find_one.side_effect = RuntimeError("mongo down")
        ctx = await repo.get_video_context("bad")
        assert ctx is None
