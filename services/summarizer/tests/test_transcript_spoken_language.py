"""The youtube-transcript-api fallback prefers the spoken language (2.4).

When yt-dlp knew the spoken language (``VideoData.spoken_language``) the
caption API takes a manual track in it, then a generated one, before its
English-first order. Only the spoken signal is passed — never the language
waterfall's guess, which can be ``ar`` for an English video with community
subtitles. Tracks are real ``youtube_transcript_api`` objects (no network:
nothing is fetched).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import requests
from youtube_transcript_api import Transcript, TranscriptList

from src.services.transcription import transcript_fetcher
from src.services.transcription.transcript import _fetch_once, _select_track
from src.services.transcription.transcript_fetcher import fetch_transcript
from tests.test_transcript_fetcher import _drain, _video_data_without_captions


def _track(language_code: str, *, generated: bool) -> Transcript:
    return Transcript(
        requests.Session(),
        "vid",
        f"https://yt/timedtext?lang={language_code}",
        language_code,
        language_code,
        generated,
        [],
    )


def _transcript_list(*tracks: Transcript) -> TranscriptList:
    manual = {t.language_code: t for t in tracks if not t.is_generated}
    generated = {t.language_code: t for t in tracks if t.is_generated}
    return TranscriptList("vid", manual, generated, [])


def _selected(transcript_list: TranscriptList, spoken: str | None) -> tuple[str, str]:
    track, transcript_type = _select_track(transcript_list, spoken)
    assert track is not None
    return track.language_code, transcript_type


class TestSelectTrackSpokenLanguage:
    def test_should_pick_the_manual_spoken_language_track_over_manual_english(self):
        tracks = _transcript_list(_track("en", generated=False), _track("de", generated=False))

        assert _selected(tracks, "de") == ("de", "manual")

    def test_should_pick_the_generated_spoken_language_track_over_manual_english(self):
        tracks = _transcript_list(_track("en", generated=False), _track("de", generated=True))

        assert _selected(tracks, "de") == ("de", "auto-generated")

    def test_should_prefer_manual_over_generated_within_the_spoken_language(self):
        tracks = _transcript_list(_track("de", generated=True), _track("de-DE", generated=False))

        assert _selected(tracks, "de") == ("de-DE", "manual")

    def test_should_match_the_legacy_iw_code_when_the_spoken_language_is_he(self):
        tracks = _transcript_list(_track("en", generated=True), _track("iw", generated=True))

        assert _selected(tracks, "he") == ("iw", "auto-generated")

    def test_should_keep_the_english_first_order_when_the_spoken_language_has_no_track(self):
        tracks = _transcript_list(_track("ar", generated=False), _track("en", generated=True))

        assert _selected(tracks, "da") == ("en", "auto-generated")

    def test_should_keep_the_english_first_order_when_the_spoken_language_is_unknown(self):
        tracks = _transcript_list(_track("ar", generated=False), _track("en", generated=True))

        assert _selected(tracks, None) == ("en", "auto-generated")


class TestFetchOnceSpokenLanguage:
    @patch("src.services.transcription.transcript.YouTubeTranscriptApi")
    def test_should_fetch_the_spoken_language_track_when_one_is_given(self, mock_api_class):
        hebrew = MagicMock(language_code="iw", is_generated=True)
        hebrew.fetch.return_value = [{"text": "שלום", "start": 0.0, "duration": 1.0}]
        english = MagicMock(language_code="en", is_generated=False)
        transcript_list = MagicMock()
        transcript_list.__iter__.return_value = iter([english, hebrew])
        transcript_list.find_manually_created_transcript.return_value = english
        mock_api_class.return_value.list.return_value = transcript_list

        _segments, text, transcript_type, language = _fetch_once("vid", None, "he")

        assert (text, transcript_type, language) == ("שלום", "auto-generated", "iw")


class TestFetcherPassesTheSpokenSignal:
    async def _caption_api_call(self, video_data: MagicMock) -> AsyncMock:
        get_transcript = AsyncMock(return_value=([{"text": "hi"}], "hi", "manual", "en"))
        with (
            patch.object(transcript_fetcher.S3Client, "is_available", return_value=False),
            patch.object(transcript_fetcher, "get_transcript", new=get_transcript),
            patch.object(transcript_fetcher, "caption_negative_cache") as negative_cache,
        ):
            negative_cache.is_marked = AsyncMock(return_value=False)
            negative_cache.mark = AsyncMock()
            await _drain(fetch_transcript("vid123", video_data, duration=300))
        return get_transcript

    async def test_should_pass_the_spoken_language_to_the_caption_api_when_known(self):
        video_data = _video_data_without_captions()
        video_data.spoken_language = "he"

        get_transcript = await self._caption_api_call(video_data)

        get_transcript.assert_awaited_once_with("vid123", spoken_language="he")

    async def test_should_pass_no_spoken_language_when_yt_dlp_had_no_signal(self):
        video_data = _video_data_without_captions()
        video_data.spoken_language = None

        get_transcript = await self._caption_api_call(video_data)

        get_transcript.assert_awaited_once_with("vid123", spoken_language=None)

    async def test_should_pass_only_the_spoken_signal_when_the_waterfall_guessed_another(self):
        """The waterfall's ``ar`` (community subtitles on an English video) never
        reaches the caption API — only yt-dlp's spoken signal does."""
        video_data = _video_data_without_captions()
        video_data.language = "ar"
        video_data.spoken_language = "en"

        get_transcript = await self._caption_api_call(video_data)

        get_transcript.assert_awaited_once_with("vid123", spoken_language="en")
