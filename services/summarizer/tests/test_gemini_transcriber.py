"""Tests for Gemini transcriber service."""

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.media.download_utils import MAX_DOWNLOAD_ATTEMPTS
from src.services.transcription.gemini_transcriber import (
    GEMINI_TRANSCRIPTION_MAX_TOKENS,
    GEMINI_TRANSCRIPTION_MODEL,
    MIME_TYPES,
    MUSIC_TRANSCRIPTION_PROMPT,
    TRANSCRIPTION_PROMPT,
    _download_audio_raw_sync,
    _get_gemini_model,
    _get_mime_type,
    _parse_gemini_response,
    _parse_ndjson_response,
    _recover_truncated_json,
    transcribe_with_gemini,
)


class TestDownloadAudioRawSync:
    """Tests for _download_audio_raw_sync function."""

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_download_success_webm(self, mock_ydl_class, mock_uuid, tmp_path):
        """Test successful audio download keeps raw webm format."""
        video_id = "test123"
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            # Simulate yt-dlp producing a .webm file
            webm_path = tmp_path / f"{video_id}_aabbccdd.webm"
            webm_path.write_bytes(b"fake webm audio" * 1000)

            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            result = _download_audio_raw_sync(video_id)

            assert result == webm_path
            assert result.suffix == ".webm"
            mock_ydl.download.assert_called_once()

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_download_success_m4a(self, mock_ydl_class, mock_uuid, tmp_path):
        """Test successful download with m4a format."""
        video_id = "test456"
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            m4a_path = tmp_path / f"{video_id}_aabbccdd.m4a"
            m4a_path.write_bytes(b"fake m4a audio" * 1000)

            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            result = _download_audio_raw_sync(video_id)

            assert result == m4a_path
            assert result.suffix == ".m4a"

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_extract_audio_postprocessor_stream_copies(self, mock_ydl_class, mock_uuid, tmp_path):
        """yt-dlp must stream-copy audio (codec "best") and fall back to /best.

        The /best fallback covers SABR-stripped androids where no audio-only
        format exists; the postprocessor drops the video track in that case.
        """
        video_id = "test789"
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            # Post-processed output may be .opus, .webm, or .m4a depending on source
            opus_path = tmp_path / f"{video_id}_aabbccdd.opus"
            opus_path.write_bytes(b"fake audio" * 1000)

            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            result = _download_audio_raw_sync(video_id)

            assert result == opus_path
            opts = mock_ydl_class.call_args[0][0]
            assert opts["format"] == "bestaudio/best"
            assert opts["postprocessors"] == [
                {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
            ]

    @patch("src.services.media.download_utils.time.sleep")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_download_failure_raises_transcript_error(self, mock_ydl_class, mock_sleep):
        """Test download failure raises TranscriptError with DOWNLOAD_ERROR."""
        mock_ydl = MagicMock()
        mock_ydl.download.side_effect = Exception("Network error")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        with pytest.raises(TranscriptError) as exc_info:
            _download_audio_raw_sync("test123")

        assert exc_info.value.code == ErrorCode.DOWNLOAD_ERROR
        assert "Failed to download audio" in str(exc_info.value)

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_download_file_not_found(self, mock_ydl_class, mock_uuid, tmp_path):
        """Test error when download completes but no file found."""
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            with pytest.raises(TranscriptError) as exc_info:
                _download_audio_raw_sync("test123")

            assert exc_info.value.code == ErrorCode.UNKNOWN_ERROR
            assert "file not found" in str(exc_info.value)

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_uuid_based_filenames(self, mock_ydl_class, mock_uuid, tmp_path):
        """Test that filenames include UUID to prevent race conditions."""
        mock_uuid.return_value = MagicMock(hex="deadbeef12345678")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            webm_path = tmp_path / "myvideo_deadbeef.webm"
            webm_path.write_bytes(b"audio" * 100)

            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            result = _download_audio_raw_sync("myvideo")

            assert "deadbeef" in result.stem

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_resilience_settings(self, mock_ydl_class, mock_uuid, tmp_path):
        """Test that yt-dlp is configured with retry and timeout options."""
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            webm_path = tmp_path / "vid_aabbccdd.webm"
            webm_path.write_bytes(b"audio" * 100)

            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            _download_audio_raw_sync("vid")

            opts = mock_ydl_class.call_args[0][0]
            assert opts["retries"] == 3
            assert opts["fragment_retries"] == 5
            assert opts["socket_timeout"] == 30
            assert opts["continuedl"] is False

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.time.sleep")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_retries_with_backoff(self, mock_ydl_class, mock_sleep, mock_uuid, tmp_path):
        """Test exponential backoff on retries."""
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            webm_path = tmp_path / "vid_aabbccdd.webm"
            webm_path.write_bytes(b"audio" * 100)

            mock_ydl = MagicMock()
            mock_ydl.download.side_effect = [
                Exception("read error"),
                Exception("connection reset"),
                None,
            ]
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            result = _download_audio_raw_sync("vid")

            assert result == webm_path
            assert mock_ydl.download.call_count == 3
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(2)
            mock_sleep.assert_any_call(4)

    @patch("src.services.media.download_utils.time.sleep")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_exhausts_retries(self, mock_ydl_class, mock_sleep):
        """Test that after exhausting all retries the error is raised."""
        mock_ydl = MagicMock()
        mock_ydl.download.side_effect = Exception("timeout reading data")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        with pytest.raises(TranscriptError):
            _download_audio_raw_sync("test")

        assert mock_ydl.download.call_count == MAX_DOWNLOAD_ATTEMPTS
        assert mock_sleep.call_count == MAX_DOWNLOAD_ATTEMPTS - 1

    @patch("src.services.transcription.gemini_transcriber.uuid.uuid4")
    @patch("src.services.media.download_utils.yt_dlp.YoutubeDL")
    def test_part_files_filtered(self, mock_ydl_class, mock_uuid, tmp_path):
        """Test that .part files are not returned as the downloaded file."""
        mock_uuid.return_value = MagicMock(hex="aabbccdd11223344")

        with patch("src.services.transcription.gemini_transcriber.TEMP_DIR", tmp_path):
            # Both a real file and a .part file exist
            webm_path = tmp_path / "vid_aabbccdd.webm"
            webm_path.write_bytes(b"audio" * 100)
            part_path = tmp_path / "vid_aabbccdd.webm.part"
            part_path.write_bytes(b"partial")

            mock_ydl = MagicMock()
            mock_ydl_class.return_value.__enter__.return_value = mock_ydl

            result = _download_audio_raw_sync("vid")

            assert result == webm_path
            assert ".part" not in result.suffix


class TestMimeTypeDetection:
    """Tests for _get_mime_type function."""

    def test_webm(self, tmp_path):
        """Test .webm maps to audio/webm."""
        assert _get_mime_type(tmp_path / "file.webm") == "audio/webm"

    def test_m4a(self, tmp_path):
        """Test .m4a maps to audio/mp4."""
        assert _get_mime_type(tmp_path / "file.m4a") == "audio/mp4"

    def test_ogg(self, tmp_path):
        """Test .ogg maps to audio/ogg."""
        assert _get_mime_type(tmp_path / "file.ogg") == "audio/ogg"

    def test_opus(self, tmp_path):
        """Test .opus maps to audio/opus."""
        assert _get_mime_type(tmp_path / "file.opus") == "audio/opus"

    def test_mp3(self, tmp_path):
        """Test .mp3 maps to audio/mpeg."""
        assert _get_mime_type(tmp_path / "file.mp3") == "audio/mpeg"

    def test_wav(self, tmp_path):
        """Test .wav maps to audio/wav."""
        assert _get_mime_type(tmp_path / "file.wav") == "audio/wav"

    def test_flac(self, tmp_path):
        """Test .flac maps to audio/flac."""
        assert _get_mime_type(tmp_path / "file.flac") == "audio/flac"

    def test_unknown_extension_defaults_to_webm(self, tmp_path):
        """Test unknown extensions default to audio/webm."""
        assert _get_mime_type(tmp_path / "file.xyz") == "audio/webm"

    def test_all_mime_types_covered(self):
        """Test that all declared MIME types are valid."""
        for ext, mime in MIME_TYPES.items():
            assert ext.startswith(".")
            assert mime.startswith("audio/")


class TestGetGeminiModel:
    """Tests for _get_gemini_model function."""

    @patch("src.services.transcription.gemini_transcriber.settings")
    def test_default_model_when_no_override(self, mock_settings):
        """Test that default Gemini model is used when LLM_FAST_MODEL is not set."""
        mock_settings.LLM_FAST_MODEL = None
        assert _get_gemini_model() == GEMINI_TRANSCRIPTION_MODEL

    @patch("src.services.transcription.gemini_transcriber.settings")
    def test_default_model_when_openai_configured(self, mock_settings):
        """Test that default Gemini model is used even when fast model is OpenAI."""
        mock_settings.LLM_FAST_MODEL = "openai/gpt-4o-mini"
        assert _get_gemini_model() == GEMINI_TRANSCRIPTION_MODEL

    @patch("src.services.transcription.gemini_transcriber.settings")
    def test_gemini_override_strips_prefix(self, mock_settings):
        """Test that explicit Gemini model override strips gemini/ prefix."""
        mock_settings.LLM_FAST_MODEL = "gemini/gemini-2.0-flash"
        assert _get_gemini_model() == "gemini-2.0-flash"

    @patch("src.services.transcription.gemini_transcriber.settings")
    def test_anthropic_model_uses_default(self, mock_settings):
        """Test that Anthropic model falls back to default Gemini model."""
        mock_settings.LLM_FAST_MODEL = "anthropic/claude-haiku-4-5-20251001"
        assert _get_gemini_model() == GEMINI_TRANSCRIPTION_MODEL


class TestGeminiResponseParsing:
    """Tests for _parse_gemini_response function."""

    def test_valid_json_array(self):
        """Test parsing a clean JSON array response."""
        response = json.dumps(
            [
                {"text": "Hello everyone", "startMs": 0, "endMs": 25000},
                {"text": "Welcome to the show", "startMs": 25000, "endMs": 50000},
            ]
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "Hello everyone"
        assert result[0]["startMs"] == 0
        assert result[0]["endMs"] == 25000
        assert result[1]["text"] == "Welcome to the show"

    def test_markdown_code_block(self):
        """Test parsing JSON wrapped in markdown code blocks."""
        response = '```json\n[{"text": "Hello", "startMs": 0, "endMs": 5000}]\n```'
        result = _parse_gemini_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_markdown_code_block_no_language(self):
        """Test parsing JSON wrapped in bare code blocks."""
        response = '```\n[{"text": "Hello", "startMs": 0, "endMs": 5000}]\n```'
        result = _parse_gemini_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_extra_text_around_json(self):
        """Test parsing when there's extra text around the JSON array."""
        response = (
            'Here is the transcription:\n[{"text": "Hello", "startMs": 0, "endMs": 5000}]\nDone!'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_missing_timestamps_default_to_zero(self):
        """Test that missing timestamps default to 0."""
        response = '[{"text": "Hello"}]'
        result = _parse_gemini_response(response)

        assert len(result) == 1
        assert result[0]["startMs"] == 0
        assert result[0]["endMs"] == 0

    def test_empty_segments_skipped(self):
        """Test that segments with empty text are filtered out."""
        response = json.dumps(
            [
                {"text": "Hello", "startMs": 0, "endMs": 5000},
                {"text": "", "startMs": 5000, "endMs": 10000},
                {"text": "  ", "startMs": 10000, "endMs": 15000},
                {"text": "World", "startMs": 15000, "endMs": 20000},
            ]
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "Hello"
        assert result[1]["text"] == "World"

    def test_invalid_segments_skipped(self):
        """Test that segments without text key are skipped."""
        response = json.dumps(
            [
                {"text": "Hello", "startMs": 0, "endMs": 5000},
                {"startMs": 5000, "endMs": 10000},  # No text key
                "not a dict",
                {"text": "World", "startMs": 10000, "endMs": 15000},
            ]
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "Hello"
        assert result[1]["text"] == "World"

    def test_no_json_array_raises(self):
        """Test that response without JSON array raises ValueError."""
        with pytest.raises(ValueError, match="No JSON array found"):
            _parse_gemini_response("This is just text, no JSON here.")

    def test_invalid_json_raises(self):
        """Test that malformed JSON raises an error."""
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _parse_gemini_response("[{broken json}")

    def test_single_object_parsed_as_ndjson(self):
        """Test that a single JSON object is parsed via ndjson fallback."""
        result = _parse_gemini_response('{"text": "hello", "startMs": 0, "endMs": 5000}')
        assert len(result) == 1
        assert result[0]["text"] == "hello"

    def test_whitespace_text_trimmed(self):
        """Test that segment text is whitespace-trimmed."""
        response = '[{"text": "  Hello world  ", "startMs": 0, "endMs": 5000}]'
        result = _parse_gemini_response(response)

        assert result[0]["text"] == "Hello world"

    def test_large_response(self):
        """Test parsing a response with many segments."""
        segments = [
            {"text": f"Segment {i}", "startMs": i * 30000, "endMs": (i + 1) * 30000}
            for i in range(100)
        ]
        response = json.dumps(segments)
        result = _parse_gemini_response(response)

        assert len(result) == 100
        assert result[0]["startMs"] == 0
        assert result[99]["endMs"] == 3000000


def _make_mock_genai_client(response_text: str) -> MagicMock:
    """Create a mock google-genai Client with configured aio.files and aio.models."""
    mock_client = MagicMock()

    # Mock file upload result
    mock_upload = MagicMock()
    mock_upload.name = "files/test-file-123"
    mock_upload.uri = "https://generativelanguage.googleapis.com/v1beta/files/test123"
    mock_client.aio.files.upload = AsyncMock(return_value=mock_upload)
    mock_client.aio.files.delete = AsyncMock()

    # Mock generate_content result
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    return mock_client


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestTranscribeWithGemini:
    """Tests for transcribe_with_gemini async function."""

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_full_workflow_success(self, mock_download, mock_client_class, tmp_path):
        """Test successful full transcription workflow."""
        video_id = "test123"
        audio_path = tmp_path / f"{video_id}.webm"
        audio_path.write_bytes(b"fake webm audio")
        mock_download.return_value = audio_path

        response_text = json.dumps(
            [
                {"text": "Hello everyone", "startMs": 0, "endMs": 25000},
                {"text": "Welcome to the video", "startMs": 25000, "endMs": 50000},
            ]
        )
        mock_client_class.return_value = _make_mock_genai_client(response_text)

        result = await transcribe_with_gemini(video_id)

        assert result.text == "Hello everyone Welcome to the video"
        assert result.source == "gemini"
        assert len(result.segments) == 2
        assert result.segments[0].startMs == 0
        assert result.segments[0].endMs == 25000
        assert result.segments[1].text == "Welcome to the video"

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_transcript_carries_detected_language(
        self, mock_download, mock_client_class, tmp_path
    ):
        """A non-English transcript carries its detected source language so the
        pipeline runs the translation phase (and the FE shows the toggle)."""
        audio_path = tmp_path / "heb.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps(
            [
                {"text": "שלום וברוכים הבאים לפרק", "startMs": 0, "endMs": 25000},
                {"text": "היום נדבר על אמונה ובחירה", "startMs": 25000, "endMs": 50000},
            ]
        )
        mock_client_class.return_value = _make_mock_genai_client(response_text)

        result = await transcribe_with_gemini("heb123")

        assert result.language == "he"

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_cleanup_on_success(self, mock_download, mock_client_class, tmp_path):
        """Test that audio file is cleaned up after success."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Test", "startMs": 0, "endMs": 5000}])
        mock_client_class.return_value = _make_mock_genai_client(response_text)

        await transcribe_with_gemini("test")

        assert not audio_path.exists()

    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_cleanup_on_download_error(self, mock_download):
        """Test cleanup when download fails (no audio file to clean)."""
        mock_download.side_effect = TranscriptError("Download failed", ErrorCode.VIDEO_UNAVAILABLE)

        with pytest.raises(TranscriptError) as exc_info:
            await transcribe_with_gemini("test123")

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_cleanup_on_upload_error(self, mock_download, mock_client_class, tmp_path):
        """Test that audio file is cleaned up when upload fails."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        mock_client = MagicMock()
        mock_client.aio.files.upload = AsyncMock(side_effect=Exception("Upload failed"))
        mock_client_class.return_value = mock_client

        with pytest.raises(Exception, match="Upload failed"):
            await transcribe_with_gemini("test")

        assert not audio_path.exists()

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_unparseable_response_raises(self, mock_download, mock_client_class, tmp_path):
        """Test that unparseable Gemini response raises TranscriptError."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        mock_client_class.return_value = _make_mock_genai_client(
            "Sorry, I cannot transcribe this audio."
        )

        with pytest.raises(TranscriptError) as exc_info:
            await transcribe_with_gemini("test")

        assert exc_info.value.code == ErrorCode.UNKNOWN_ERROR
        assert "unparseable" in str(exc_info.value).lower()

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_empty_segments_raises(self, mock_download, mock_client_class, tmp_path):
        """Test that empty segment list raises TranscriptError."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        mock_client_class.return_value = _make_mock_genai_client("[]")

        with pytest.raises(TranscriptError) as exc_info:
            await transcribe_with_gemini("test")

        assert exc_info.value.code == ErrorCode.NO_TRANSCRIPT

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_file_upload_called_with_audio_path(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that file upload is called with the audio path."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Test", "startMs": 0, "endMs": 5000}])
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test")

        mock_client.aio.files.upload.assert_called_once()
        call_kwargs = mock_client.aio.files.upload.call_args
        assert call_kwargs.kwargs["file"] == audio_path

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_generate_content_uses_file_uri(self, mock_download, mock_client_class, tmp_path):
        """Test that generate_content is called with the uploaded file URI."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Test", "startMs": 0, "endMs": 5000}])
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test")

        mock_client.aio.models.generate_content.assert_called_once()

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_uploaded_file_deleted_after_transcription(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that uploaded file is deleted from Gemini after transcription."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Test", "startMs": 0, "endMs": 5000}])
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test")

        mock_client.aio.files.delete.assert_called_once_with(name="files/test-file-123")

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_markdown_wrapped_response(self, mock_download, mock_client_class, tmp_path):
        """Test handling of markdown-wrapped JSON response."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = '```json\n[{"text": "Hello world", "startMs": 0, "endMs": 10000}]\n```'
        mock_client_class.return_value = _make_mock_genai_client(response_text)

        result = await transcribe_with_gemini("test")

        assert result.source == "gemini"
        assert len(result.segments) == 1
        assert result.segments[0].text == "Hello world"

    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_download_error_propagates(self, mock_download):
        """Test that download errors propagate correctly."""
        mock_download.side_effect = TranscriptError("Private video", ErrorCode.VIDEO_UNAVAILABLE)

        with pytest.raises(TranscriptError) as exc_info:
            await transcribe_with_gemini("test123")

        assert exc_info.value.code == ErrorCode.VIDEO_UNAVAILABLE


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestGeminiTranscriptionMaxTokens:
    """Tests for the dedicated transcription token limit."""

    def test_max_tokens_is_65536(self):
        """Test that transcription uses 65K tokens, the model's max output."""
        assert GEMINI_TRANSCRIPTION_MAX_TOKENS == 65536

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_generate_content_uses_transcription_max_tokens(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that generate_content is called with GEMINI_TRANSCRIPTION_MAX_TOKENS."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Test", "startMs": 0, "endMs": 5000}])
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test")

        call_kwargs = mock_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs["config"]
        assert config.max_output_tokens == GEMINI_TRANSCRIPTION_MAX_TOKENS


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestTruncationRecovery:
    """Tests for _recover_truncated_json and truncated response handling."""

    def test_truncated_no_closing_bracket(self):
        """Test recovery when response is truncated with no closing ]."""
        # Simulate a response cut off mid-stream
        response = (
            '[{"text": "Hello everyone", "startMs": 0, "endMs": 25000},'
            '{"text": "Welcome to the show", "startMs": 25000, "endMs": 50000},'
            '{"text": "Today we will be tal'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "Hello everyone"
        assert result[1]["text"] == "Welcome to the show"

    def test_truncated_mid_object(self):
        """Test recovery when truncated in the middle of a JSON object."""
        response = (
            '[{"text": "Segment one", "startMs": 0, "endMs": 30000},'
            '{"text": "Segment two", "startMs": 30000, "endMs": 60000},'
            '{"text": "Segment three", "startMs": 60000, "endMs": 90000},'
            '{"text": "Segment four partial", "startMs": 90000, "end'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 3
        assert result[0]["text"] == "Segment one"
        assert result[1]["text"] == "Segment two"
        assert result[2]["text"] == "Segment three"

    def test_truncated_after_complete_objects(self):
        """Test recovery when truncated right after a complete object."""
        response = (
            '[{"text": "First", "startMs": 0, "endMs": 10000},'
            '{"text": "Second", "startMs": 10000, "endMs": 20000},'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "First"
        assert result[1]["text"] == "Second"

    def test_stray_bracket_in_text_value(self):
        """Test that a ] inside a text value (e.g. [music]) doesn't break parsing."""
        # This simulates the failure mode where rfind("]") finds a ] inside text
        segments = [
            {"text": "Hello [music] everyone", "startMs": 0, "endMs": 25000},
            {"text": "Welcome to the show", "startMs": 25000, "endMs": 50000},
        ]
        response = json.dumps(segments)
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "Hello [music] everyone"

    def test_no_recoverable_segments_raises(self):
        """Test that completely unrecoverable response raises ValueError."""
        with pytest.raises(ValueError, match="No complete segments recoverable"):
            _recover_truncated_json("[{broken", 0)

    def test_recover_single_segment(self):
        """Test recovery of a single complete segment from truncated response."""
        response = '[{"text": "Only one", "startMs": 0, "endMs": 10000},{"text": "Cut off he'
        result = _parse_gemini_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "Only one"

    def test_complete_response_still_works(self):
        """Test that complete (non-truncated) responses still parse normally."""
        segments = [
            {"text": "Hello", "startMs": 0, "endMs": 10000},
            {"text": "World", "startMs": 10000, "endMs": 20000},
        ]
        response = json.dumps(segments)
        result = _parse_gemini_response(response)

        assert len(result) == 2

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_truncated_response_in_full_workflow(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that truncated response is recovered in the full workflow."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        # Simulate a truncated response from Gemini
        truncated = (
            '[{"text": "Hello everyone", "startMs": 0, "endMs": 25000},'
            '{"text": "Welcome", "startMs": 25000, "endMs": 50000},'
            '{"text": "Trun'
        )
        mock_client_class.return_value = _make_mock_genai_client(truncated)

        result = await transcribe_with_gemini("test")

        assert result.source == "gemini"
        assert len(result.segments) == 2
        assert result.segments[0].text == "Hello everyone"
        assert result.segments[1].text == "Welcome"


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestNdjsonParsing:
    """Tests for newline-delimited JSON response parsing."""

    def test_ndjson_multiple_objects(self):
        """Test parsing multiple newline-separated JSON objects."""
        response = (
            '{"text": "First segment", "startMs": 0, "endMs": 25000}\n'
            '{"text": "Second segment", "startMs": 25000, "endMs": 50000}\n'
            '{"text": "Third segment", "startMs": 50000, "endMs": 75000}'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 3
        assert result[0]["text"] == "First segment"
        assert result[1]["text"] == "Second segment"
        assert result[2]["text"] == "Third segment"

    def test_ndjson_single_object(self):
        """Test parsing a single JSON object (no newlines)."""
        response = '{"text": "Only one", "startMs": 0, "endMs": 10000}'
        result = _parse_gemini_response(response)

        assert len(result) == 1
        assert result[0]["text"] == "Only one"

    def test_ndjson_skips_incomplete_lines(self):
        """Test that incomplete trailing line is skipped in ndjson."""
        response = (
            '{"text": "Complete", "startMs": 0, "endMs": 10000}\n'
            '{"text": "Also complete", "startMs": 10000, "endMs": 20000}\n'
            '{"text": "Truncat'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "Complete"
        assert result[1]["text"] == "Also complete"

    def test_ndjson_no_valid_objects_raises(self):
        """Test that ndjson with no valid objects raises ValueError."""
        with pytest.raises(ValueError, match="No parseable JSON objects"):
            _parse_ndjson_response("{broken\n{also broken")

    def test_ndjson_hebrew_content(self):
        """Test ndjson parsing with Hebrew/unicode content (real failure case)."""
        response = (
            '{"text": "שלום לכולם", "startMs": 0, "endMs": 27920}\n'
            '{"text": "קשה! לא קשה.", "startMs": 27920, "endMs": 50000}'
        )
        result = _parse_gemini_response(response)

        assert len(result) == 2
        assert result[0]["text"] == "שלום לכולם"
        assert result[1]["text"] == "קשה! לא קשה."

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_ndjson_response_in_full_workflow(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that ndjson response works through the full workflow."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        ndjson_response = (
            '{"text": "Hello everyone", "startMs": 0, "endMs": 25000}\n'
            '{"text": "Welcome to the show", "startMs": 25000, "endMs": 50000}'
        )
        mock_client_class.return_value = _make_mock_genai_client(ndjson_response)

        result = await transcribe_with_gemini("test")

        assert result.source == "gemini"
        assert len(result.segments) == 2
        assert result.segments[0].text == "Hello everyone"
        assert result.segments[1].text == "Welcome to the show"


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestGeminiEmptyResponseHandling:
    """Tests for Phase 1: Gemini crash fix — empty response handling."""

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_no_candidates_raises_transcript_error(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that response.text ValueError is caught and raises TranscriptError."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        mock_client = MagicMock()
        mock_upload = MagicMock()
        mock_upload.name = "files/test"
        mock_upload.uri = "https://example.com/files/test"
        mock_client.aio.files.upload = AsyncMock(return_value=mock_upload)
        mock_client.aio.files.delete = AsyncMock()

        # response.text raises ValueError when no candidates
        mock_response = MagicMock()
        type(mock_response).text = PropertyMock(side_effect=ValueError("no candidates"))
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        with pytest.raises(TranscriptError) as exc_info:
            await transcribe_with_gemini("test")

        assert exc_info.value.code == ErrorCode.NO_TRANSCRIPT

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_empty_text_raises_transcript_error(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that empty/whitespace-only response text raises TranscriptError."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        mock_client_class.return_value = _make_mock_genai_client("   ")

        with pytest.raises(TranscriptError) as exc_info:
            await transcribe_with_gemini("test")

        assert exc_info.value.code == ErrorCode.NO_TRANSCRIPT
        assert "empty" in str(exc_info.value).lower()

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_valid_text_passes_through(self, mock_download, mock_client_class, tmp_path):
        """Test that valid response text is processed normally."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Hello", "startMs": 0, "endMs": 5000}])
        mock_client_class.return_value = _make_mock_genai_client(response_text)

        result = await transcribe_with_gemini("test")
        assert result.source == "gemini"
        assert len(result.segments) == 1


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestMusicTranscriptionPromptSelection:
    """Tests for Phase 3: Music-aware transcription prompt selection."""

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_is_music_true_uses_music_prompt(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that is_music=True uses MUSIC_TRANSCRIPTION_PROMPT."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps(
            [{"text": "Never gonna give you up", "startMs": 0, "endMs": 5000}]
        )
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test", is_music=True)

        # Check that the prompt used in generate_content was the music prompt
        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs["contents"]
        prompt_text = contents[0].parts[0].text
        assert prompt_text == MUSIC_TRANSCRIPTION_PROMPT

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_is_music_false_uses_standard_prompt(
        self, mock_download, mock_client_class, tmp_path
    ):
        """Test that is_music=False uses standard TRANSCRIPTION_PROMPT."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Hello everyone", "startMs": 0, "endMs": 5000}])
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test", is_music=False)

        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs["contents"]
        prompt_text = contents[0].parts[0].text
        assert prompt_text == TRANSCRIPTION_PROMPT

    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_default_is_not_music(self, mock_download, mock_client_class, tmp_path):
        """Test that default behavior (no is_music arg) uses standard prompt."""
        audio_path = tmp_path / "test.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "Test", "startMs": 0, "endMs": 5000}])
        mock_client = _make_mock_genai_client(response_text)
        mock_client_class.return_value = mock_client

        await transcribe_with_gemini("test")

        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs["contents"]
        prompt_text = contents[0].parts[0].text
        assert prompt_text == TRANSCRIPTION_PROMPT


@patch("src.services.transcription.gemini_transcriber.settings.GEMINI_API_KEY", "test-key")
class TestGeminiUsageEmission:
    """Phase 0.5 — Gemini transcription emits a token-priced cost row.

    Gemini bypasses LiteLLM via google.genai, so the transcriber emits the cost
    from the response's usage_metadata token counts.
    """

    @patch("src.services.transcription.gemini_transcriber.emit_transcription_usage")
    @patch("src.services.transcription.gemini_transcriber.genai.Client")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_emits_token_cost_on_success(
        self, mock_download, mock_client_class, mock_emit, tmp_path
    ):
        audio_path = tmp_path / "v.webm"
        audio_path.write_bytes(b"fake audio")
        mock_download.return_value = audio_path

        response_text = json.dumps([{"text": "hi", "startMs": 0, "endMs": 1000}])
        client = _make_mock_genai_client(response_text)
        resp = client.aio.models.generate_content.return_value
        resp.usage_metadata.prompt_token_count = 50_000
        resp.usage_metadata.candidates_token_count = 4_000
        mock_client_class.return_value = client

        await transcribe_with_gemini("v")

        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["provider"] == "google"
        assert kwargs["feature"] == "summarize:transcript:gemini"
        assert kwargs["tokens_in"] == 50_000
        assert kwargs["tokens_out"] == 4_000
        assert kwargs["success"] is True

    @patch("src.services.transcription.gemini_transcriber.emit_transcription_usage")
    @patch("src.services.transcription.gemini_transcriber._download_audio_raw_sync")
    async def test_emits_failure_when_download_fails(self, mock_download, mock_emit):
        mock_download.side_effect = TranscriptError("boom", ErrorCode.UNKNOWN_ERROR)

        with pytest.raises(TranscriptError):
            await transcribe_with_gemini("v")

        mock_emit.assert_called_once()
        assert mock_emit.call_args.kwargs["success"] is False
        assert mock_emit.call_args.kwargs["feature"] == "summarize:transcript:gemini"


class TestTranscriptionPrompts:
    """The transcription prompts must keep transcripts in the source language."""

    def test_default_prompt_instructs_original_language(self):
        """Non-music transcription must NOT translate — otherwise a non-English
        video's source-language signal is lost and the translate toggle never
        appears."""
        lowered = TRANSCRIPTION_PROMPT.lower()
        assert "original spoken language" in lowered
        assert "do not translate" in lowered

    def test_music_prompt_preserves_language(self):
        """Music prompt already preserves language — guard against regression."""
        assert "preserving the language" in MUSIC_TRANSCRIPTION_PROMPT.lower()
