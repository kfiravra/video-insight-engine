"""Tests for vision LLM frame analysis."""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media.frame_analyzer import (
    _select_top_frames,
    analyze_frames_with_vision,
    parse_vision_response,
)


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed batches retry after a 1 s pause; tests need no real wait."""
    monkeypatch.setattr("src.services.media.frame_analyzer._RETRY_PAUSE_SECONDS", 0.0)


# ─────────────────────────────────────────────────────
# parse_vision_response
# ─────────────────────────────────────────────────────


class TestParseVisionResponse:
    """Test JSON parsing of vision LLM output."""

    def _metadata(self, count: int = 3) -> list[dict]:
        return [
            {
                "index": i,
                "timestamp_sec": i * 60.0,
                "s3_url": f"https://s3/frame_{i}.jpg",
                "original_index": i,
            }
            for i in range(count)
        ]

    def test_valid_json_array(self):
        raw = json.dumps(
            [
                {
                    "frame_index": 0,
                    "scene_type": "code",
                    "content": "Python function",
                    "text_visible": "def foo():",
                    "educational_value": "Shows impl",
                },
                {
                    "frame_index": 1,
                    "scene_type": "slide",
                    "content": "Architecture diagram",
                    "text_visible": "",
                    "educational_value": "System overview",
                },
            ]
        )
        results = parse_vision_response(raw, self._metadata(2))

        assert len(results) == 2
        assert results[0]["scene_type"] == "code"
        assert results[0]["content"] == "Python function"
        assert results[0]["text_visible"] == "def foo():"
        assert results[0]["timestamp_sec"] == 0.0
        assert results[0]["s3_url"] == "https://s3/frame_0.jpg"
        assert results[1]["scene_type"] == "slide"
        assert results[1]["timestamp_sec"] == 60.0

    def test_fenced_json(self):
        raw = (
            "```json\n"
            + json.dumps(
                [
                    {
                        "frame_index": 0,
                        "scene_type": "diagram",
                        "content": "ER diagram",
                        "text_visible": "",
                        "educational_value": "DB schema",
                    },
                ]
            )
            + "\n```"
        )
        results = parse_vision_response(raw, self._metadata(1))

        assert len(results) == 1
        assert results[0]["scene_type"] == "diagram"

    def test_fenced_without_lang(self):
        raw = (
            "```\n"
            + json.dumps(
                [
                    {
                        "frame_index": 0,
                        "scene_type": "table",
                        "content": "Comparison table",
                        "text_visible": "",
                        "educational_value": "Feature comparison",
                    },
                ]
            )
            + "\n```"
        )
        results = parse_vision_response(raw, self._metadata(1))

        assert len(results) == 1

    def test_malformed_json(self):
        results = parse_vision_response("not valid json {{{", self._metadata())
        assert results == []

    def test_empty_string(self):
        assert parse_vision_response("", self._metadata()) == []

    def test_none_text(self):
        assert parse_vision_response(None, self._metadata()) == []

    def test_json_object_not_array(self):
        raw = json.dumps({"frame_index": 0, "scene_type": "code"})
        results = parse_vision_response(raw, self._metadata())
        assert results == []

    def test_missing_fields_get_defaults(self):
        raw = json.dumps([{"frame_index": 0}])
        results = parse_vision_response(raw, self._metadata(1))

        assert len(results) == 1
        assert results[0]["scene_type"] == "other"
        assert results[0]["content"] == ""
        assert results[0]["text_visible"] == ""
        assert results[0]["educational_value"] is None

    def test_frame_index_out_of_range(self):
        """An index naming no frame is dropped — kept, it surfaced as a 0:00 caption."""
        raw = json.dumps([{"frame_index": 99, "scene_type": "code", "content": "test"}])
        results = parse_vision_response(raw, self._metadata(1))

        assert results == []

    def test_duplicate_frame_index_keeps_first(self):
        raw = json.dumps(
            [
                {"frame_index": 0, "scene_type": "code", "content": "first"},
                {"frame_index": 0, "scene_type": "slide", "content": "second"},
            ]
        )
        results = parse_vision_response(raw, self._metadata(2))

        assert [r["content"] for r in results] == ["first"]

    def test_non_dict_items_skipped(self):
        raw = json.dumps(
            [
                "not a dict",
                {"frame_index": 0, "scene_type": "code", "content": "test"},
                42,
            ]
        )
        results = parse_vision_response(raw, self._metadata(1))
        assert len(results) == 1


# ─────────────────────────────────────────────────────
# _select_top_frames
# ─────────────────────────────────────────────────────


class TestSelectTopFrames:
    """Test frame selection by score."""

    def test_selects_top_by_score(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(5):
                p = os.path.join(d, f"frame_{i}.jpg")
                with open(p, "wb") as f:
                    f.write(b"\xff\xd8\xff")
                paths.append(p)

            frames = [
                {"path": paths[i], "total_score": (i + 1) * 0.1, "index": i} for i in range(5)
            ]
            result = _select_top_frames(frames, 3)

            assert len(result) == 3
            # Highest scores first
            assert result[0]["total_score"] == 0.5
            assert result[1]["total_score"] == 0.4

    def test_skips_missing_files(self):
        frames = [
            {"path": "/nonexistent/frame.jpg", "total_score": 0.9, "index": 0},
        ]
        result = _select_top_frames(frames, 5)
        assert result == []

    def test_skips_no_path(self):
        frames = [{"total_score": 0.9, "index": 0}]
        result = _select_top_frames(frames, 5)
        assert result == []

    def test_empty_frames(self):
        assert _select_top_frames([], 5) == []


# ─────────────────────────────────────────────────────
# analyze_frames_with_vision
# ─────────────────────────────────────────────────────


class TestAnalyzeFramesWithVision:
    """Test the main vision analysis function."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_valid_frames(self):
        provider = MagicMock()
        result = await analyze_frames_with_vision([], provider, max_frames=8)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(side_effect=asyncio.TimeoutError)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "frame.jpg")
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

            frames = [{"path": path, "total_score": 0.8, "index": 0, "timestamp": 30}]
            result = await analyze_frames_with_vision(frames, provider, timeout=0.1)
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(side_effect=RuntimeError("API down"))

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "frame.jpg")
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

            frames = [{"path": path, "total_score": 0.8, "index": 0, "timestamp": 30}]
            result = await analyze_frames_with_vision(frames, provider)
            assert result == []

    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        response_json = json.dumps(
            [
                {
                    "frame_index": 0,
                    "scene_type": "code",
                    "content": "Binary search impl",
                    "text_visible": "def search():",
                    "educational_value": "Core algorithm",
                },
            ]
        )
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(return_value=response_json)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "frame.jpg")
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

            frames = [{"path": path, "total_score": 0.8, "index": 5, "timestamp": 90}]
            result = await analyze_frames_with_vision(frames, provider, max_frames=8)

            assert len(result) == 1
            assert result[0]["scene_type"] == "code"
            assert result[0]["content"] == "Binary search impl"
            assert result[0]["timestamp_sec"] == 90

    @pytest.mark.asyncio
    async def test_max_frames_cap(self):
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(return_value="[]")

        with tempfile.TemporaryDirectory() as d:
            frames = []
            for i in range(20):
                path = os.path.join(d, f"frame_{i}.jpg")
                with open(path, "wb") as f:
                    f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
                frames.append(
                    {"path": path, "total_score": i * 0.05, "index": i, "timestamp": i * 10}
                )

            await analyze_frames_with_vision(frames, provider, max_frames=5)

            # Only 5 images were sent, across however many calls ("[]" is
            # retried once, so count first attempts only)
            image_count = sum(
                1
                for call in provider.complete_with_messages.call_args_list
                if call.kwargs["span_metadata"]["attempt"] == 1
                for block in call.args[0][0]["content"]
                if block.get("type") == "image_url"
            )
            assert image_count == 5

    @pytest.mark.asyncio
    async def test_routes_to_primary_model(self):
        """Vision frame analysis routes to the primary model
        (``use_fast_model=False``). Live spot-check on 2026-05-14 showed
        ``openai/gpt-4o-mini`` is only ~20% cheaper for vision (not 6×)
        and hallucinates OCR on dense-text frames — see
        `reports/frame-vision-spotcheck-20260514-101033.json` and the v5
        verification notes in `dev/active/.../tasks.md`. Sonnet is the
        right default until a Haiku-specific override lands."""
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(return_value="[]")

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "frame.jpg")
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

            frames = [{"path": path, "total_score": 0.8, "index": 0, "timestamp": 30}]
            await analyze_frames_with_vision(frames, provider, max_frames=1)

            kwargs = provider.complete_with_messages.call_args.kwargs
            assert kwargs.get("use_fast_model") is False


class TestVisualSubject:
    def test_parse_passes_visual_subject_through(self):
        from src.services.media.frame_analyzer import parse_vision_response

        raw = (
            '[{"frame_index": 0, "scene_type": "card_reveal", "content": "Card", '
            '"text_visible": "", "educational_value": "chase pull", '
            '"visual_subject": "content"}]'
        )
        meta = [{"index": 0, "timestamp_sec": 12.0, "s3_url": "u", "original_index": 3}]

        out = parse_vision_response(raw, meta)

        assert out[0]["visual_subject"] == "content"
        assert out[0]["scene_type"] == "card_reveal"

    def test_missing_visual_subject_defaults_empty(self):
        from src.services.media.frame_analyzer import parse_vision_response

        raw = '[{"frame_index": 0, "scene_type": "other", "content": "x", "text_visible": ""}]'
        out = parse_vision_response(raw, [{"index": 0, "timestamp_sec": 0, "s3_url": ""}])

        assert out[0]["visual_subject"] == ""
