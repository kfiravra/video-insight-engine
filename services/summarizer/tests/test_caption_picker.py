"""Tests for the spoken-language caption rule (``caption_picker``, 2.4).

The rule: choose the language first — yt-dlp's ``language`` field, else the
language of the one original ASR track (``<lang>-orig``) — then a manual track
in it, else its untranslated auto track. A manual track in another language is
never chosen over the spoken language; today's waterfall runs only when the
rule finds nothing. Fixtures are synthetic dicts shaped like the dev probe of
2026-10-12 (yt-dlp metadata only); each video id names the shape it mirrors.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from src.services.video.caption_picker import (
    choose_caption_track,
    pick_spoken_track,
    spoken_language,
)
from src.services.video.youtube import VideoData, extract_video_data


def _orig(lang: str) -> list[dict[str, Any]]:
    """An untranslated json3 format (an original track)."""
    return [{"ext": "json3", "url": f"https://yt/timedtext?lang={lang}&fmt=json3"}]


def _translated(source: str, target: str) -> list[dict[str, Any]]:
    """YouTube's machine translation ``source`` → ``target`` (``tlang`` on the URL)."""
    return [{"ext": "json3", "url": f"https://yt/timedtext?lang={source}&tlang={target}&fmt=json3"}]


def _info(
    language: str | None,
    manual: dict[str, Any] | None = None,
    auto: dict[str, Any] | None = None,
    title: str = "A video",
) -> dict[str, Any]:
    return {
        "language": language,
        "title": title,
        "description": "",
        "subtitles": manual or {},
        "automatic_captions": auto or {},
    }


