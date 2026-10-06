"""Tests for transcript service (transcript.py).

Tests transcript fetching, parsing, segmentation, and timestamp handling.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.transcription.transcript import (
    _fetch_transcript_sync,
    _is_rate_limit_error,
    clean_transcript,
    format_transcript_with_timestamps,
    get_transcript,
    normalize_segments,
)


class TestCleanTranscript:
    """Tests for transcript text cleaning."""

    def test_removes_music_annotation(self):
        """Test removing [Music] annotation."""
        result = clean_transcript("Hello [Music] world")
        assert result == "Hello world"

    def test_removes_applause_annotation(self):
        """Test removing [Applause] annotation."""
        result = clean_transcript("Thank you [Applause] everyone")
        assert result == "Thank you everyone"

    def test_removes_laughter_annotation(self):
        """Test removing [Laughter] annotation."""
        result = clean_transcript("That was funny [Laughter]")
        assert result == "That was funny"

    def test_case_insensitive_removal(self):
        """Test case-insensitive annotation removal."""
        result = clean_transcript("[MUSIC] test [music] [MuSiC]")
        assert result == "test"

    def test_normalizes_whitespace(self):
        """Test whitespace normalization."""
        result = clean_transcript("Hello    world   test")
        assert result == "Hello world test"

    def test_strips_leading_trailing_whitespace(self):
        """Test stripping leading/trailing whitespace."""
        result = clean_transcript("  Hello world  ")
        assert result == "Hello world"

    def test_handles_empty_string(self):
        """Test handling empty string."""
        result = clean_transcript("")
        assert result == ""

    def test_handles_only_annotations(self):
        """Test handling text with only annotations."""
        result = clean_transcript("[Music] [Applause]")
        assert result == ""


class TestFormatTranscriptWithTimestamps:
    """Tests for timestamp formatting of transcript segments."""

    def test_formats_with_default_interval(self):
        """Test formatting with default 30-second interval."""
        segments = [
            {"text": "Hello everyone", "start": 0},
            {"text": "welcome to the video", "start": 5},
            {"text": "Today we discuss", "start": 35},
        ]

        result = format_transcript_with_timestamps(segments)
        lines = result.split("\n")

        assert len(lines) == 2
        assert "[0:00]" in lines[0]
        assert "Hello everyone" in lines[0]
        assert "[0:30]" in lines[1]  # 30-59 is interval 1, starting at 0:30
        assert "Today we discuss" in lines[1]

    def test_formats_with_custom_interval(self):
        """Test formatting with custom interval."""
        segments = [
            {"text": "Part 1", "start": 0},
            {"text": "Part 2", "start": 70},
            {"text": "Part 3", "start": 130},
        ]

        result = format_transcript_with_timestamps(segments, interval_seconds=60)
        lines = result.split("\n")

        assert len(lines) == 3
        assert "[0:00]" in lines[0]
        assert "[1:00]" in lines[1]
        assert "[2:00]" in lines[2]

    def test_groups_segments_in_same_interval(self):
        """Test that segments in same interval are grouped."""
        segments = [
            {"text": "First", "start": 0},
            {"text": "Second", "start": 10},
            {"text": "Third", "start": 20},
        ]

        result = format_transcript_with_timestamps(segments, interval_seconds=30)
        lines = result.split("\n")

        assert len(lines) == 1
        assert "First" in lines[0]
        assert "Second" in lines[0]
        assert "Third" in lines[0]

    def test_handles_empty_segments(self):
        """Test handling empty segments list."""
        result = format_transcript_with_timestamps([])
        assert result == ""

    def test_skips_empty_text(self):
        """Test skipping segments with empty text."""
        segments = [
            {"text": "Hello", "start": 0},
            {"text": "", "start": 10},
            {"text": "   ", "start": 20},
            {"text": "World", "start": 25},
        ]

        result = format_transcript_with_timestamps(segments)

        assert "Hello" in result
        assert "World" in result

    def test_formats_minutes_correctly(self):
        """Test correct minute formatting for longer videos."""
        segments = [
            {"text": "Start", "start": 0},
            {"text": "Middle", "start": 600},  # 10 minutes
            {"text": "End", "start": 3600},  # 60 minutes
        ]

        result = format_transcript_with_timestamps(segments, interval_seconds=300)
        lines = result.split("\n")

        assert "[0:00]" in lines[0]
        assert "[10:00]" in lines[1]
        assert "[60:00]" in lines[2]


class TestNormalizeSegments:
    """Tests for segment normalization to milliseconds."""

    def test_normalizes_api_format(self):
        """Test normalizing youtube-transcript-api format (start + duration)."""
        segments = [
            {"text": "Hello", "start": 1.5, "duration": 2.0},
            {"text": "World", "start": 4.0, "duration": 1.5},
        ]

        result = normalize_segments(segments, "api")

        assert len(result) == 2
        assert result[0].text == "Hello"
        assert result[0].startMs == 1500
        assert result[0].endMs == 3500  # 1.5 + 2.0 = 3.5s
        assert result[1].startMs == 4000
        assert result[1].endMs == 5500

    def test_normalizes_whisper_format(self):
        """Test normalizing Whisper format (start + end in seconds)."""
        segments = [
            {"text": "Hello", "start": 1.0, "end": 3.0},
            {"text": "World", "start": 3.0, "end": 5.0},
        ]

        result = normalize_segments(segments, "whisper")

        assert len(result) == 2
        assert result[0].startMs == 1000
        assert result[0].endMs == 3000
        assert result[1].startMs == 3000
        assert result[1].endMs == 5000

    def test_preserves_already_normalized(self):
        """Test preserving already normalized format (startMs/endMs)."""
        segments = [
            {"text": "Hello", "startMs": 1500, "endMs": 3500},
            {"text": "World", "startMs": 4000, "endMs": 5500},
        ]

        result = normalize_segments(segments, "api")

        assert len(result) == 2
        assert result[0].startMs == 1500
        assert result[0].endMs == 3500

    def test_handles_missing_duration(self):
        """Test handling segments with missing duration."""
        segments = [
            {"text": "Hello", "start": 1.0},  # No duration
        ]

        result = normalize_segments(segments, "api")

        assert result[0].startMs == 1000
        assert result[0].endMs == 1000  # Same as start when no duration

    def test_handles_empty_segments(self):
        """Test handling empty segments list."""
        result = normalize_segments([], "api")
        assert result == []


class TestIsRateLimitError:
    """Tests for rate limit error detection."""

    def test_detects_429_error(self):
        """Test detecting 429 status code error."""
        error = Exception("HTTP Error 429: Too Many Requests")
        assert _is_rate_limit_error(error) is True

    def test_detects_too_many_requests(self):
        """Test detecting 'too many' in error message."""
        error = Exception("Too many requests, please slow down")
        assert _is_rate_limit_error(error) is True

    def test_detects_rate_limit_message(self):
        """Test detecting 'rate limit' in error message."""
        error = Exception("You have been rate limited")
        assert _is_rate_limit_error(error) is True

    def test_case_insensitive(self):
        """Test case-insensitive detection."""
        error = Exception("RATE LIMIT exceeded")
        assert _is_rate_limit_error(error) is True

    def test_does_not_detect_other_errors(self):
        """Test non-rate-limit errors are not detected."""
        error = Exception("Video not found")
        assert _is_rate_limit_error(error) is False


class TestFetchTranscriptSync:
    """Tests for synchronous transcript fetching."""

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_fetches_manual_transcript(self, mock_api_class):
        """Test fetching manually created transcript."""
        # Setup mock
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api

        mock_transcript_list = MagicMock()
        mock_transcript = MagicMock()
        mock_fetched = MagicMock()
        mock_fetched.to_raw_data.return_value = [
            {"text": "Hello", "start": 0.0, "duration": 1.0},
            {"text": "World", "start": 1.0, "duration": 1.0},
        ]

        mock_transcript.fetch.return_value = mock_fetched
        mock_transcript_list.find_manually_created_transcript.return_value = mock_transcript
        mock_api.list.return_value = mock_transcript_list

        segments, full_text, transcript_type, _lang = _fetch_transcript_sync("test_video_id")

        assert len(segments) == 2
        assert segments[0]["text"] == "Hello"
        assert full_text == "Hello World"
        assert transcript_type == "manual"

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_falls_back_to_auto_generated(self, mock_api_class):
        """Test falling back to auto-generated transcript."""
        from youtube_transcript_api._errors import NoTranscriptFound

        mock_api = MagicMock()
        mock_api_class.return_value = mock_api

        mock_transcript_list = MagicMock()
        mock_transcript = MagicMock()
        mock_fetched = MagicMock()
        mock_fetched.to_raw_data.return_value = [
            {"text": "Auto text", "start": 0.0, "duration": 1.0},
        ]

        mock_transcript.fetch.return_value = mock_fetched
        mock_transcript_list.find_manually_created_transcript.side_effect = NoTranscriptFound(
            "test", [], ""
        )
        mock_transcript_list.find_generated_transcript.return_value = mock_transcript
        mock_api.list.return_value = mock_transcript_list

        segments, full_text, transcript_type, _lang = _fetch_transcript_sync("test_video_id")

        assert transcript_type == "auto-generated"
        assert full_text == "Auto text"

    @staticmethod
    def _install_any_language_track(
        mock_api_class: MagicMock, *, is_generated: bool, language_code: str
    ) -> None:
        """Make both English finders fail so only list iteration yields a track."""
        from youtube_transcript_api._errors import NoTranscriptFound

        mock_track = MagicMock()
        mock_track.is_generated = is_generated
        mock_track.language_code = language_code
        mock_fetched = MagicMock()
        mock_fetched.to_raw_data.return_value = [{"text": "Shalom", "start": 0.0, "duration": 1.0}]
        mock_track.fetch.return_value = mock_fetched

        mock_transcript_list = MagicMock()
        mock_transcript_list.find_manually_created_transcript.side_effect = NoTranscriptFound(
            "test", [], ""
        )
        mock_transcript_list.find_generated_transcript.side_effect = NoTranscriptFound(
            "test", [], ""
        )
        mock_transcript_list.__iter__.return_value = iter([mock_track])
        mock_api_class.return_value.list.return_value = mock_transcript_list

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_labels_manual_track_from_any_language_fallback(self, mock_api_class):
        """Test any-language fallback labels a creator-uploaded track as manual."""
        self._install_any_language_track(mock_api_class, is_generated=False, language_code="he")

        _segments, _full_text, transcript_type, lang = _fetch_transcript_sync("test_video_id")

        assert transcript_type == "manual"
        assert lang == "he"

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_labels_generated_track_from_any_language_fallback(self, mock_api_class):
        """Test any-language fallback labels an auto-generated track as such."""
        self._install_any_language_track(mock_api_class, is_generated=True, language_code="he")

        _segments, _full_text, transcript_type, _lang = _fetch_transcript_sync("test_video_id")

        assert transcript_type == "auto-generated"

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_raises_error_on_disabled_captions(self, mock_api_class):
        """Test error when captions are disabled."""
        from youtube_transcript_api._errors import TranscriptsDisabled

        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = TranscriptsDisabled("test")

        with pytest.raises(TranscriptError) as exc_info:
            _fetch_transcript_sync("test_video_id")

        assert exc_info.value.code == ErrorCode.NO_TRANSCRIPT

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_raises_error_on_unavailable_video(self, mock_api_class):
        """Test error when video is unavailable."""
        from youtube_transcript_api._errors import VideoUnavailable

        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = VideoUnavailable("test")

        with pytest.raises(TranscriptError) as exc_info:
            _fetch_transcript_sync("test_video_id")

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE

    @pytest.fixture
    def no_retry_wait(self):
        """Skip tenacity's 4s + 8s backoff sleeps without disabling the retries."""
        with patch.object(_fetch_transcript_sync.retry, "sleep"):
            yield

    @pytest.mark.usefixtures("no_retry_wait")
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_raises_rate_limit_error(self, mock_api_class):
        """Test rate limit error handling."""
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = Exception("429 Too Many Requests")

        with pytest.raises(TranscriptError) as exc_info:
            _fetch_transcript_sync("test_video_id")

        assert exc_info.value.code == ErrorCode.RATE_LIMITED

    @pytest.mark.usefixtures("no_retry_wait")
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_should_retry_three_times_when_rate_limited(self, mock_api_class):
        """Guards the tenacity decorator staying on _fetch_transcript_sync —
        it once slid onto a helper inserted directly above, silently."""
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = Exception("429 Too Many Requests")

        with pytest.raises(TranscriptError):
            _fetch_transcript_sync("test_video_id")

        assert mock_api.list.call_count == 3

    @pytest.mark.usefixtures("no_retry_wait")
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_should_not_retry_when_error_is_not_a_rate_limit(self, mock_api_class):
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = Exception("connection reset")

        with pytest.raises(TranscriptError):
            _fetch_transcript_sync("test_video_id")

        assert mock_api.list.call_count == 1


