"""Tests for YouTube service (youtube.py).

Tests video data extraction, category detection, and error handling.
"""

from unittest.mock import patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.media import download_utils
from src.services.video.youtube import (
    VALID_CATEGORIES,
    Chapter,
    SubtitleSegment,
    VideoData,
    _build_display_tags,
    _build_yt_dlp_opts,
    _clean_subtitle_text,
    _detect_category,
    _extract_hashtags,
    _fetch_subtitle_data_sync,
    _parse_chapters,
    extract_video_context,
    extract_video_data,
)


class TestExtractHashtags:
    """Tests for hashtag extraction from description."""

    def test_extracts_single_hashtag(self):
        """Test extracting a single hashtag."""
        result = _extract_hashtags("Check out #python today!")
        assert result == ["python"]

    def test_extracts_multiple_hashtags(self):
        """Test extracting multiple hashtags."""
        result = _extract_hashtags("Learn #python and #javascript #webdev")
        assert result == ["python", "javascript", "webdev"]

    def test_returns_lowercase(self):
        """Test hashtags are lowercased."""
        result = _extract_hashtags("#Python #JAVASCRIPT")
        assert result == ["python", "javascript"]

    def test_empty_description(self):
        """Test empty description returns empty list."""
        result = _extract_hashtags("")
        assert result == []

    def test_no_hashtags(self):
        """Test description without hashtags."""
        result = _extract_hashtags("This is a description without hashtags.")
        assert result == []

    def test_none_description(self):
        """Test None description returns empty list."""
        result = _extract_hashtags(None)
        assert result == []


class TestBuildDisplayTags:
    """Tests for building display tags from video metadata."""

    def test_builds_tags_from_video_tags(self):
        """Test building display tags from video tags."""
        result = _build_display_tags(
            tags=["Python", "Programming", "Tutorial"],
            hashtags=[],
            max_tags=6,
        )
        assert result == ["Python", "Programming", "Tutorial"]

    def test_builds_tags_from_hashtags(self):
        """Test building display tags from hashtags."""
        result = _build_display_tags(
            tags=[],
            hashtags=["python", "code"],
            max_tags=6,
        )
        # Hashtags are capitalized
        assert result == ["Python", "Code"]

    def test_deduplicates_tags(self):
        """Test that duplicate tags are removed."""
        result = _build_display_tags(
            tags=["Python", "python", "PYTHON"],
            hashtags=["python"],
            max_tags=6,
        )
        assert result == ["Python"]

    def test_respects_max_tags(self):
        """Test that max_tags limit is respected."""
        result = _build_display_tags(
            tags=["Tag1", "Tag2", "Tag3", "Tag4", "Tag5", "Tag6", "Tag7", "Tag8"],
            hashtags=[],
            max_tags=6,
        )
        assert len(result) == 6

    def test_filters_short_tags(self):
        """Test that short tags are filtered out."""
        result = _build_display_tags(
            tags=["AI", "Go", "Python", "JS"],
            hashtags=[],
            min_length=3,
        )
        assert result == ["Python"]


class TestParseChapters:
    """Tests for parsing chapters from yt-dlp info dict."""

    def test_parses_chapters(self):
        """Test parsing chapters with valid data."""
        info = {
            "chapters": [
                {"start_time": 0, "end_time": 60, "title": "Introduction"},
                {"start_time": 60, "end_time": 180, "title": "Main Content"},
                {"start_time": 180, "end_time": 300, "title": "Conclusion"},
            ]
        }
        chapters = _parse_chapters(info)

        assert len(chapters) == 3
        assert chapters[0].start_time == 0.0
        assert chapters[0].end_time == 60.0
        assert chapters[0].title == "Introduction"
        assert chapters[1].title == "Main Content"

    def test_handles_missing_chapters(self):
        """Test handling missing chapters gracefully."""
        info = {}
        chapters = _parse_chapters(info)
        assert chapters == []

    def test_handles_empty_chapters(self):
        """Test handling empty chapters list."""
        info = {"chapters": []}
        chapters = _parse_chapters(info)
        assert chapters == []

    def test_handles_missing_fields(self):
        """Test handling chapters with missing fields."""
        info = {
            "chapters": [
                {"start_time": 0, "title": "Intro"},  # Missing end_time
                {"start_time": 60},  # Missing end_time and title
            ]
        }
        chapters = _parse_chapters(info)

        assert len(chapters) == 2
        assert chapters[0].end_time == 0.0  # Defaults to start_time
        assert chapters[1].title == "Untitled"


