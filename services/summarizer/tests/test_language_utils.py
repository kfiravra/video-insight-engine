"""Tests for language detection and utility functions."""

from __future__ import annotations

import pytest

from src.utils.language_utils import (
    RTL_LANGUAGES,
    detect_language_by_script,
    detect_language_from_text,
    get_language_name,
    is_rtl,
    normalize_language_code,
)


class TestIsRtl:
    """Tests for is_rtl()."""

    @pytest.mark.parametrize("code", ["he", "ar", "fa", "ur", "yi"])
    def test_should_return_true_for_rtl_languages(self, code: str):
        """RTL languages (Hebrew, Arabic, Persian, Urdu, Yiddish) return True."""
        assert is_rtl(code) is True

    @pytest.mark.parametrize("code", ["en", "es", "fr", "de", "zh", "ja", "ru"])
    def test_should_return_false_for_ltr_languages(self, code: str):
        """LTR languages return False."""
        assert is_rtl(code) is False

    def test_should_return_false_for_unknown_code(self):
        """Unknown language code returns False."""
        assert is_rtl("xx") is False

    def test_rtl_languages_frozenset_has_expected_members(self):
        """RTL_LANGUAGES contains exactly the expected codes."""
        assert RTL_LANGUAGES == frozenset({"he", "ar", "fa", "ur", "yi"})


