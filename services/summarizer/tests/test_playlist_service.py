"""Tests for playlist service."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.media import download_utils
from src.services.video.playlist import (
    PlaylistData,
    PlaylistVideoInfo,
    _build_playlist_opts,
    _extract_playlist_sync,
    extract_playlist_data,
)


@pytest.fixture(autouse=True)
def _fresh_exit_memory(monkeypatch):
    """Each test starts with no remembered proxy exit (it is per-process state)."""
    monkeypatch.setattr(download_utils, "_EXIT_MEMORY", download_utils._ExitMemory())


class TestPlaylistVideoInfo:
    """Tests for PlaylistVideoInfo dataclass."""

    def test_create_with_all_fields(self):
        """Test creating PlaylistVideoInfo with all fields."""
        info = PlaylistVideoInfo(
            video_id="abc123",
            title="Test Video",
            position=0,
            duration=120,
            thumbnail_url="https://img.youtube.com/vi/abc123/mqdefault.jpg",
        )

        assert info.video_id == "abc123"
        assert info.title == "Test Video"
        assert info.position == 0
        assert info.duration == 120
        assert info.thumbnail_url is not None

    def test_create_with_optional_fields_none(self):
        """Test creating PlaylistVideoInfo with optional fields as None."""
        info = PlaylistVideoInfo(
            video_id="abc123",
            title="Test Video",
            position=0,
            duration=None,
            thumbnail_url=None,
        )

        assert info.duration is None
        assert info.thumbnail_url is None


class TestPlaylistData:
    """Tests for PlaylistData dataclass."""

    def test_total_videos_property(self):
        """Test total_videos property."""
        videos = [
            PlaylistVideoInfo(
                video_id="1", title="Video 1", position=0, duration=None, thumbnail_url=None
            ),
            PlaylistVideoInfo(
                video_id="2", title="Video 2", position=1, duration=None, thumbnail_url=None
            ),
            PlaylistVideoInfo(
                video_id="3", title="Video 3", position=2, duration=None, thumbnail_url=None
            ),
        ]

        playlist = PlaylistData(
            playlist_id="PLtest",
            title="Test Playlist",
            channel="Test Channel",
            thumbnail_url=None,
            videos=videos,
        )

        assert playlist.total_videos == 3

    def test_empty_videos(self):
        """Test playlist with no videos."""
        playlist = PlaylistData(
            playlist_id="PLtest",
            title="Empty Playlist",
            channel=None,
            thumbnail_url=None,
            videos=[],
        )

        assert playlist.total_videos == 0


class TestBuildPlaylistOpts:
    """Tests for _build_playlist_opts function."""

    PROXY = "http://user123:pass456@proxy.example:8080"

    def test_default_opts(self, monkeypatch):
        """Test default options without proxy."""
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", None)

        opts = _build_playlist_opts()

        assert opts["quiet"] is True
        assert opts["no_warnings"] is True
        assert opts["extract_flat"] == "in_playlist"
        assert opts["ignoreerrors"] is True
        assert opts["skip_download"] is True
        assert "proxy" not in opts

    def test_should_route_through_youtube_proxy_url_when_set(self, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", self.PROXY)

        opts = _build_playlist_opts()

        assert opts["proxy"] == self.PROXY

    def test_should_stay_direct_when_proxy_url_blank(self, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", "   ")

        opts = _build_playlist_opts()

        assert "proxy" not in opts


class TestExtractPlaylistSync:
    """Tests for _extract_playlist_sync function."""

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_successful_extraction(self, mock_ydl_class):
        """Test successful playlist extraction."""
        mock_info = {
            "title": "Test Playlist",
            "uploader": "Test Channel",
            "thumbnails": [{"url": "https://example.com/thumb.jpg"}],
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 120},
                {"id": "video2", "title": "Video 2", "duration": 180},
            ],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123")

        assert result.playlist_id == "PLtest123"
        assert result.title == "Test Playlist"
        assert result.channel == "Test Channel"
        assert len(result.videos) == 2
        assert result.videos[0].video_id == "video1"
        assert result.videos[1].video_id == "video2"

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_playlist_not_found(self, mock_ydl_class):
        """Test error when playlist not found."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = None
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        with pytest.raises(ValueError) as exc_info:
            _extract_playlist_sync("PLnonexistent")

        assert "not found" in str(exc_info.value)

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_skips_unavailable_videos(self, mock_ydl_class):
        """Test that unavailable videos (None entries) are skipped."""
        mock_info = {
            "title": "Test Playlist",
            "uploader": "Test Channel",
            "thumbnails": [],
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 120},
                None,  # Unavailable video
                {"id": "video3", "title": "Video 3", "duration": 180},
            ],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123")

        assert len(result.videos) == 2
        assert result.videos[0].video_id == "video1"
        assert result.videos[1].video_id == "video3"

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_skips_entries_without_id(self, mock_ydl_class):
        """Test that entries without video_id are skipped."""
        mock_info = {
            "title": "Test Playlist",
            "uploader": "Test Channel",
            "thumbnails": [],
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 120},
                {"title": "No ID Video", "duration": 60},  # No 'id' field
                {"id": "video3", "title": "Video 3", "duration": 180},
            ],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123")

        assert len(result.videos) == 2

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_max_videos_limit(self, mock_ydl_class):
        """Test that max_videos parameter limits results."""
        mock_info = {
            "title": "Test Playlist",
            "uploader": "Test Channel",
            "thumbnails": [],
            "entries": [
                {"id": f"video{i}", "title": f"Video {i}", "duration": 60} for i in range(20)
            ],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123", max_videos=5)

        assert len(result.videos) == 5

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_video_positions_are_sequential(self, mock_ydl_class):
        """Test that video positions are 0-indexed and sequential."""
        mock_info = {
            "title": "Test Playlist",
            "uploader": "Test Channel",
            "thumbnails": [],
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 120},
                None,  # Skip this
                {"id": "video2", "title": "Video 2", "duration": 180},
            ],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123")

        assert result.videos[0].position == 0
        assert result.videos[1].position == 1  # Position continues after skip

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_thumbnail_fallback_to_first_video(self, mock_ydl_class):
        """Test thumbnail falls back to first video's thumbnail."""
        mock_info = {
            "title": "Test Playlist",
            "uploader": "Test Channel",
            "thumbnails": [],  # No playlist thumbnail
            "entries": [
                {"id": "video1", "title": "Video 1", "duration": 120},
            ],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123")

        assert result.thumbnail_url is not None
        assert "video1" in result.thumbnail_url

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_handles_missing_channel(self, mock_ydl_class):
        """Test handling of missing channel info."""
        mock_info = {
            "title": "Test Playlist",
            "thumbnails": [],
            "entries": [],
        }

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        result = _extract_playlist_sync("PLtest123")

        assert result.channel is None

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_download_error_raises_value_error(self, mock_ydl_class):
        """Test that DownloadError is wrapped in ValueError."""
        from yt_dlp.utils import DownloadError

        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = DownloadError("Blocked")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        with pytest.raises(ValueError, match="Failed to extract playlist"):
            _extract_playlist_sync("PLtest123")

    @patch("src.services.video.playlist.yt_dlp.YoutubeDL")
    def test_extractor_error_raises_value_error(self, mock_ydl_class):
        """Test that ExtractorError is wrapped in ValueError."""
        from yt_dlp.utils import ExtractorError

        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = ExtractorError("Extractor failed")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        with pytest.raises(ValueError, match="Failed to extract playlist"):
            _extract_playlist_sync("PLtest123")


class TestExtractPlaylistData:
    """Tests for extract_playlist_data async function."""

    @patch("src.services.video.playlist._extract_playlist_sync")
    async def test_calls_sync_in_thread(self, mock_sync):
        """Test that async wrapper calls sync function."""
        mock_sync.return_value = PlaylistData(
            playlist_id="PLtest",
            title="Test",
            channel="Channel",
            thumbnail_url=None,
            videos=[],
        )

        result = await extract_playlist_data("PLtest")

        mock_sync.assert_called_once_with("PLtest", 100)
        assert result.playlist_id == "PLtest"

    @patch("src.services.video.playlist._extract_playlist_sync")
    async def test_passes_max_videos_parameter(self, mock_sync):
        """Test that max_videos parameter is passed through."""
        mock_sync.return_value = PlaylistData(
            playlist_id="PLtest",
            title="Test",
            channel="Channel",
            thumbnail_url=None,
            videos=[],
        )

        await extract_playlist_data("PLtest", max_videos=50)

        mock_sync.assert_called_once_with("PLtest", 50)

    @patch("src.services.video.playlist._extract_playlist_sync")
    async def test_propagates_errors(self, mock_sync):
        """Test that errors from sync function propagate."""
        mock_sync.side_effect = ValueError("Playlist not found")

        with pytest.raises(ValueError) as exc_info:
            await extract_playlist_data("PLnonexistent")

        assert "Playlist not found" in str(exc_info.value)


BOT_CHECK = (
    "ERROR: [youtube:tab] PLtest123: Sign in to confirm you’re not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)
PLAYLIST_INFO = {"title": "Test Playlist", "entries": [{"id": "video1", "title": "Video 1"}]}


def _exit(n: int) -> str:
    return f"http://user-{n}:pass@p.webshare.io:80"


class _FakeFlatYoutubeDL:
    """YoutubeDL stand-in that answers per proxy exit the way ``ignoreerrors``
    does: the error line goes to the ``logger`` and extract_info returns None."""

    def __init__(self) -> None:
        self.errors: dict[str, str] = {}  # exit URL -> logged error line
        self.raises: dict[str, Exception] = {}  # exit URL -> raised error
        self.tried: list[str | None] = []

    def build(self, opts: dict) -> MagicMock:
        proxy = opts.get("proxy")
        self.tried.append(proxy)
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        ydl.extract_info.side_effect = lambda *_a, **_k: self._answer(proxy, opts.get("logger"))
        return ydl

    def _answer(self, proxy: str | None, ytdlp_logger) -> dict | None:
        if proxy in self.raises:
            raise self.raises[proxy]
        if proxy in self.errors:
            ytdlp_logger.error(self.errors[proxy])
            return None
        return PLAYLIST_INFO


class TestPlaylistExitRotation:
    """A bot check or 429 on one proxy exit moves the playlist lookup to the next."""

    @pytest.fixture
    def ydl(self, monkeypatch):
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", _exit(1))
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 3)
        fake = _FakeFlatYoutubeDL()
        with patch("src.services.video.playlist.yt_dlp.YoutubeDL", side_effect=fake.build):
            yield fake

    def test_should_extract_on_the_next_exit_when_the_first_gets_the_bot_check(self, ydl):
        ydl.errors[_exit(1)] = BOT_CHECK

        result = _extract_playlist_sync("PLtest123")

        assert (result.title, ydl.tried) == ("Test Playlist", [_exit(1), _exit(2)])

    def test_should_rotate_on_a_raised_429(self, ydl):
        from yt_dlp.utils import DownloadError

        ydl.raises[_exit(1)] = DownloadError("ERROR: HTTP Error 429: Too Many Requests")

        _extract_playlist_sync("PLtest123")

        assert ydl.tried == [_exit(1), _exit(2)]

    def test_should_fail_with_todays_error_when_every_exit_gets_the_bot_check(self, ydl):
        ydl.errors.update({_exit(n): BOT_CHECK for n in (1, 2, 3)})

        with pytest.raises(ValueError, match="Playlist not found or unavailable"):
            _extract_playlist_sync("PLtest123")

        assert ydl.tried == [_exit(1), _exit(2), _exit(3)]

    def test_should_not_rotate_when_the_playlist_does_not_exist(self, ydl):
        ydl.errors[_exit(1)] = "ERROR: [youtube:tab] PLtest123: The playlist does not exist."

        with pytest.raises(ValueError, match="not found"):
            _extract_playlist_sync("PLtest123")

        assert ydl.tried == [_exit(1)]

    def test_should_start_the_next_lookup_from_the_exit_that_worked(self, ydl):
        ydl.errors[_exit(1)] = BOT_CHECK
        _extract_playlist_sync("PLtest123")
        ydl.tried.clear()

        _extract_playlist_sync("PLtest123")

        assert ydl.tried == [_exit(2)]

    def test_should_make_one_attempt_with_a_single_exit(self, ydl, monkeypatch):
        from yt_dlp.utils import DownloadError

        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_EXIT_COUNT", 1)
        ydl.raises[_exit(1)] = DownloadError(BOT_CHECK)

        with pytest.raises(ValueError, match="Failed to extract playlist"):
            _extract_playlist_sync("PLtest123")

        assert ydl.tried == [_exit(1)]
