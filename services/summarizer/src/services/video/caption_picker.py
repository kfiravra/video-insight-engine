"""Caption-track choice for the yt-dlp layer: the spoken language first, then manual over auto.

yt-dlp lists every caption track YouTube offers: creator uploads under
``subtitles`` (any language — a translated upload is as "manual" as the
original) and ASR + on-demand machine translations under
``automatic_captions``. Picking manual-first across languages handed English
videos their Arabic or Dutch community subtitles, so the language is chosen
first — the language spoken in the video — and only then manual over auto.

The spoken language is yt-dlp's ``language`` field (gate-2 decision 4, Kfir
2026-10-12) unless the one original ASR track — the ``<lang>-orig`` key yt-dlp
adds only for an ASR track — names another language: then the ASR's language
wins (Kfir, round 3). The tag is the uploader's setting and can be wrong (an
English song tagged Danish, the reason the fallback waterfall below demotes
it); the ASR language is what YouTube processed the audio as, which is right
more often but can carry the same mistake (TNhaISOUy6Q: tagged Polish, Polish
ASR of English speech — then neither signal helps). No field → that ASR's
language; one ASR in an unsupported language → no spoken language.
A dubbed video (audio formats in more than one language) carries one ASR per
dub audio, so its ASR keys — several, or a dub's alone when the original's is
missing — never override the field; without a field, several ``-orig``
languages are no signal at all.

Only untranslated json3 formats count: a machine translation INTO the spoken
language is not a track in that language. When the spoken language is unknown
or has no track, the older language waterfall + preference order picks, as
before 2.4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.utils.language_utils import detect_language_by_script, normalize_language_code

logger = logging.getLogger(__name__)

_ORIG_SUFFIX = "-orig"


@dataclass(frozen=True)
class SubtitleTrack:
    """A json3 caption track picked for the transcript.

    Carries provenance alongside the URL: ``kind`` is the yt-dlp bucket the
    track came from ("manual" for creator uploads, "auto-generated" for ASR)
    and ``lang`` the caption key that matched (e.g. ``"ar-SA"``), so the
    transcript trail can say exactly which track was tried.
    """

    url: str
    kind: str  # "manual" | "auto-generated"
    lang: str


@dataclass(frozen=True)
class CaptionChoice:
    """The pick for one video: the track plus the two language signals.

    ``language`` is what the transcript is in — the spoken language when it
    had a track, else the waterfall's result. ``spoken_language`` is the
    spoken signal alone, whether or not it had a track.
    """

    language: str | None
    spoken_language: str | None
    track: SubtitleTrack | None


def _is_machine_translation(url: str) -> bool:
    """True for YouTube's on-demand machine translation of another track.

    yt-dlp files those under the target language's key, marked only by a
    ``tlang`` query parameter on the URL.
    """
    return "tlang" in parse_qs(urlsplit(url).query)


def json3_url(fmts: list[dict[str, Any]], *, translated: bool) -> str | None:
    """URL of the first json3 variant that is (or is not) a machine translation."""
    for fmt in fmts:
        url = fmt.get("url")
        if fmt.get("ext") == "json3" and url and _is_machine_translation(url) == translated:
            return url
    return None


# ─── Spoken-language rule ───────────────────────────────────────────────────


def _original_asr_languages(auto_captions: dict[str, Any]) -> set[str | None]:
    """Languages of the original ASR tracks; ``None`` stands for an unsupported one."""
    return {
        normalize_language_code(key.removesuffix(_ORIG_SUFFIX))
        for key in auto_captions
        if key.endswith(_ORIG_SUFFIX)
    }


def _is_dubbed(info: dict[str, Any]) -> bool:
    """Audio formats in more than one language: a dubbed (multi-audio) video."""
    languages = {
        normalize_language_code(fmt.get("language")) or fmt.get("language")
        for fmt in info.get("formats") or []
        if fmt.get("acodec") not in (None, "none") and fmt.get("language")
    }
    return len(languages) > 1


def spoken_language(info: dict[str, Any], auto_captions: dict[str, Any]) -> str | None:
    """The language spoken in the video, as a supported ISO 639-1 code, or None.

    The one original ASR track's language when there is exactly one and the
    video is not dubbed — it agrees with yt-dlp's ``language`` field or
    overrides a wrong one; ``None`` when that ASR's language is unsupported.
    Otherwise the field (``en-US`` → ``en``, ``iw`` → ``he``), and without a
    field the one ASR's language (2.4).
    """
    asr = _original_asr_languages(auto_captions)
    tag = normalize_language_code(info.get("language"))
    if len(asr) == 1 and (tag is None or not _is_dubbed(info)):
        return next(iter(asr))
    return tag


def _untranslated_track(language: str, kind: str, captions: dict[str, Any]) -> SubtitleTrack | None:
    """First key whose aliased primary subtag is ``language`` and that has an untranslated json3.

    ``iw-orig`` → he, ``en-US-cvfXDfbeED0`` → en, ``pt-BR`` → pt. One key can
    list formats from several source tracks (a dubbed video's ``en`` key lists
    the ar→en translation first), so the format is chosen, not the key.
    """
    for key, fmts in captions.items():
        if normalize_language_code(key) != language:
            continue
        url = json3_url(fmts, translated=False)
        if url:
            return SubtitleTrack(url=url, kind=kind, lang=key)
    return None


def pick_spoken_track(
    language: str | None,
    manual_captions: dict[str, Any],
    auto_captions: dict[str, Any],
) -> SubtitleTrack | None:
    """Manual track in the spoken language, else its untranslated auto track, else None.

    A manual track in another language is never chosen here — ``None`` sends
    the caller to the fallback.
    """
    if not language:
        return None
    return _untranslated_track(language, "manual", manual_captions) or _untranslated_track(
        language, "auto-generated", auto_captions
    )


# ─── Fallback: language waterfall + preference order (pre-2.4) ──────────────


def resolve_video_language(
    info: dict[str, Any],
    manual_captions: dict[str, Any],
    auto_captions: dict[str, Any],
) -> str | None:
    """Pick the most likely primary language without making any network calls.

    Waterfall, in order of reliability:
      1. Script-detection on title + first 500 chars of description PLUS
         agreement with a manual-captions track. Two weak signals agreeing
         is stronger than either alone — protects against the Arabic-title /
         English-audio false positive (a creator with an English video and a
         short Arabic title would otherwise be routed as Arabic).
      2. First MANUAL-captions key — creator-uploaded tracks are almost always
         the original audio language.
      3. Script-detection alone — catches Arabic/Hebrew/CJK/etc. titles when
         YouTube ships no manual captions. Demoted below manual captions
         because a 500-char description sample can produce 10% script
         coverage from a few accent characters and trip the heuristic.
      4. English in AUTO-captions — for English-audio videos with no manual
         track. Falls between the heuristics and the YouTube tag because
         YouTube's tag is unreliable: it falsely labelled Bruno Mars's
         "The Lazy Song" as Danish.
      5. yt-dlp's audio-language tag (``info['language']``) — useful but
         demoted because of the false-positive rate.
      6. First auto-captions key — last-resort.

    Returns an ISO 639-1 code (e.g. ``"ar"``) or ``None`` when no signal is
    available. Downstream treats ``None`` as English.
    """
    title = info.get("title", "") or ""
    description = (info.get("description") or "")[:500]
    script_lang = detect_language_by_script(f"{title} {description}")

    manual_lang: str | None = None
    for key in manual_captions.keys():
        cand = normalize_language_code(key)
        if cand:
            manual_lang = cand
            break

    # Two-signal agreement beats either alone. If script-detection and the
    # first manual-captions track both point to the same language, ship it.
    if script_lang and manual_lang and script_lang == manual_lang:
        return script_lang

    # Manual captions on their own are still a strong signal — creator-
    # uploaded tracks are the original audio language in the vast majority
    # of cases.
    if manual_lang:
        return manual_lang

    # Script-detection alone — last chance before falling back to the less
    # reliable signals.
    if script_lang:
        return script_lang

    for key in auto_captions.keys():
        if normalize_language_code(key) == "en":
            return "en"

    lang = normalize_language_code(info.get("language"))
    if lang:
        return lang

    for key in auto_captions.keys():
        cand = normalize_language_code(key)
        if cand:
            return cand

    return None


def _key_matches(key: str, lang_code: str) -> bool:
    """``ar`` matches ``ar`` / ``ar-SA``, and an aliased primary subtag matches too.

    The raw prefix keeps the ``en-US`` / ``en-GB`` preference entries working;
    the alias lets a waterfall ``he`` (from a manual ``iw`` key or a Hebrew
    title) find the ``iw`` / ``iw-orig`` keys YouTube actually lists.
    """
    return (
        key == lang_code
        or key.startswith(lang_code + "-")
        or normalize_language_code(key) == lang_code
    )


def _find_track(
    lang_code: str,
    sources: tuple[tuple[str, dict[str, Any]], ...],
    *,
    translated: bool,
) -> SubtitleTrack | None:
    """First json3 track keyed ``lang_code``, a regional variant (``ar-SA``) or an alias."""
    for kind, captions in sources:
        for key, fmts in captions.items():
            if not _key_matches(key, lang_code):
                continue
            url = json3_url(fmts, translated=translated)
            if url:
                return SubtitleTrack(url=url, kind=kind, lang=key)
    return None


def _pick_subtitle_url(
    detected_language: str | None,
    manual_captions: dict[str, Any],
    auto_captions: dict[str, Any],
) -> SubtitleTrack | None:
    """Pick a json3 subtitle track preferring the detected language.

    Order: detected language, then English. Within a language an original
    track (manual, then auto) beats a machine translation into it, which is
    only the last resort. YouTube auto-translates every foreign video to
    English on demand; without the language preference the pipeline would grab
    the auto-EN track and pretend the audio was English. Without the
    original-first rule an auto-dubbed video (one ASR track per dub) would get
    whichever translation yt-dlp lists first under ``en`` (ar→en, seen in prod)
    instead of the English ASR track listed after it.

    The returned track records which bucket it came from and the exact caption
    key that matched (``"ar-SA"`` for detected ``"ar"``), so a later fallback
    can be attributed to the specific track that failed.
    """
    pref_langs: list[str] = []
    if detected_language and detected_language != "en":
        pref_langs.append(detected_language)
    pref_langs += ["en", "en-US", "en-GB"]
    pref_langs = list(dict.fromkeys(pref_langs))

    sources = (("manual", manual_captions), ("auto-generated", auto_captions))
    for lang_code in pref_langs:
        for translated in (False, True):
            track = _find_track(lang_code, sources, translated=translated)
            if track:
                return track
    return None


# ─── Entry point ────────────────────────────────────────────────────────────


def choose_caption_track(video_id: str, info: dict[str, Any]) -> CaptionChoice:
    """The caption track (and languages) for one yt-dlp info dict.

    The spoken-language rule decides first; only when it has no track do the
    waterfall + preference order run, unchanged. Either way the language is
    resolved *before* the pick: yt-dlp offers English auto-translations for
    every foreign-language video, and picking one would silently mislabel the
    audio as English.
    """
    auto_captions = info.get("automatic_captions") or {}
    manual_captions = info.get("subtitles") or {}
    spoken = spoken_language(info, auto_captions)
    track = pick_spoken_track(spoken, manual_captions, auto_captions)
    rule = "spoken"
    language = spoken
    if track is None:
        rule = "fallback"
        language = resolve_video_language(info, manual_captions, auto_captions)
        track = _pick_subtitle_url(language, manual_captions, auto_captions)
    logger.info(
        "Video %s: caption pick %s by the %s rule (spoken=%s, tag=%s, language=%s)",
        video_id,
        f"{track.kind}/{track.lang}" if track else "none",
        rule,
        spoken or "unknown",
        normalize_language_code(info.get("language")) or "none",
        language or "unknown",
    )
    return CaptionChoice(language=language, spoken_language=spoken, track=track)
