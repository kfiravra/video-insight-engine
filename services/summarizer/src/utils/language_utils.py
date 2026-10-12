"""Language detection and utility functions for multi-language pipeline support."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ISO 639-1 codes for right-to-left languages
RTL_LANGUAGES = frozenset({"he", "ar", "fa", "ur", "yi"})

# ISO 639-1 language name mapping (common languages)
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "he": "Hebrew",
    "ar": "Arabic",
    "fa": "Persian",
    "ur": "Urdu",
    "yi": "Yiddish",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "cs": "Czech",
    "ro": "Romanian",
    "hu": "Hungarian",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "uk": "Ukrainian",
    "bg": "Bulgarian",
    "hr": "Croatian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "ml": "Malayalam",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "sw": "Swahili",
    "af": "Afrikaans",
    "ca": "Catalan",
    "eu": "Basque",
    "gl": "Galician",
    "sr": "Serbian",
}


# Injected at the top of every generation prompt. The pipeline is
# English-canonical: all stages must emit English regardless of the transcript's
# language (the source-language version is produced later by translating this
# English output). An ABSENT instruction is not enough — with a non-English
# transcript the model otherwise echoes the source language for labels/content.
ENGLISH_OUTPUT_DIRECTIVE = (
    "OUTPUT LANGUAGE — CRITICAL: Write EVERY content value (titles, labels, "
    "descriptions, summaries, key points, questions, answer options, tips) in "
    "ENGLISH. If the transcript is in another language, translate its meaning "
    "into natural, fluent English — do NOT copy the original language. JSON "
    "field names and tab IDs stay in English."
)


def is_rtl(language: str) -> bool:
    """Check if a language code is RTL."""
    return language in RTL_LANGUAGES


def is_sound_only_video(
    is_music: bool,
    language: str | None,
    raw_text: str,
    duration: int,
    wps_threshold: float,
) -> bool:
    """True when a music-category video has essentially no speech.

    A music video with a few hallucinated words across many minutes is almost
    always instrumental (trance, opera passages, ambient, piano sonata). Vocal
    songs sit well above the threshold because Whisper transcribes real lyrics.
    """
    if not is_music or not language or language == "en":
        return False
    word_count = len(raw_text.split())
    safe_duration = max(duration or 1, 1)
    return (word_count / safe_duration) < wps_threshold


def get_language_name(code: str) -> str:
    """Get human-readable language name from ISO 639-1 code."""
    return _LANGUAGE_NAMES.get(code, code.upper())


# Native-script display names — used by the pipeline when building the
# ``sourceLanguage`` block so the FE can render a pill in the user's own
# script (e.g. "עברית" for Hebrew). Mirrors apps/web's NATIVE_LANGUAGE_NAMES.
_NATIVE_NAMES: dict[str, str] = {
    "en": "English",
    "he": "עברית",
    "ar": "العربية",
    "fa": "فارسی",
    "ur": "اردو",
    "yi": "ייִדיש",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
    "ru": "Русский",
    "hi": "हिन्दी",
    "tr": "Türkçe",
    "pl": "Polski",
    "nl": "Nederlands",
    "el": "Ελληνικά",
    "th": "ไทย",
    "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia",
    "uk": "Українська",
    "cs": "Čeština",
    "sv": "Svenska",
    "da": "Dansk",
    "no": "Norsk",
    "fi": "Suomi",
    "ro": "Română",
    "hu": "Magyar",
}


def get_native_name(code: str) -> str:
    """Native-script display name for an ISO 639-1 code.

    Falls back to ``get_language_name`` (English name) for codes without a
    native-script entry, and to the uppercased code for unknown codes.
    """
    return _NATIVE_NAMES.get(code) or get_language_name(code)


SUPPORTED_LANGS: frozenset[str] = frozenset(_LANGUAGE_NAMES.keys())

# Inverse of _LANGUAGE_NAMES: Whisper's API returns language as a name
# ("chinese", "hebrew") rather than an ISO code. Without this map "chinese"
# would be truncated to "ch" — Chamorro, not Chinese — and the pipeline would
# emit garbage downstream.
_NAME_TO_CODE: dict[str, str] = {name.lower(): code for code, name in _LANGUAGE_NAMES.items()}

# ISO 639-2/3 three-letter → ISO 639-1 two-letter. yt-dlp and some legacy
# caption tracks return three-letter codes; the prior implementation truncated
# them to invalid two-letter codes ("jpn" → "jp", "chi" → "ch").
_ALPHA3_TO_ALPHA2: dict[str, str] = {
    "eng": "en",
    "heb": "he",
    "ara": "ar",
    "per": "fa",
    "fas": "fa",
    "urd": "ur",
    "yid": "yi",
    "spa": "es",
    "fre": "fr",
    "fra": "fr",
    "ger": "de",
    "deu": "de",
    "ita": "it",
    "por": "pt",
    "rus": "ru",
    "jpn": "ja",
    "kor": "ko",
    "chi": "zh",
    "zho": "zh",
    "hin": "hi",
    "tur": "tr",
    "pol": "pl",
    "dut": "nl",
    "nld": "nl",
    "swe": "sv",
    "dan": "da",
    "nor": "no",
    "fin": "fi",
    "ell": "el",
    "gre": "el",
    "cze": "cs",
    "ces": "cs",
    "rum": "ro",
    "ron": "ro",
    "hun": "hu",
    "tha": "th",
    "vie": "vi",
    "ind": "id",
    "may": "ms",
    "msa": "ms",
    "ukr": "uk",
    "bul": "bg",
    "hrv": "hr",
    "slo": "sk",
    "slk": "sk",
    "slv": "sl",
    "lit": "lt",
    "lav": "lv",
    "est": "et",
    "tam": "ta",
    "tel": "te",
    "ben": "bn",
    "mal": "ml",
    "mar": "mr",
    "guj": "gu",
    "kan": "kn",
    "swa": "sw",
    "afr": "af",
    "cat": "ca",
    "baq": "eu",
    "eus": "eu",
    "glg": "gl",
    "srp": "sr",
}

# ISO 639-1 codes withdrawn in 1989 that YouTube still serves: Hebrew is
# ``iw`` in yt-dlp's ``language`` field and caption keys (``iw-orig``), so
# without the alias every Hebrew signal normalized to None.
_LEGACY_ALIASES: dict[str, str] = {"iw": "he", "ji": "yi", "in": "id"}


def normalize_language_code(raw: str | None) -> str | None:
    """Normalize anything-language-shaped to a supported ISO 639-1 code.

    Accepts BCP-47 region tags (``en-US`` → ``en``, ``zh-Hans`` → ``zh``),
    YouTube's legacy codes (``iw`` → ``he``, ``ji`` → ``yi``, ``in`` → ``id``),
    ISO 639-2/3 three-letter codes (``chi`` → ``zh``, ``jpn`` → ``ja``),
    and language names returned by the Whisper API (``chinese`` → ``zh``,
    ``hebrew`` → ``he``).

    Returns ``None`` for empty, malformed, or unsupported input — never an
    unsupported 2-letter code (e.g. ``ch`` for Chamorro). Returning None is
    safe: downstream callers gate on language presence and default to English.
    """
    if not raw:
        return None
    s = raw.strip().lower()
    if not s:
        return None

    head = s.split("-")[0].split("_")[0]
    head = _LEGACY_ALIASES.get(head, head)
    if len(head) == 2 and head in SUPPORTED_LANGS:
        return head
    if len(head) == 3 and head in _ALPHA3_TO_ALPHA2:
        return _ALPHA3_TO_ALPHA2[head]
    if s in _NAME_TO_CODE:
        return _NAME_TO_CODE[s]
    return None


def detect_language_from_text(text: str) -> str | None:
    """Detect language from text using langdetect library.

    Returns ISO 639-1 code or None on failure.
    Falls back to None if langdetect is not installed or detection fails.
    """
    if not text or len(text.strip()) < 50:
        return None

    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0  # Deterministic results
        code = detect(text[:5000])
        return normalize_language_code(code)
    except ImportError:
        logger.debug("langdetect not installed, skipping text-based language detection")
        return None
    except Exception as e:
        logger.debug("Language detection failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Character-set heuristic (fast, no external dependency)
# ─────────────────────────────────────────────────────────────────────────────

# Regex patterns for script detection
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_CJK_RE = re.compile(r"[\u4E00-\u9FFF\u3400-\u4DBF]")
_HANGUL_RE = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF]")
_HIRAGANA_KATAKANA_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


def detect_language_by_script(text: str) -> str | None:
    """Fast script-based language detection using Unicode ranges.

    Less accurate than langdetect but zero dependencies and instant.
    Returns ISO 639-1 code or None if cannot determine.
    """
    if not text or len(text) < 20:
        return None

    sample = text[:3000]
    total = len(sample)

    # Japanese kana (hiragana/katakana) MUST be checked before the CJK range:
    # Japanese mixes kanji — which live in the CJK block — with kana, so a kanji
    # ratio over the threshold would otherwise mislabel Japanese as Chinese
    # ("zh"). Chinese never contains kana, so kana-first can't misfire the other
    # way. Order is significant; the first pattern over threshold wins.
    checks: list[tuple[re.Pattern, str]] = [
        (_HEBREW_RE, "he"),
        (_ARABIC_RE, "ar"),
        (_HIRAGANA_KATAKANA_RE, "ja"),
        (_CJK_RE, "zh"),
        (_HANGUL_RE, "ko"),
        (_CYRILLIC_RE, "ru"),
        (_DEVANAGARI_RE, "hi"),
        (_THAI_RE, "th"),
    ]

    for pattern, lang in checks:
        matches = len(pattern.findall(sample))
        if matches / max(total, 1) > 0.1:
            return lang

    return None


# Hebrew (with its presentation forms), Arabic (with the supplement,
# extended-A and both presentation-form blocks; not U+FEFF, the BOM) and CJK
# (Han with extension A and the compatibility ideographs, kana, Hangul
# syllables and jamo). Wider than the detection patterns above, which only
# need the common letters to pass their 10 % share. Escaped, never literal: NFC
# normalization maps U+FB1D and the U+F900 ideographs to other code points.
_HEBREW_ARABIC_CJK_RE = re.compile(
    r"[\u0590-\u05FF\uFB1D-\uFB4F"
    r"\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFC"
    r"\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
    r"\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]"
)


def has_hebrew_arabic_or_cjk(text: str) -> bool:
    """True when ``text`` holds a single Hebrew, Arabic or CJK character.

    One is enough, unlike ``detect_language_by_script``'s share: an English
    translation with one such word left in it is partly untranslated.
    """
    return _HEBREW_ARABIC_CJK_RE.search(text) is not None