class TestCleanSubtitleText:
    """Tests for cleaning subtitle text artifacts."""

    def test_removes_music_annotations(self):
        """Test removing [Music] annotations."""
        result = _clean_subtitle_text("Hello [Music] World")
        assert result == "Hello World"

    def test_removes_applause_annotations(self):
        """Test removing [Applause] annotations."""
        result = _clean_subtitle_text("Thank you [Applause] everyone")
        assert result == "Thank you everyone"

    def test_removes_music_notes(self):
        """Test removing music notes."""
        result = _clean_subtitle_text("Hello world")
        assert result == "Hello world"

    def test_normalizes_whitespace(self):
        """Test normalizing excessive whitespace."""
        result = _clean_subtitle_text("Hello    world   test")
        assert result == "Hello world test"

    def test_case_insensitive(self):
        """Test case-insensitive removal of annotations."""
        result = _clean_subtitle_text("[MUSIC] test [music] [MuSiC]")
        assert result == "test"


class TestExtractVideoContext:
    """Tests for extracting video context from yt-dlp info."""

    @patch("src.services.video.youtube._load_category_rules")
    def test_extracts_context(self, mock_category_rules):
        """Test extracting full video context."""
        mock_category_rules.return_value = {
            "detection_config": {
                "llm_fallback_threshold": 0.4,
                "weights": {
                    "keywords": 0.40,
                    "youtube_category": 0.30,
                    "title": 0.15,
                    "channel": 0.15,
                },
            },
            "categories": {
                "coding": {
                    "keywords": {
                        "primary": ["python", "programming"],
                        "secondary": ["tutorial"],
                    },
                    "youtube_categories": {
                        "primary": ["Science & Technology"],
                        "secondary": ["Education"],
                    },
                    "channel_patterns": [],
                    "title_patterns": ["tutorial"],
                },
            },
            "default_category": "standard",
        }

        info = {
            "categories": ["Science & Technology"],
            "tags": ["Python", "Tutorial", "Programming"],
        }
        description = "Learn Python #programming #tutorial"

        context = extract_video_context(info, description)

        assert context.youtube_category == "Science & Technology"
        assert context.category == "coding"
        assert "Python" in context.tags
        assert len(context.display_tags) <= 6

    @patch("src.services.video.youtube._load_category_rules")
    def test_handles_missing_category(self, mock_category_rules):
        """Test handling videos without category."""
        mock_category_rules.return_value = {
            "detection_config": {
                "llm_fallback_threshold": 0.4,
                "weights": {
                    "keywords": 0.40,
                    "youtube_category": 0.30,
                    "title": 0.15,
                    "channel": 0.15,
                },
            },
            "categories": {},
            "default_category": "standard",
        }

        info = {"tags": ["video"]}
        context = extract_video_context(info, "")

        assert context.youtube_category is None
        assert context.category == "standard"


