"""Tests for the language-detection waterfall and subtitle-pick preference
added to ``services/summarizer/src/services/video/youtube.py``.

We deliberately don't hit yt-dlp here — both functions under test accept the
raw ``info`` dict / caption dicts, so we can construct realistic fixtures
without any network.
"""

from __future__ import annotations

from src.services.video.youtube import (
    _pick_subtitle_url,
    resolve_video_language,
)

# ─── resolve_video_language ──────────────────────────────────────────────────


class TestResolveVideoLanguage:
    def test_uses_audio_language_tag_when_no_stronger_signal(self):
        """YouTube's audio-language tag is honoured when no script/manual signal
        outranks it. Demoted below script + manual + en-auto because YouTube
        mislabels videos (observed: Bruno Mars tagged as Danish)."""
        info = {"language": "es", "title": "Untitled", "description": ""}
        manual: dict = {}
        auto = {"fr": [], "de": []}  # no English in auto
        assert resolve_video_language(info, manual, auto) == "es"

    def test_script_detection_wins_over_audio_tag(self):
        """When the YouTube tag disagrees with the title's actual script, trust
        the title. (Bruno Mars regression: YouTube tagged the English video as
        Danish; title is Latin so we'd fall through and pick en from auto.)"""
        info = {
            "language": "fr",
            "title": "الأقوى على الاطلاق 😲 عبدالله أشكناني ومحمد سهيل",
            "description": "",
        }
        assert resolve_video_language(info, {}, {}) == "ar"

    def test_falls_back_to_title_script_for_non_latin(self):
        """When yt-dlp doesn't tag the language, title script detection catches
        Arabic/Hebrew/CJK titles. Latin titles return None at this step."""
        info = {
            "language": None,
            "title": "الأقوى على الاطلاق 😲 عبدالله أشكناني ومحمد سهيل",
            "description": "",
        }
        assert resolve_video_language(info, {}, {}) == "ar"

    def test_falls_back_to_caption_keys_when_title_is_latin(self):
        """Latin titles slip past the script step — caption-track keys are the
        next signal. Manual captions are preferred over auto-translated ones."""
        info = {"language": None, "title": "Tutorial demo", "description": ""}
        manual = {"he": [{"ext": "json3", "url": "x"}]}
        auto = {"en": [{"ext": "json3", "url": "y"}]}
        assert resolve_video_language(info, manual, auto) == "he"

    def test_returns_none_when_no_signal(self):
        """English-tagged video with English captions = no foreign signal, None."""
        info = {"language": None, "title": "Untitled", "description": ""}
        manual: dict = {}
        auto: dict = {}
        assert resolve_video_language(info, manual, auto) is None

    def test_normalizes_three_letter_audio_tag(self):
        """yt-dlp sometimes returns 'jpn' instead of 'ja' — normalization must run."""
        info = {"language": "jpn", "title": "x", "description": ""}
        assert resolve_video_language(info, {}, {}) == "ja"

    def test_ignores_invalid_audio_tag_and_uses_title(self):
        """A bogus ``language`` tag falls through to script detection. Title
        plus description must exceed ``detect_language_by_script``'s 20-char
        minimum, so we use a realistic Chinese title."""
        info = {
            "language": "xyzzy",
            "title": "如何使用 Python 编写你的第一个网络爬虫程序",
            "description": "",
        }
        assert resolve_video_language(info, {}, {}) == "zh"

    def test_prefers_english_auto_caption_over_dict_iteration_order(self):
        """Regression: Bruno Mars's 'The Lazy Song' has auto-captions in many
        languages. Dict iteration order is implementation-defined, and the prior
        loop picked Danish first. English MUST win when present among auto-captions
        and no stronger signal exists."""
        info = {"language": None, "title": "Bruno Mars - The Lazy Song", "description": ""}
        manual: dict = {}
        auto = {"da": [], "en": [], "fr": [], "de": []}
        assert resolve_video_language(info, manual, auto) == "en"

    def test_manual_track_still_wins_over_english_auto(self):
        """A creator-uploaded manual track in a non-English language must
        win over English auto-captions — that's the Arabic-video case."""
        info = {"language": None, "title": "Tutorial", "description": ""}
        manual = {"ar": [{"ext": "json3", "url": "x"}]}
        auto = {"en": [{"ext": "json3", "url": "y"}]}
        assert resolve_video_language(info, manual, auto) == "ar"

    def test_description_contributes_to_script_detection(self):
        """A short or English title with a long Hebrew description is still Hebrew."""
        info = {
            "language": None,
            "title": "Workout",
            "description": "אימון בטן של 7 דקות בבית, ללא ציוד וללא פלאנקים." * 5,
        }
        assert resolve_video_language(info, {}, {}) == "he"

    def test_latin_script_non_english_with_en_auto_classifies_as_english(self):
        """INTENTIONAL TRADEOFF — document, don't fix.

        A Spanish/French/etc. video with no manual captions and YouTube's
        on-demand en auto-translation will be classified as English, NOT as
        the source language. This is the price of preferring en-auto over
        ``info['language']``: it protects against the documented Bruno-Mars
        false-positive ('English video tagged Danish by YouTube') at the
        cost of misclassifying any Latin-script non-English video without a
        creator-uploaded caption track.

        Without a tlang-aware signal (yt-dlp doesn't reliably distinguish
        the original auto-generated track from on-demand translations), this
        is the safer of the two failure modes — the pipeline still runs on
        coherent English content rather than processing a non-English audio
        track in the wrong language. Source-language toggle is lost; the
        content is still useful.

        Revisit if/when yt-dlp exposes a reliable tlang signal, or if we add
        Latin-script stopword detection (le/la for French, el/los for Spanish).
        """
        info = {"language": "es", "title": "Tutorial", "description": ""}
        manual: dict = {}
        # Spanish video with both the original es auto-track AND YouTube's
        # on-demand en auto-translation. The pipeline picks "en".
        auto = {"es": [{"ext": "json3", "url": "x"}], "en": [{"ext": "json3", "url": "y"}]}
        assert resolve_video_language(info, manual, auto) == "en"