class TestGetLanguageName:
    """Tests for get_language_name()."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("en", "English"),
            ("he", "Hebrew"),
            ("ar", "Arabic"),
            ("fa", "Persian"),
            ("es", "Spanish"),
            ("ja", "Japanese"),
            ("zh", "Chinese"),
        ],
    )
    def test_should_return_correct_name_for_known_codes(self, code: str, expected: str):
        """Known ISO 639-1 codes return human-readable names."""
        assert get_language_name(code) == expected

    def test_should_return_uppercased_code_for_unknown(self):
        """Unknown codes return the code uppercased."""
        assert get_language_name("xx") == "XX"
        assert get_language_name("zz") == "ZZ"


class TestNormalizeLanguageCode:
    """Tests for normalize_language_code()."""

    def test_should_extract_two_letter_code_from_locale(self):
        """Locale codes like 'en-US' normalize to 'en'."""
        assert normalize_language_code("en-US") == "en"

    def test_should_handle_zh_hans(self):
        """Chinese variant 'zh-Hans' normalizes to 'zh'."""
        assert normalize_language_code("zh-Hans") == "zh"

    def test_should_handle_pt_br(self):
        """Portuguese variant 'pt-BR' normalizes to 'pt'."""
        assert normalize_language_code("pt-BR") == "pt"

    def test_should_return_none_for_none_input(self):
        """None input returns None."""
        assert normalize_language_code(None) is None

    def test_should_return_none_for_empty_string(self):
        """Empty string returns None."""
        assert normalize_language_code("") is None

    def test_should_lowercase_the_code(self):
        """Uppercase codes are lowercased."""
        assert normalize_language_code("EN") == "en"
        assert normalize_language_code("He") == "he"

    def test_should_strip_whitespace(self):
        """Leading/trailing whitespace is stripped."""
        assert normalize_language_code("  fr  ") == "fr"

    def test_should_return_none_for_single_char(self):
        """Single character input returns None (not a valid 2-letter code)."""
        assert normalize_language_code("e") is None

    def test_should_return_none_for_numeric_input(self):
        """Numeric input returns None."""
        assert normalize_language_code("12") is None

    def test_should_map_whisper_language_name_to_code(self):
        """Whisper returns language as a NAME — must map to the right ISO code."""
        assert normalize_language_code("chinese") == "zh"
        assert normalize_language_code("hebrew") == "he"
        assert normalize_language_code("english") == "en"
        assert normalize_language_code("japanese") == "ja"
        assert normalize_language_code("arabic") == "ar"

    def test_should_map_iso_639_3_to_639_1(self):
        """Three-letter ISO codes map to two-letter codes correctly."""
        assert normalize_language_code("chi") == "zh"
        assert normalize_language_code("zho") == "zh"
        assert normalize_language_code("jpn") == "ja"
        assert normalize_language_code("heb") == "he"
        assert normalize_language_code("ara") == "ar"
        assert normalize_language_code("eng") == "en"

    def test_should_reject_unsupported_two_letter_codes(self):
        """Bogus two-letter strings (Chamorro 'ch', invented 'jp') are rejected."""
        assert normalize_language_code("ch") is None
        assert normalize_language_code("jp") is None
        assert normalize_language_code("xy") is None

    def test_should_handle_underscore_locale_format(self):
        """Some sources use 'en_US' with underscores instead of dashes."""
        assert normalize_language_code("en_US") == "en"
        assert normalize_language_code("zh_Hans") == "zh"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("iw", "he"),
            ("IW", "he"),
            ("iw-orig", "he"),
            ("iw-IL", "he"),
            ("ji", "yi"),
            ("in", "id"),
        ],
    )
    def test_should_alias_youtube_legacy_codes_when_normalizing(self, raw: str, expected: str):
        """YouTube still serves the pre-1989 codes in ``language`` and caption keys."""
        assert normalize_language_code(raw) == expected


class TestDetectLanguageByScript:
    """Tests for detect_language_by_script()."""

    def test_should_detect_hebrew_script(self):
        """Text with Hebrew characters detects as 'he'."""
        hebrew_text = "שלום עולם, זהו טקסט בעברית שמכיל מספיק תווים כדי לעבור את הסף"
        assert detect_language_by_script(hebrew_text) == "he"

    def test_should_detect_arabic_script(self):
        """Text with Arabic characters detects as 'ar'."""
        arabic_text = "مرحبا بالعالم، هذا نص بالعربية يحتوي على عدد كافٍ من الأحرف"
        assert detect_language_by_script(arabic_text) == "ar"

    def test_should_detect_cjk_script(self):
        """Text with CJK characters detects as 'zh'."""
        chinese_text = "你好世界这是一段中文文本包含足够多的字符来通过检测阈值的要求"
        assert detect_language_by_script(chinese_text) == "zh"

    def test_should_detect_japanese_as_ja_not_zh(self):
        """Kanji+kana Japanese detects as 'ja', not 'zh'.

        Regression: kanji live in the CJK Unicode block, so a CJK-before-kana
        check order mislabeled kanji-heavy Japanese as Chinese — which then
        translated the source-language toggle into the wrong language.
        """
        japanese_text = (
            "これは日本語のテストです。十分に長い文章を書いて、"
            "漢字とひらがなとカタカナを混ぜています。"
        )
        assert detect_language_by_script(japanese_text) == "ja"

    def test_should_detect_korean_script(self):
        """Text with Hangul characters detects as 'ko'."""
        korean_text = "안녕하세요 세계입니다 이것은 한국어 텍스트입니다 충분한 문자를 포함합니다"
        assert detect_language_by_script(korean_text) == "ko"

    def test_should_detect_cyrillic_script(self):
        """Text with Cyrillic characters detects as 'ru'."""
        russian_text = "Привет мир, это текст на русском языке с достаточным количеством символов"
        assert detect_language_by_script(russian_text) == "ru"

    def test_should_return_none_for_latin_text(self):
        """Latin/English text returns None (no specific script detected)."""
        english_text = "Hello world, this is a text in English with enough characters to pass the minimum length"
        assert detect_language_by_script(english_text) is None

    def test_should_return_none_for_short_text(self):
        """Text shorter than 20 characters returns None."""
        assert detect_language_by_script("שלום") is None

    def test_should_return_none_for_empty_text(self):
        """Empty text returns None."""
        assert detect_language_by_script("") is None


class TestDetectLanguageFromText:
    """Text-based detection (langdetect) — the fallback that resolves
    Latin-script non-English languages that the script heuristic can't.

    Guarded by importorskip because langdetect is a runtime dependency: in a
    deployment missing it, detect_language_from_text safely returns None (which
    is exactly the bug that mislabeled a Latin-script video as English)."""

    def test_detects_spanish_latin_script_when_installed(self):
        pytest.importorskip("langdetect")
        spanish = (
            "Hola a todos y bienvenidos a este video donde vamos a hablar "
            "sobre la importancia de aprender un nuevo idioma cada año."
        )
        assert detect_language_from_text(spanish) == "es"

    def test_returns_none_for_short_text(self):
        """Under the 50-char floor, detection returns None regardless of deps."""
        assert detect_language_from_text("hola") is None