class TestVideoDataClass:
    """Tests for VideoData dataclass methods."""

    def test_has_chapters_true(self):
        """Test has_chapters returns True when chapters exist."""
        video = VideoData(
            video_id="test123",
            title="Test",
            channel="Test Channel",
            duration=300,
            thumbnail_url=None,
            description="",
            chapters=[Chapter(start_time=0, end_time=60, title="Intro")],
            subtitles=[],
        )
        assert video.has_chapters is True

    def test_has_chapters_false(self):
        """Test has_chapters returns False when no chapters."""
        video = VideoData(
            video_id="test123",
            title="Test",
            channel="Test Channel",
            duration=300,
            thumbnail_url=None,
            description="",
            chapters=[],
            subtitles=[],
        )
        assert video.has_chapters is False

    def test_transcript_text(self):
        """Test transcript_text joins subtitle segments."""
        video = VideoData(
            video_id="test123",
            title="Test",
            channel="Test Channel",
            duration=300,
            thumbnail_url=None,
            description="",
            chapters=[],
            subtitles=[
                SubtitleSegment(text="Hello", start=0, duration=1),
                SubtitleSegment(text="world", start=1, duration=1),
            ],
        )
        assert video.transcript_text == "Hello world"

    def test_get_chapter_transcript(self):
        """Test getting transcript for specific chapter."""
        video = VideoData(
            video_id="test123",
            title="Test",
            channel="Test Channel",
            duration=300,
            thumbnail_url=None,
            description="",
            chapters=[
                Chapter(start_time=0, end_time=10, title="Intro"),
                Chapter(start_time=10, end_time=20, title="Main"),
            ],
            subtitles=[
                SubtitleSegment(text="Hello", start=0, duration=5),
                SubtitleSegment(text="intro", start=5, duration=5),
                SubtitleSegment(text="main", start=10, duration=5),
                SubtitleSegment(text="content", start=15, duration=5),
            ],
        )

        intro_transcript = video.get_chapter_transcript(0)
        assert intro_transcript == "Hello intro"

        main_transcript = video.get_chapter_transcript(1)
        assert main_transcript == "main content"

    def test_get_chapter_transcript_invalid_index(self):
        """Test getting transcript for invalid chapter index."""
        video = VideoData(
            video_id="test123",
            title="Test",
            channel="Test Channel",
            duration=300,
            thumbnail_url=None,
            description="",
            chapters=[],
            subtitles=[],
        )
        assert video.get_chapter_transcript(0) == ""
        assert video.get_chapter_transcript(-1) == ""


class TestExtractVideoDataAsync:
    """Tests for async video data extraction."""

    @pytest.fixture
    def sample_yt_dlp_info(self):
        """Sample yt-dlp info dict."""
        return {
            "id": "test123",
            "title": "Test Video",
            "uploader": "Test Channel",
            "duration": 300,
            "description": "Test description #python",
            "upload_date": "20240101",
            "categories": ["Science & Technology"],
            "tags": ["python", "tutorial"],
            "thumbnails": [{"url": "https://example.com/thumb.jpg"}],
            "chapters": [
                {"start_time": 0, "end_time": 60, "title": "Intro"},
            ],
            "automatic_captions": {},
            "subtitles": {},
        }

    @patch("src.services.video.youtube._extract_video_data_sync")
    async def test_extracts_video_data(self, mock_extract):
        """Test async extraction wrapper."""
        mock_extract.return_value = VideoData(
            video_id="test123",
            title="Test Video",
            channel="Test Channel",
            duration=300,
            thumbnail_url="https://example.com/thumb.jpg",
            description="Test",
            chapters=[],
            subtitles=[],
        )

        result = await extract_video_data("test123")

        assert result.video_id == "test123"
        assert result.title == "Test Video"
        mock_extract.assert_called_once_with("test123")

    @patch("src.services.video.youtube._extract_with_retry")
    def test_raises_error_on_unavailable_video(self, mock_extract):
        """Test error handling for unavailable video."""
        mock_extract.return_value = None

        from src.services.video.youtube import _extract_video_data_sync

        with pytest.raises(TranscriptError) as exc_info:
            _extract_video_data_sync("invalid_id")

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE

    @patch("src.services.video.youtube._extract_with_retry")
    def test_raises_error_on_live_stream(self, mock_extract):
        """Test error handling for live streams."""
        mock_extract.return_value = {"is_live": True}

        from src.services.video.youtube import _extract_video_data_sync

        with pytest.raises(TranscriptError) as exc_info:
            _extract_video_data_sync("live_stream_id")

        assert exc_info.value.code == ErrorCode.LIVE_STREAM

    @patch("src.services.video.youtube._extract_with_retry")
    def test_handles_missing_metadata(self, mock_extract, sample_yt_dlp_info):
        """Test handling missing optional metadata."""
        # Remove optional fields
        sample_yt_dlp_info.pop("uploader")
        sample_yt_dlp_info.pop("thumbnails")
        sample_yt_dlp_info["channel"] = "Fallback Channel"
        mock_extract.return_value = sample_yt_dlp_info

        from src.services.video.youtube import _extract_video_data_sync

        result = _extract_video_data_sync("test123")

        assert result.channel == "Fallback Channel"
        # Should use default YouTube thumbnail URL
        assert "img.youtube.com" in result.thumbnail_url