def _picked(info: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """``(kind, caption key, language)`` the picker chose."""
    choice = choose_caption_track("vid", info)
    track = choice.track
    return (track.kind if track else None, track.lang if track else None, choice.language)


class TestSpokenLanguage:
    def test_should_take_the_language_field_when_it_is_a_regional_tag(self):
        assert spoken_language({"language": "en-US"}, {}) == "en"

    def test_should_alias_the_legacy_hebrew_code_when_the_field_is_iw(self):
        assert spoken_language({"language": "iw"}, {}) == "he"

    def test_should_use_the_single_original_asr_language_when_the_field_is_absent(self):
        auto = {"de-orig": _orig("de"), "de": _orig("de"), "en": _translated("de", "en")}

        assert spoken_language({}, auto) == "de"

    def test_should_report_no_signal_when_several_original_asr_tracks_and_no_field(self):
        """An auto-dubbed video: one ASR track per dub audio."""
        auto = {"ar-orig": _orig("ar"), "en-orig": _orig("en"), "fr-FR-orig": _orig("fr")}

        assert spoken_language({}, auto) is None

    def test_should_count_regional_original_tracks_of_one_language_once(self):
        auto = {"en-orig": _orig("en"), "en-US-orig": _orig("en")}

        assert spoken_language({}, auto) == "en"

    def test_should_fall_back_to_the_original_asr_when_the_field_is_unsupported(self):
        assert spoken_language({"language": "xx"}, {"pl-orig": _orig("pl")}) == "pl"


class TestChooseCaptionTrackProbeShapes:
    def test_should_pick_the_hebrew_asr_when_a_dub_lists_english_asr_first(self):
        """EqaMrzd9nZU: Hebrew talk with an English auto-dub; today got the dub ASR."""
        auto = {
            "iw": _orig("iw") + _translated("en-US", "iw"),
            "en-US-orig": _orig("en-US"),
            "en-US": _orig("en-US"),
            "iw-orig": _orig("iw"),
        }

        assert _picked(_info("iw", auto=auto)) == ("auto-generated", "iw", "he")

    def test_should_pick_the_english_manual_track_over_a_dutch_one_listed_first(self):
        """vOkkWuEwUmc: manual nl, en-US, fr, … — today got Dutch."""
        manual = {"nl": _orig("nl"), "en-US": _orig("en-US"), "fr": _orig("fr")}
        auto = {"nl-nl": _orig("nl"), "en-orig": _orig("en"), "en": _orig("en")}

        assert _picked(_info("en-US", manual, auto)) == ("manual", "en-US", "en")

    def test_should_pick_the_english_manual_track_over_an_arabic_one_listed_first(self):
        """v8KaQr0MhjE / eo5XndJaz-Y: community subtitles, Arabic first."""
        manual = {"ar": _orig("ar"), "en": _orig("en"), "es": _orig("es")}
        auto = {"en": _orig("en"), "ar-ar": _orig("ar"), "en-orig": _orig("en")}

        assert _picked(_info("en", manual, auto)) == ("manual", "en", "en")

    def test_should_pick_the_english_auto_track_over_manual_tracks_in_other_languages(self):
        """yv64abAJvEA: manual pt + es-419 only — a manual track in another
        language never beats the spoken language."""
        manual = {"pt": _orig("pt"), "es-419": _orig("es")}
        auto = {"en-orig": _orig("en"), "en": _orig("en"), "pt-pt": _orig("pt")}

        assert _picked(_info("en-US", manual, auto)) == ("auto-generated", "en-orig", "en")

    def test_should_pick_the_untranslated_english_format_when_a_translation_is_listed_first(
        self,
    ):
        """T1dQhQAm8Tc: 21 dub ASRs; the ``en`` key lists the ar→en translation first."""
        auto = {
            "ar-orig": _orig("ar"),
            "ar": _orig("ar"),
            "en": _translated("ar", "en") + _orig("en"),
            "en-orig": _orig("en"),
        }

        choice = choose_caption_track("vid", _info("en-US", auto=auto))

        assert choice.track is not None
        assert (choice.track.lang, choice.track.url) == ("en", _orig("en")[0]["url"])

    def test_should_keep_the_same_manual_track_when_its_key_carries_a_track_suffix(self):
        """jMq8lEu-of0 / 2pp17E4E-O8: ``en-US-cvfXDfbeED0`` is English."""
        manual = {"en-US-cvfXDfbeED0": _orig("en")}
        auto = {"en-orig": _orig("en"), "en": _orig("en")}

        assert _picked(_info("en-US", manual, auto)) == ("manual", "en-US-cvfXDfbeED0", "en")

    def test_should_keep_the_english_asr_when_no_manual_track_exists(self):
        """wCkLNqy5OHE / Jru5B044HOs: unchanged."""
        auto = {"en-orig": _orig("en"), "en": _orig("en")}

        assert _picked(_info("en", auto=auto)) == ("auto-generated", "en-orig", "en")

    def test_should_pick_the_tagged_language_asr_over_its_english_translation(self):
        """TNhaISOUy6Q: uploader set Polish; the rule takes the Polish ASR (known limitation)."""
        auto = {"pl-orig": _orig("pl"), "pl": _orig("pl"), "en": _translated("pl", "en")}

        assert _picked(_info("pl", auto=auto)) == ("auto-generated", "pl-orig", "pl")


class TestChooseCaptionTrackSignals:
    def test_should_use_the_original_asr_language_when_the_field_is_absent(self):
        auto = {"de-orig": _orig("de"), "de": _orig("de"), "en": _translated("de", "en")}

        assert _picked(_info(None, auto=auto)) == ("auto-generated", "de-orig", "de")

    def test_should_fall_back_to_the_waterfall_when_several_original_asrs_and_no_field(self):
        """No spoken signal: today's code picks (English preference, translation last)."""
        auto = {
            "ar-orig": _orig("ar"),
            "fr-orig": _orig("fr"),
            "en": _translated("ar", "en"),
        }

        choice = choose_caption_track("vid", _info(None, auto=auto))

        assert choice.track is not None
        assert (choice.track.url, choice.language, choice.spoken_language) == (
            _translated("ar", "en")[0]["url"],
            "en",
            None,
        )

    def test_should_fall_back_to_the_waterfall_when_the_spoken_language_has_no_track(self):
        """``da`` field, only an English ASR: today's pick, the spoken signal kept."""
        auto = {"en-orig": _orig("en"), "en": _orig("en")}

        choice = choose_caption_track("vid", _info("da", auto=auto))

        assert (choice.track.lang if choice.track else None, choice.language) == ("en-orig", "en")
        assert choice.spoken_language == "da"

    def test_should_not_take_a_translation_into_the_spoken_language_as_its_track(self):
        auto = {"he": _translated("en", "he"), "en-orig": _orig("en")}

        assert _picked(_info("he", auto=auto)) == ("auto-generated", "en-orig", "en")

    def test_should_match_a_legacy_iw_key_when_the_field_is_he(self):
        assert _picked(_info("he", auto={"iw": _orig("iw")})) == ("auto-generated", "iw", "he")

    def test_should_match_an_iw_orig_key_when_the_field_is_iw(self):
        auto = {"iw-orig": _orig("iw")}

        assert _picked(_info("iw", auto=auto)) == ("auto-generated", "iw-orig", "he")

    def test_should_return_the_spoken_language_alongside_the_track_it_picked(self):
        choice = choose_caption_track("vid", _info("iw", auto={"iw": _orig("iw")}))

        assert (choice.language, choice.spoken_language) == ("he", "he")

    def test_should_pick_nothing_when_the_spoken_language_is_unknown(self):
        assert pick_spoken_track(None, {"en": _orig("en")}, {}) is None

    def test_should_pick_nothing_when_no_caption_dicts_are_listed(self):
        info = {"language": "en", "title": "x", "subtitles": None, "automatic_captions": None}

        assert choose_caption_track("vid", info).track is None


class TestChooseCaptionTrackFallbackAliases:
    """The fallback aliases caption keys too: a waterfall ``he`` (from a manual
    ``iw`` key) must not fall through to English labelled ``he``."""

    def test_should_pick_the_manual_iw_track_when_no_field_and_no_asr(self):
        manual = {"iw": _orig("iw")}
        auto = {"en-iw": _translated("iw", "en")}

        assert _picked(_info(None, manual, auto)) == ("manual", "iw", "he")

    def test_should_pick_the_manual_iw_track_over_manual_english_when_several_asrs(self):
        manual = {"iw": _orig("iw"), "en": _orig("en")}
        auto = {"ar-orig": _orig("ar"), "fr-orig": _orig("fr")}

        assert _picked(_info(None, manual, auto)) == ("manual", "iw", "he")


class TestVideoDataCarriesThePick:
    """``extract_video_data`` stamps the rule's pick and both language signals."""

    @staticmethod
    async def _video_data(info: dict[str, Any]) -> VideoData:
        info = {"id": "vid", "uploader": "c", "duration": 300, "thumbnails": [], **info}
        with patch("src.services.video.youtube._extract_with_retry", return_value=info):
            return await extract_video_data("vid")

    async def test_should_label_the_video_with_the_spoken_language_when_it_had_a_track(self):
        manual = {"ar": _orig("ar"), "en": _orig("en")}

        video_data = await self._video_data(_info("en", manual))

        assert (video_data.caption_track, video_data.caption_lang) == ("manual", "en")
        assert (video_data.language, video_data.spoken_language) == ("en", "en")

    async def test_should_keep_the_waterfall_language_and_the_spoken_signal_on_fallback(self):
        video_data = await self._video_data(_info("da", auto={"en-orig": _orig("en")}))

        assert (video_data.language, video_data.spoken_language) == ("en", "da")
