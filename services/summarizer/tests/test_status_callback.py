"""Tests for status callback service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.status_callback import send_video_status


class TestSendVideoStatus:
    """Tests for send_video_status function."""

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_basic_status_update(self, mock_settings, mock_client_class):
        """Test sending basic status update."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
        )

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        # Check URL
        assert call_args[0][0] == "http://api:3000/internal/status"

        # Check payload
        payload = call_args.kwargs["json"]
        assert payload["type"] == "video.status"
        assert payload["payload"]["videoSummaryId"] == "vid123"
        assert payload["payload"]["userId"] == "user456"
        assert payload["payload"]["status"] == "processing"

        # Check headers
        headers = call_args.kwargs["headers"]
        assert headers["X-Internal-Secret"] == "test-secret"

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_marks_a_cache_hit_completion(self, mock_settings, mock_client_class):
        """A completion served from the response cache says so (I3)."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123", user_id=None, status="completed", cache_hit=True
        )

        assert mock_client.post.call_args.kwargs["json"]["payload"]["cacheHit"] is True

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_omits_the_cache_hit_flag_on_a_real_run(self, mock_settings, mock_client_class):
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(video_summary_id="vid123", user_id=None, status="completed")

        assert "cacheHit" not in mock_client.post.call_args.kwargs["json"]["payload"]

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_status_with_progress(self, mock_settings, mock_client_class):
        """Test sending status with progress."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
            progress=50,
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["payload"]["progress"] == 50

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_status_with_message(self, mock_settings, mock_client_class):
        """Test sending status with message."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
            message="Fetching transcript...",
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["payload"]["message"] == "Fetching transcript..."

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_status_with_error(self, mock_settings, mock_client_class):
        """Test sending status with error."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="failed",
            error="Transcript not available",
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["payload"]["error"] == "Transcript not available"

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_status_without_user_id(self, mock_settings, mock_client_class):
        """Test sending status without user_id."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id=None,
            status="processing",
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert "userId" not in payload["payload"]

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_status_with_all_fields(self, mock_settings, mock_client_class):
        """Test sending status with all optional fields."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="completed",
            progress=100,
            message="Processing complete",
            error=None,
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["payload"]["videoSummaryId"] == "vid123"
        assert payload["payload"]["userId"] == "user456"
        assert payload["payload"]["status"] == "completed"
        assert payload["payload"]["progress"] == 100
        assert payload["payload"]["message"] == "Processing complete"
        assert "error" not in payload["payload"]

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_uses_correct_timeout(self, mock_settings, mock_client_class):
        """Test that correct timeout is used."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
        )

        call_args = mock_client.post.call_args
        assert call_args.kwargs["timeout"] == 5.0

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_handles_connection_error_gracefully(self, mock_settings, mock_client_class):
        """Test that connection errors don't raise exceptions."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Should not raise
        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
        )

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_handles_timeout_gracefully(self, mock_settings, mock_client_class):
        """Test that timeout errors don't raise exceptions."""
        import httpx

        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Should not raise
        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
        )

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_logs_error_on_failure(self, mock_settings, mock_client_class):
        """Test that errors are logged."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with patch("src.services.status_callback.logger") as mock_logger:
            await send_video_status(
                video_summary_id="vid123",
                user_id="user456",
                status="processing",
            )

            mock_logger.warning.assert_called_once()
            log_message = mock_logger.warning.call_args[0][0]
            assert "Failed to send status callback" in log_message

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_progress_zero_is_included(self, mock_settings, mock_client_class):
        """Test that progress=0 is included (not filtered as falsy)."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
            progress=0,
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["payload"]["progress"] == 0

    @patch("src.services.status_callback.httpx.AsyncClient")
    @patch("src.services.status_callback.settings")
    async def test_empty_message_not_included(self, mock_settings, mock_client_class):
        """Test that empty message is not included."""
        mock_settings.API_URL = "http://api:3000"
        mock_settings.INTERNAL_SECRET = "test-secret"

        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_video_status(
            video_summary_id="vid123",
            user_id="user456",
            status="processing",
            message="",
        )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert "message" not in payload["payload"]