class TestRateLimitHandling:
    """Tests for rate limit detection and retry behavior."""

    @patch("src.services.video.youtube._extract_with_retry")
    def test_extraction_failure_raises_transcript_error(self, mock_extract):
        """Test that extraction failure wraps in TranscriptError."""
        mock_extract.side_effect = ConnectionError("Connection failed")

        from src.services.video.youtube import _extract_video_data_sync

        with pytest.raises(TranscriptError) as exc_info:
            _extract_video_data_sync("test123")

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE
        assert "Connection failed" in str(exc_info.value)


class TestSubtitleFetchErrorCode:
    """The timedtext fetch classifies its failure into a caption_fetch_error
    code at the except site, so callers never re-inspect exception types."""

    def _http_error(self, status: int):
        import requests

        response = requests.models.Response()
        response.status_code = status
        return requests.exceptions.HTTPError(f"{status} error", response=response)

    @patch("src.services.video.youtube._fetch_subtitle_data_sync")
    def test_direct_429_reports_http_429(self, mock_fetch):
        from src.services.video.youtube import _fetch_subtitles_from_url_sync

        mock_fetch.side_effect = self._http_error(429)

        assert _fetch_subtitles_from_url_sync("http://example/timedtext") == ([], "http_429")

    @patch("src.services.video.youtube._fetch_subtitle_data_sync")
    def test_tenacity_wrapped_429_reports_http_429(self, mock_fetch):
        """Real failures arrive as tenacity RetryError wrapping the HTTPError."""
        import tenacity

        from src.services.video.youtube import _fetch_subtitles_from_url_sync

        attempt = tenacity.Future(attempt_number=2)
        attempt.set_exception(self._http_error(429))
        mock_fetch.side_effect = tenacity.RetryError(attempt)

        assert _fetch_subtitles_from_url_sync("http://example/timedtext") == ([], "http_429")

    @patch("src.services.video.youtube._fetch_subtitle_data_sync")
    def test_403_reports_http_403(self, mock_fetch):
        """Non-429 HTTP failures keep their status so a 403 wall is distinguishable
        from throttling and never trips the rate-limit negative cache."""
        from src.services.video.youtube import _fetch_subtitles_from_url_sync

        mock_fetch.side_effect = self._http_error(403)

        assert _fetch_subtitles_from_url_sync("http://example/timedtext") == ([], "http_403")

    @patch("src.services.video.youtube._fetch_subtitle_data_sync")
    def test_connection_error_reports_request(self, mock_fetch):
        import requests

        from src.services.video.youtube import _fetch_subtitles_from_url_sync

        mock_fetch.side_effect = requests.exceptions.ConnectionError("reset by peer")

        assert _fetch_subtitles_from_url_sync("http://example/timedtext") == ([], "request")

    @patch("src.services.video.youtube._fetch_subtitle_data_sync")
    def test_value_error_reports_parse(self, mock_fetch):
        """Undecodable or oversized bodies surface as a parse failure."""
        from src.services.video.youtube import _fetch_subtitles_from_url_sync

        mock_fetch.side_effect = ValueError("bad json")

        assert _fetch_subtitles_from_url_sync("http://example/timedtext") == ([], "parse")

    @patch("src.services.video.youtube._fetch_subtitle_data_sync")
    def test_body_without_events_reports_no_error(self, mock_fetch):
        """A well-formed body with no speech is not a fetch error — the caller
        decides whether that means 'empty'."""
        from src.services.video.youtube import _fetch_subtitles_from_url_sync

        mock_fetch.return_value = {"wireMagic": "pb3"}

        assert _fetch_subtitles_from_url_sync("http://example/timedtext") == ([], None)