class TestGetTranscriptAsync:
    """Tests for async transcript fetching."""

    @patch("src.services.transcription.transcript._fetch_transcript_sync")
    async def test_fetches_transcript_async(self, mock_fetch):
        """Test async wrapper for transcript fetching."""
        mock_fetch.return_value = (
            [{"text": "Hello", "start": 0, "duration": 1}],
            "Hello",
            "manual",
            "en",
        )

        segments, text, transcript_type, _lang = await get_transcript("test123")

        assert segments[0]["text"] == "Hello"
        assert text == "Hello"
        assert transcript_type == "manual"
        mock_fetch.assert_called_once_with("test123")

    @patch("src.services.transcription.transcript._fetch_transcript_sync")
    async def test_propagates_errors(self, mock_fetch):
        """Test that errors are propagated from sync function."""
        mock_fetch.side_effect = TranscriptError("No transcript", ErrorCode.NO_TRANSCRIPT)

        with pytest.raises(TranscriptError) as exc_info:
            await get_transcript("test123")

        assert exc_info.value.code == ErrorCode.NO_TRANSCRIPT


class TestTranscriptSegmentationEdgeCases:
    """Tests for edge cases in transcript segmentation."""

    def test_handles_very_long_videos(self):
        """Test formatting very long videos (2+ hours)."""
        segments = [
            {"text": "Start", "start": 0},
            {"text": "End", "start": 7200},  # 2 hours
        ]

        result = format_transcript_with_timestamps(segments, interval_seconds=3600)
        lines = result.split("\n")

        assert "[0:00]" in lines[0]
        assert "[120:00]" in lines[1]

    def test_handles_fractional_timestamps(self):
        """Test handling fractional second timestamps."""
        segments = [
            {"text": "A", "start": 0.333},
            {"text": "B", "start": 29.999},
            {"text": "C", "start": 30.001},
        ]

        result = format_transcript_with_timestamps(segments, interval_seconds=30)
        lines = result.split("\n")

        # First two should be in 0:00 interval, third in 0:30
        assert len(lines) == 2
        assert "A" in lines[0]
        assert "B" in lines[0]
        assert "C" in lines[1]

    def test_handles_unicode_text(self):
        """Test handling unicode characters in transcript."""
        segments = [
            {"text": "Hello world", "start": 0},
            {"text": "Cafe latte", "start": 5},
        ]

        result = format_transcript_with_timestamps(segments)

        assert "world" in result

    def test_preserves_segment_order(self):
        """Test that segment order is preserved."""
        segments = [
            {"text": "First", "start": 0},
            {"text": "Second", "start": 5},
            {"text": "Third", "start": 10},
        ]

        result = format_transcript_with_timestamps(segments)

        # Order should be preserved in output
        first_idx = result.find("First")
        second_idx = result.find("Second")
        third_idx = result.find("Third")

        assert first_idx < second_idx < third_idx


