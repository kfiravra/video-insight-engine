"""YouTube playlist extraction using yt-dlp.

This module provides fast playlist metadata extraction using yt-dlp's
extract_flat mode, which retrieves playlist info without downloading videos.
"""

import asyncio
import logging
from dataclasses import dataclass

import yt_dlp  # type: ignore[import-untyped]
from yt_dlp.utils import DownloadError, ExtractorError  # type: ignore[import-untyped]

from src.services.media.download_utils import ytdlp_proxy_url

logger = logging.getLogger(__name__)


@dataclass
class PlaylistVideoInfo:
    """Information about a single video in a playlist."""

    video_id: str
    title: str
    position: int
    duration: int | None
    thumbnail_url: str | None


@dataclass
class PlaylistData:
    """Complete playlist data extracted from yt-dlp."""

    playlist_id: str
    title: str
    channel: str | None
    thumbnail_url: str | None
    videos: list[PlaylistVideoInfo]

    @property
    def total_videos(self) -> int:
        return len(self.videos)


def _build_playlist_opts() -> dict:
    """Build yt-dlp options for fast playlist extraction; YOUTUBE_PROXY_URL applies when set."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",  # Fast mode: metadata only, no download
        "ignoreerrors": True,  # Skip unavailable videos
        "skip_download": True,
    }

    proxy_url = ytdlp_proxy_url()
    if proxy_url:
        opts["proxy"] = proxy_url

    return opts


def _extract_playlist_sync(playlist_id: str, max_videos: int = 100) -> PlaylistData:
    """
    Extract playlist data using yt-dlp (synchronous).

    Uses extract_flat mode for fast metadata-only extraction.
    Does not download any video content.

    Args:
        playlist_id: YouTube playlist ID (e.g., "PLsDq_ElIL9Vaz")
        max_videos: Maximum number of videos to return (default 100)

    Returns:
        PlaylistData with playlist info and video list

    Raises:
        ValueError: If playlist not found or extraction fails
    """
    url = f"https://www.youtube.com/playlist?list={playlist_id}"

    opts = _build_playlist_opts()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError, OSError, ConnectionError) as e:
        raise ValueError(f"Failed to extract playlist: {e}") from e

    if not info:
        raise ValueError("Playlist not found or unavailable")

    # Extract playlist metadata
    playlist_title = info.get("title", "Unknown Playlist")
    channel = info.get("uploader") or info.get("channel")

    # Get playlist thumbnail (first video's thumbnail or playlist image)
    thumbnail_url = None
    thumbnails = info.get("thumbnails", [])
    if thumbnails:
        for thumb in reversed(thumbnails):
            if thumb.get("url"):
                thumbnail_url = thumb["url"]
                break

    # Extract video entries
    entries = info.get("entries", [])
    videos: list[PlaylistVideoInfo] = []

    for idx, entry in enumerate(entries):
        if entry is None:  # Skipped/unavailable video
            continue
        if idx >= max_videos:
            break

        video_id = entry.get("id")
        if not video_id:
            continue

        title = entry.get("title", "Unknown Title")
        duration = entry.get("duration")

        # Get video thumbnail
        video_thumb = None
        if video_id:
            video_thumb = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"

        videos.append(
            PlaylistVideoInfo(
                video_id=video_id,
                title=title,
                position=len(videos),  # 0-indexed position
                duration=int(duration) if duration else None,
                thumbnail_url=video_thumb,
            )
        )

    logger.info(
        "Playlist %s: extracted %d videos (title=%s, channel=%s)",
        playlist_id,
        len(videos),
        playlist_title,
        channel,
    )

    # Use first video's thumbnail if no playlist thumbnail
    if not thumbnail_url and videos:
        thumbnail_url = videos[0].thumbnail_url

    return PlaylistData(
        playlist_id=playlist_id,
        title=playlist_title,
        channel=channel,
        thumbnail_url=thumbnail_url,
        videos=videos,
    )


async def extract_playlist_data(playlist_id: str, max_videos: int = 100) -> PlaylistData:
    """
    Extract playlist data using yt-dlp (async wrapper).

    Runs the blocking yt-dlp call in a thread pool to avoid
    blocking the event loop.

    Args:
        playlist_id: YouTube playlist ID (e.g., "PLsDq_ElIL9Vaz")
        max_videos: Maximum number of videos to return (default 100)

    Returns:
        PlaylistData with playlist info and video list

    Raises:
        ValueError: If playlist not found or extraction fails

    Example:
        playlist = await extract_playlist_data("PLsDq_ElIL9Vaz")
        # playlist.title -> "React Tutorial Series"
        # len(playlist.videos) -> 23
        # playlist.videos[0].position -> 0
        # playlist.videos[0].title -> "Introduction to React"
    """
    return await asyncio.to_thread(_extract_playlist_sync, playlist_id, max_videos)