class TestYoutubeProxy:
    """YOUTUBE_PROXY_URL reaches both YouTube calls of the metadata phase:
    the yt-dlp extraction and the timedtext fetch of the URL it returns."""

    PROXY = "http://user:pass@proxy.example:8080"

    def test_should_route_metadata_extraction_through_proxy_when_set(self, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", self.PROXY)

        assert _build_yt_dlp_opts()["proxy"] == self.PROXY

    def test_should_extract_metadata_directly_when_proxy_blank(self, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", "   ")

        assert "proxy" not in _build_yt_dlp_opts()

    @patch("src.services.video.youtube.requests.get")
    def test_should_fetch_timedtext_through_proxy_when_set(self, mock_get, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", self.PROXY)
        mock_get.return_value.iter_content.return_value = [b'{"events": []}']

        _fetch_subtitle_data_sync("http://example/timedtext")

        assert mock_get.call_args.kwargs["proxies"] == {"http": self.PROXY, "https": self.PROXY}

    @patch("src.services.video.youtube.requests.get")
    def test_should_fetch_timedtext_directly_when_proxy_blank(self, mock_get, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", None)
        mock_get.return_value.iter_content.return_value = [b'{"events": []}']

        _fetch_subtitle_data_sync("http://example/timedtext")

        assert mock_get.call_args.kwargs["proxies"] is None


class TestCaptionTrackFields:
    """``_extract_video_data_sync`` records which caption track was picked and
    how its fetch went, so an empty ``subtitles`` list is explainable."""

    @pytest.fixture
    def info_with_track(self):
        """Minimal yt-dlp info dict with a single manual Arabic (regional) track."""
        return {
            "id": "test123",
            "title": "Test Video",
            "uploader": "Test Channel",
            "duration": 300,
            "description": "",
            "thumbnails": [],
            "chapters": [],
            "automatic_captions": {},
            "subtitles": {"ar-SA": [{"ext": "json3", "url": "http://example/timedtext"}]},
        }

    @patch("src.services.video.youtube._fetch_subtitles_from_url_sync")
    @patch("src.services.video.youtube._extract_with_retry")
    def test_manual_track_with_segments_records_track_and_no_error(
        self, mock_extract, mock_fetch, info_with_track
    ):
        from src.services.video.youtube import _extract_video_data_sync

        mock_extract.return_value = info_with_track
        mock_fetch.return_value = ([SubtitleSegment(text="hi", start=0.0, duration=1.0)], None)

        result = _extract_video_data_sync("test123")

        assert result.caption_track == "manual"
        assert result.caption_lang == "ar-SA"
        assert result.caption_fetch_error is None
        assert result.captions_rate_limited is False

    @patch("src.services.video.youtube._fetch_subtitles_from_url_sync")
    @patch("src.services.video.youtube._extract_with_retry")
    def test_http_429_sets_error_and_rate_limited_flag(
        self, mock_extract, mock_fetch, info_with_track
    ):
        """The legacy rate-limit flag is derived from the error code so the two
        can never disagree."""
        from src.services.video.youtube import _extract_video_data_sync

        mock_extract.return_value = info_with_track
        mock_fetch.return_value = ([], "http_429")

        result = _extract_video_data_sync("test123")

        assert result.caption_fetch_error == "http_429"
        assert result.captions_rate_limited is True

    @patch("src.services.video.youtube._fetch_subtitles_from_url_sync")
    @patch("src.services.video.youtube._extract_with_retry")
    def test_picked_track_with_no_segments_is_labelled_empty(
        self, mock_extract, mock_fetch, info_with_track
    ):
        """A track that was offered but held no speech is 'empty' — distinct
        from 'no track' and from a transport failure."""
        from src.services.video.youtube import _extract_video_data_sync

        mock_extract.return_value = info_with_track
        mock_fetch.return_value = ([], None)

        result = _extract_video_data_sync("test123")

        assert result.caption_fetch_error == "empty"
        assert result.captions_rate_limited is False

    @patch("src.services.video.youtube._fetch_subtitles_from_url_sync")
    @patch("src.services.video.youtube._extract_with_retry")
    def test_no_tracks_leaves_all_caption_fields_none(
        self, mock_extract, mock_fetch, info_with_track
    ):
        from src.services.video.youtube import _extract_video_data_sync

        info_with_track["subtitles"] = {}
        mock_extract.return_value = info_with_track

        result = _extract_video_data_sync("test123")

        assert result.caption_track is None
        assert result.caption_lang is None
        assert result.caption_fetch_error is None
        mock_fetch.assert_not_called()


class TestDetectCategory:
    """Tests for weighted category detection."""

    @patch("src.services.video.youtube._load_category_rules")
    def test_detects_cooking_from_keywords(self, mock_rules):
        """Test detecting cooking category from strong keywords."""
        mock_rules.return_value = {
            "detection_config": {
                "llm_fallback_threshold": 0.4,
                "weights": {
                    "keywords": 0.40,
                    "youtube_category": 0.30,
                    "title": 0.15,
                    "channel": 0.15,
                },
            },
            "categories": {
                "cooking": {
                    "keywords": {
                        "primary": ["recipe", "cooking"],
                        "secondary": ["food", "baking"],
                    },
                    "youtube_categories": {
                        "primary": ["Howto & Style"],
                        "secondary": ["Entertainment"],
                    },
                    "channel_patterns": ["jamie oliver"],
                    "title_patterns": ["recipe"],
                },
            },
            "default_category": "standard",
        }

        # Jamie Oliver video with Entertainment category (not primary)
        category, confidence = _detect_category(
            youtube_category="Entertainment",
            tags=["recipe", "cooking", "food"],
            hashtags=["recipe"],
            channel="Jamie Oliver",
            title="Easy Recipe for Dinner",
        )

        assert category == "cooking"
        assert confidence > 0.4  # Above threshold

    @patch("src.services.video.youtube._load_category_rules")
    def test_detects_coding_category(self, mock_rules):
        """Test detecting coding category."""
        mock_rules.return_value = {
            "detection_config": {
                "llm_fallback_threshold": 0.4,
                "weights": {
                    "keywords": 0.40,
                    "youtube_category": 0.30,
                    "title": 0.15,
                    "channel": 0.15,
                },
            },
            "categories": {
                "coding": {
                    "keywords": {
                        "primary": ["programming", "coding", "python"],
                        "secondary": ["javascript", "api"],
                    },
                    "youtube_categories": {
                        "primary": ["Science & Technology"],
                        "secondary": ["Education"],
                    },
                    "channel_patterns": ["fireship"],
                    "title_patterns": ["tutorial", "crash course"],
                },
            },
            "default_category": "standard",
        }

        category, confidence = _detect_category(
            youtube_category="Science & Technology",
            tags=["python", "programming"],
            hashtags=["coding"],
            channel="Some Channel",
            title="Python Tutorial",
        )

        assert category == "coding"
        assert confidence > 0.5

    @patch("src.services.video.youtube._load_category_rules")
    def test_defaults_to_standard_with_low_confidence(self, mock_rules):
        """Test defaulting to standard when no strong match."""
        mock_rules.return_value = {
            "detection_config": {
                "llm_fallback_threshold": 0.4,
                "weights": {
                    "keywords": 0.40,
                    "youtube_category": 0.30,
                    "title": 0.15,
                    "channel": 0.15,
                },
            },
            "categories": {
                "cooking": {
                    "keywords": {
                        "primary": ["recipe"],
                        "secondary": ["food"],
                    },
                    "youtube_categories": {
                        "primary": ["Howto & Style"],
                        "secondary": [],
                    },
                    "channel_patterns": [],
                    "title_patterns": [],
                },
            },
            "default_category": "standard",
        }

        category, confidence = _detect_category(
            youtube_category="Entertainment",
            tags=["funny", "comedy"],
            hashtags=[],
            channel="Random Channel",
            title="Funny Video",
        )

        assert category == "standard"
        assert confidence < 0.4

    @patch("src.services.video.youtube._load_category_rules")
    def test_channel_pattern_matching(self, mock_rules):
        """Test channel pattern contributes to score."""
        mock_rules.return_value = {
            "detection_config": {
                "llm_fallback_threshold": 0.4,
                "weights": {
                    "keywords": 0.40,
                    "youtube_category": 0.30,
                    "title": 0.15,
                    "channel": 0.15,
                },
            },
            "categories": {
                "cooking": {
                    "keywords": {"primary": [], "secondary": []},
                    "youtube_categories": {"primary": [], "secondary": []},
                    "channel_patterns": ["gordon ramsay"],
                    "title_patterns": [],
                },
            },
            "default_category": "standard",
        }

        # Only channel matches, gets 0.15 score
        category, confidence = _detect_category(
            youtube_category="Entertainment",
            tags=[],
            hashtags=[],
            channel="Gordon Ramsay",
            title="Kitchen Nightmares",
        )

        # Should detect cooking from channel alone
        assert category == "cooking"
        assert confidence == pytest.approx(0.15, abs=0.01)


def _base_music_rules(**category_overrides):
    """Build a category rules dict with sensible music defaults.

    Pass ``categories`` (or any other top-level key) as a keyword argument
    to override only that part of the structure.
    """
    base = {
        "detection_config": {
            "llm_fallback_threshold": 0.4,
            "weights": {
                "keywords": 0.40,
                "youtube_category": 0.30,
                "title": 0.15,
                "channel": 0.15,
            },
        },
        "categories": {
            "music": {
                "keywords": {
                    "primary": ["music", "song", "official video"],
                    "secondary": ["remix", "cover"],
                },
                "youtube_categories": {
                    "primary": ["Music"],
                    "secondary": ["Entertainment"],
                },
                "channel_patterns": [],
                "title_patterns": ["official video"],
            },
        },
        "default_category": "standard",
    }
    base.update(category_overrides)
    return base


class TestMusicCategoryDetection:
    """Tests for Phase 2: Music category detection."""

    def test_music_in_valid_categories(self):
        """Test that 'music' is in VALID_CATEGORIES."""
        assert "music" in VALID_CATEGORIES

    @patch("src.services.video.youtube._load_category_rules")
    def test_detects_music_from_youtube_category(self, mock_rules):
        """Test detecting music from YouTube Music category."""
        mock_rules.return_value = _base_music_rules(
            categories={
                "music": {
                    "keywords": {
                        "primary": ["music", "song", "album", "official video", "lyrics"],
                        "secondary": ["remix", "cover", "acoustic", "feat", "ft"],
                    },
                    "youtube_categories": {
                        "primary": ["Music"],
                        "secondary": ["Entertainment"],
                    },
                    "channel_patterns": ["vevo"],
                    "title_patterns": ["official (music )?video", "lyrics"],
                },
            },
        )

        category, confidence = _detect_category(
            youtube_category="Music",
            tags=["music", "song", "official video"],
            hashtags=["music"],
            channel="ArtistVEVO",
            title="Artist - Song (Official Music Video)",
        )

        assert category == "music"
        assert confidence > 0.4

    @patch("src.services.video.youtube._load_category_rules")
    def test_detects_music_from_title_pattern(self, mock_rules):
        """Test detecting music from title with 'ft.' pattern."""
        mock_rules.return_value = _base_music_rules(
            categories={
                "music": {
                    "keywords": {
                        "primary": ["music", "song", "official video"],
                        "secondary": ["feat", "ft"],
                    },
                    "youtube_categories": {
                        "primary": ["Music"],
                        "secondary": ["Entertainment"],
                    },
                    "channel_patterns": [],
                    "title_patterns": ["\\bft\\.?\\b", "\\bfeat\\.?\\b"],
                },
            },
        )

        category, confidence = _detect_category(
            youtube_category="Music",
            tags=["music", "song"],
            hashtags=[],
            title="Artist ft. Another - Song Title",
        )

        assert category == "music"
        assert confidence > 0.3

    @patch("src.services.video.youtube._load_category_rules")
    def test_music_review_not_detected_as_music(self, mock_rules):
        """Test that a music review video is NOT detected as music."""
        mock_rules.return_value = _base_music_rules(
            categories={
                "reviews": {
                    "keywords": {
                        "primary": ["review", "unboxing", "comparison"],
                        "secondary": ["test", "rating", "impressions"],
                    },
                    "youtube_categories": {
                        "primary": ["Science & Technology"],
                        "secondary": ["Gaming"],
                    },
                    "channel_patterns": [],
                    "title_patterns": ["review"],
                },
                "music": {
                    "keywords": {
                        "primary": ["music", "song", "official video"],
                        "secondary": ["remix", "cover"],
                    },
                    "youtube_categories": {
                        "primary": ["Music"],
                        "secondary": ["Entertainment"],
                    },
                    "channel_patterns": [],
                    "title_patterns": ["official video"],
                },
            },
        )

        category, _ = _detect_category(
            youtube_category="Entertainment",
            tags=["review", "album review", "rating"],
            hashtags=["review"],
            title="Album Review: Artist's New Album",
        )

        assert category == "reviews"