class TestBlockedDetection:
    """The library's IP-scoped blocks — the 429 (IpBlocked) and its parent, the
    bot check (RequestBlocked) — must read as a rate limit even once a proxy
    config has replaced their message with a generic proxy hint."""

    def test_should_detect_ip_blocked_with_proxy_message(self):
        from youtube_transcript_api._errors import IpBlocked
        from youtube_transcript_api.proxies import GenericProxyConfig

        error = IpBlocked("vid").with_proxy_config(
            GenericProxyConfig(http_url="http://u-1:p@h:80", https_url="http://u-1:p@h:80")
        )
        assert "429" not in str(error)  # the reason the text match alone was not enough

        assert _is_rate_limit_error(error) is True

    def test_should_detect_request_blocked_with_proxy_message(self):
        """The bot check matches the text ("too many requests") only when direct."""
        from youtube_transcript_api._errors import RequestBlocked
        from youtube_transcript_api.proxies import GenericProxyConfig

        error = RequestBlocked("vid").with_proxy_config(
            GenericProxyConfig(http_url="http://u-1:p@h:80", https_url="http://u-1:p@h:80")
        )
        assert "too many" not in str(error).lower()  # proxied: the text match misses it

        assert _is_rate_limit_error(error) is True

    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_should_map_ip_blocked_to_rate_limited(self, mock_api_class):
        from youtube_transcript_api._errors import IpBlocked

        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = IpBlocked("vid")

        with (
            patch.object(_fetch_transcript_sync.retry, "sleep"),
            pytest.raises(TranscriptError) as exc_info,
        ):
            _fetch_transcript_sync("test_video_id")

        assert exc_info.value.code == ErrorCode.RATE_LIMITED