# ─── _pick_subtitle_url ──────────────────────────────────────────────────────


class TestPickSubtitleUrl:
    def _track(self, url: str) -> list[dict]:
        return [{"ext": "json3", "url": url}]

    def test_picks_original_manual_over_english_auto(self):
        """Arabic manual captions beat English auto-translated captions even
        when the auto-EN track exists. This is the regression that mislabeled
        Arabic videos as English."""
        manual = {"ar": self._track("ar-manual")}
        auto = {"en": self._track("en-auto"), "ar": self._track("ar-auto")}
        assert _pick_subtitle_url("ar", manual, auto).url == "ar-manual"

    def test_picks_original_auto_when_no_manual(self):
        """If only auto captions exist, the original-language auto track still
        beats the English-language auto track."""
        manual: dict = {}
        auto = {"en": self._track("en-auto"), "he": self._track("he-auto")}
        assert _pick_subtitle_url("he", manual, auto).url == "he-auto"

    def test_falls_back_to_english_when_original_absent(self):
        """If the original language has no track at all, English is acceptable."""
        manual = {"en": self._track("en-manual")}
        auto = {"en": self._track("en-auto")}
        assert _pick_subtitle_url("ar", manual, auto).url == "en-manual"

    def test_matches_regional_variant(self):
        """``ar`` matches caption keys ``ar`` and ``ar-SA`` alike."""
        manual = {"ar-SA": self._track("ar-SA-track")}
        auto: dict = {}
        assert _pick_subtitle_url("ar", manual, auto).url == "ar-SA-track"

    def test_no_detected_language_still_picks_english(self):
        """When language resolution failed, we still want English captions."""
        manual = {"en": self._track("en-track")}
        auto: dict = {}
        assert _pick_subtitle_url(None, manual, auto).url == "en-track"

    def test_returns_none_when_no_json3_track(self):
        """Tracks without a json3 variant aren't usable."""
        manual = {"ar": [{"ext": "vtt", "url": "ar-vtt"}]}
        auto: dict = {}
        assert _pick_subtitle_url("ar", manual, auto) is None

    def test_english_detected_does_not_double_count(self):
        """Detected language 'en' must not produce duplicate work or a None result."""
        manual = {"en": self._track("en-manual")}
        auto: dict = {}
        assert _pick_subtitle_url("en", manual, auto).url == "en-manual"

    def test_manual_track_reports_kind_and_matched_key(self):
        """The picked track carries its bucket and the *matched* caption key —
        ``ar-SA``, not the detected ``ar`` — so the transcript trail names the
        exact track that was tried."""
        manual = {"ar-SA": self._track("ar-SA-track")}
        auto = {"en": self._track("en-auto")}
        track = _pick_subtitle_url("ar", manual, auto)
        assert track is not None
        assert (track.kind, track.lang) == ("manual", "ar-SA")

    def test_english_auto_fallback_reports_auto_generated_kind(self):
        """Falling through to YouTube's ASR English track is labelled as such,
        so downstream can tell a creator upload from an auto-translation."""
        manual: dict = {}
        auto = {"en": self._track("en-auto")}
        track = _pick_subtitle_url("ar", manual, auto)
        assert track is not None
        assert (track.kind, track.lang) == ("auto-generated", "en")


class TestPickOriginalOverTranslation:
    """Within a language, an original track beats YouTube's machine translation
    (``tlang`` on the URL) into that language."""

    ORIGINAL_EN = "https://www.youtube.com/api/timedtext?lang=en&kind=asr&fmt=json3"
    AR_TO_EN = "https://www.youtube.com/api/timedtext?lang=ar&kind=asr&tlang=en&fmt=json3"

    @staticmethod
    def _json3(*urls: str) -> list[dict]:
        return [{"ext": "json3", "url": url} for url in urls]

    def test_should_pick_the_original_when_a_translation_is_listed_first(self):
        """Auto-dubbed video shape (prod T1dQhQAm8Tc): one ASR track per dub, so
        yt-dlp's ``en`` key lists the ar→en translation before the English ASR."""
        auto = {
            "ar-orig": self._json3("ar-orig"),
            "ar": self._json3("ar-asr"),
            "en": self._json3(self.AR_TO_EN, self.ORIGINAL_EN),
            "en-orig": self._json3(self.ORIGINAL_EN),
        }

        track = _pick_subtitle_url("en", {}, auto)

        assert track is not None
        assert (track.url, track.kind, track.lang) == (self.ORIGINAL_EN, "auto-generated", "en")

    def test_should_not_let_a_translated_regional_key_beat_the_original(self):
        """A prefix-matched translation key (``en-ar``: English from Arabic
        manual subs) listed first must not win over the original ``en``."""
        auto = {
            "en-ar": self._json3(self.AR_TO_EN),
            "en": self._json3(self.ORIGINAL_EN),
        }

        track = _pick_subtitle_url("en", {}, auto)

        assert track is not None
        assert (track.url, track.lang) == (self.ORIGINAL_EN, "en")

    def test_should_fall_back_to_the_translation_without_an_original(self):
        """No original track in the language: the translation is still used
        (the documented Latin-script tradeoff in TestResolveVideoLanguage)."""
        auto = {"es": self._json3("es-asr"), "en": self._json3(self.AR_TO_EN)}

        track = _pick_subtitle_url("en", {}, auto)

        assert track is not None
        assert track.url == self.AR_TO_EN

    def test_should_keep_the_detected_language_over_an_english_original(self):
        """Original-first applies within a language only — a Hebrew video keeps
        its he translation rather than switching to English text."""
        en_to_he = "https://www.youtube.com/api/timedtext?lang=en&kind=asr&tlang=he&fmt=json3"
        auto = {"en": self._json3(self.ORIGINAL_EN), "he": self._json3(en_to_he)}

        track = _pick_subtitle_url("he", {}, auto)

        assert track is not None
        assert track.url == en_to_he