class TestExitRotation:
    """With several proxy exits a 429 moves to the next exit instead of backing off."""

    EXITS = [
        "http://u-1:p@h:80",
        "http://u-2:p@h:80",
        "http://u-3:p@h:80",
    ]

    @pytest.fixture
    def exits(self):
        with patch(
            "src.services.transcription.transcript.ytdlp_proxy_exit_urls",
            return_value=list(self.EXITS),
        ):
            yield

    @staticmethod
    def _proxied_urls(mock_api_class) -> list[str | None]:
        urls = []
        for call in mock_api_class.call_args_list:
            config = call.kwargs["proxy_config"]
            urls.append(config.to_requests_dict()["https"] if config else None)
        return urls

    @staticmethod
    def _api_that_429s_on(blocked_urls: set[str], mock_api_class) -> None:
        def build(proxy_config=None):
            api = MagicMock()
            url = proxy_config.to_requests_dict()["https"] if proxy_config else None
            if url in blocked_urls:
                api.list.side_effect = Exception("429 Too Many Requests")
            else:
                track = MagicMock(language_code="en")
                track.fetch.return_value = [{"text": "hi", "start": 0.0, "duration": 1.0}]
                transcript_list = MagicMock()
                transcript_list.find_manually_created_transcript.return_value = track
                api.list.return_value = transcript_list
            return api

        mock_api_class.side_effect = build

    @pytest.mark.usefixtures("exits")
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    async def test_should_succeed_on_the_next_exit_after_a_429(self, mock_api_class):
        self._api_that_429s_on({self.EXITS[0]}, mock_api_class)

        segments, _, _, _ = await get_transcript("vid")

        assert segments[0]["text"] == "hi"
        assert self._proxied_urls(mock_api_class) == self.EXITS[:2]

    @pytest.mark.usefixtures("exits")
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    async def test_should_raise_rate_limited_when_every_exit_429s(self, mock_api_class):
        self._api_that_429s_on(set(self.EXITS), mock_api_class)

        with pytest.raises(TranscriptError) as exc_info:
            await get_transcript("vid")

        assert exc_info.value.code == ErrorCode.RATE_LIMITED
        assert self._proxied_urls(mock_api_class) == self.EXITS

    @pytest.mark.usefixtures("exits")
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    async def test_should_stop_rotating_on_a_non_rate_limit_error(self, mock_api_class):
        from youtube_transcript_api._errors import VideoUnavailable

        api = MagicMock()
        api.list.side_effect = VideoUnavailable("vid")
        mock_api_class.return_value = api

        with pytest.raises(TranscriptError) as exc_info:
            await get_transcript("vid")

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE
        assert mock_api_class.call_count == 1

    @patch("src.services.transcription.transcript.ytdlp_proxy_exit_urls", return_value=[])
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    async def test_should_use_same_exit_backoff_without_rotation(self, mock_api_class, _exits):
        """Single exit (or direct): the tenacity path with its 3 attempts stays."""
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.list.side_effect = Exception("429 Too Many Requests")

        with (
            patch.object(_fetch_transcript_sync.retry, "sleep"),
            pytest.raises(TranscriptError),
        ):
            await get_transcript("vid")

        assert mock_api.list.call_count == 3
