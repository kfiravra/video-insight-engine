"""YouTube video data extraction using yt-dlp.

This module provides a single-call extraction of all video data:
- Metadata (title, channel, duration, thumbnail)
- Chapters (creator-defined timestamps)
- Description (full text)
- Subtitles/captions with timestamps
- Video context (category, tags)

Category detection uses weighted scoring:
- Keywords (tags + hashtags): 40%
- YouTube category: 30%
- Title patterns: 15%
- Channel patterns: 15%
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlsplit

import requests
import tenacity
import yt_dlp  # type: ignore[import-untyped]

from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.media.download_utils import (
    is_exit_blocked,
    try_proxy_exits,
    ytdlp_proxy_exit_urls,
    ytdlp_proxy_url,
)
from src.utils.language_utils import (
    detect_language_by_script,
    normalize_language_code,
)

logger = logging.getLogger(__name__)

# Path to detection rules
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Valid category values (matches frontend VideoCategory)
VALID_CATEGORIES: frozenset[str] = frozenset(
    [
        "cooking",
        "coding",
        "fitness",
        "travel",
        "education",
        "podcast",
        "reviews",
        "gaming",
        "diy",
        "music",
        "standard",
    ]
)


# -----------------------------------------------------------------------------
# Video Context Extraction
# -----------------------------------------------------------------------------
class CategoryKeywords(TypedDict):
    """Keywords config for category detection."""

    primary: list[str]
    secondary: list[str]


class YouTubeCategories(TypedDict):
    """YouTube categories config for category detection."""

    primary: list[str]
    secondary: list[str]


class CategoryConfig(TypedDict):
    """Configuration for a single category detection rule."""

    keywords: CategoryKeywords
    youtube_categories: YouTubeCategories
    channel_patterns: list[str]
    title_patterns: list[str]


class DetectionConfig(TypedDict):
    """Detection configuration."""

    llm_fallback_threshold: float
    weights: dict[str, float]


class CategoryRules(TypedDict):
    """Structure of category_rules.json."""

    version: str
    detection_config: DetectionConfig
    categories: dict[str, CategoryConfig]
    default_category: str


@lru_cache(maxsize=1)
def _load_category_rules() -> CategoryRules:
    """Load category detection rules from JSON file.

    Returns:
        Dict with 'categories' containing weighted scoring rules,
        'detection_config' for thresholds and weights,
        and 'default_category' for fallback.

    Raises:
        ValueError: If required keys are missing from the config file.

    Note:
        Results are cached to avoid repeated disk reads.
    """
    path = PROMPTS_DIR / "detection" / "category_rules.json"
    data = json.loads(path.read_text())

    # Schema validation - check required top-level keys
    required_keys = {"categories", "default_category", "detection_config"}
    missing_keys = required_keys - set(data.keys())
    if missing_keys:
        raise ValueError(f"Invalid category_rules.json: missing required keys {missing_keys}")

    # Validate detection_config structure
    detection_config = data.get("detection_config", {})
    if "weights" not in detection_config:
        raise ValueError("Invalid category_rules.json: detection_config.weights is required")

    weights = detection_config.get("weights", {})
    required_weights = {"keywords", "youtube_category", "title", "channel"}
    missing_weights = required_weights - set(weights.keys())
    if missing_weights:
        raise ValueError(f"Invalid category_rules.json: missing weights {missing_weights}")

    return data


@dataclass
class VideoContext:
    """Context information extracted from video metadata.

    Attributes:
        youtube_category: Raw YouTube category (e.g., "Science & Technology")
        category: Detected content category (e.g., "cooking", "coding", "standard")
        tags: Raw tags from video metadata
        display_tags: Cleaned, deduplicated tags for UI display (max 6)
    """

    youtube_category: str | None
    category: str  # "cooking", "coding", "travel", etc.
    tags: list[str]
    display_tags: list[str]


def _extract_hashtags(description: str) -> list[str]:
    """Extract hashtags from video description.

    Args:
        description: The video description text

    Returns:
        List of hashtags (without the # symbol), lowercased
    """
    if not description:
        return []
    return re.findall(r"#(\w+)", description.lower())


def _detect_category(
    youtube_category: str | None,
    tags: list[str],
    hashtags: list[str],
    channel: str | None = None,
    title: str | None = None,
) -> tuple[str, float]:
    """Detect video category using weighted scoring.

    Scoring weights (from category_rules.json):
    - Keywords (tags + hashtags): 40%
    - YouTube category: 30%
    - Title patterns: 15%
    - Channel patterns: 15%

    Args:
        youtube_category: YouTube category name (e.g., "Entertainment")
        tags: Video tags from metadata
        hashtags: Hashtags extracted from description
        channel: Channel name (optional, for pattern matching)
        title: Video title (optional, for pattern matching)

    Returns:
        Tuple of (category, confidence_score)
        - category: detected category ('cooking', 'coding', 'standard', etc.)
        - confidence: 0.0 to 1.0
    """
    rules = _load_category_rules()
    weights = rules.get("detection_config", {}).get("weights", {})

    # Normalize weights
    keyword_weight = weights.get("keywords", 0.40)
    yt_category_weight = weights.get("youtube_category", 0.30)
    title_weight = weights.get("title", 0.15)
    channel_weight = weights.get("channel", 0.15)

    # Combine tags and hashtags for keyword matching
    all_terms = set(t.lower() for t in tags) | set(hashtags)
    title_lower = (title or "").lower()
    channel_lower = (channel or "").lower()

    # Score each category
    category_scores: dict[str, float] = {}

    for cat_name, config in rules.get("categories", {}).items():
        score = 0.0

        # 1. Keyword scoring (weight: 0.40)
        keywords = config.get("keywords", {})
        primary_keywords = set(k.lower() for k in keywords.get("primary", []))
        secondary_keywords = set(k.lower() for k in keywords.get("secondary", []))

        primary_matches = len(all_terms & primary_keywords)
        secondary_matches = len(all_terms & secondary_keywords)

        if primary_keywords or secondary_keywords:
            max_possible = len(primary_keywords) + len(secondary_keywords) * 0.5
            keyword_score = (
                (primary_matches + secondary_matches * 0.5) / max_possible
                if max_possible > 0
                else 0
            )
            score += min(keyword_score, 1.0) * keyword_weight

        # 2. YouTube category scoring (weight: 0.30)
        if youtube_category:
            yt_cats = config.get("youtube_categories", {})
            primary_cats = yt_cats.get("primary", [])
            secondary_cats = yt_cats.get("secondary", [])

            if youtube_category in primary_cats:
                score += yt_category_weight
            elif youtube_category in secondary_cats:
                score += yt_category_weight * 0.5

        # 3. Title pattern matching (weight: 0.15)
        # Limit title length to prevent ReDoS attacks
        title_patterns = config.get("title_patterns", [])
        title_safe = title_lower[:500] if title_lower else ""
        if title_safe and title_patterns:
            for pattern in title_patterns:
                try:
                    if re.search(pattern, title_safe):
                        score += title_weight
                        break
                except re.error:
                    logger.warning("Invalid regex pattern in category rules: %s", pattern)
                    continue

        # 4. Channel pattern matching (weight: 0.15)
        channel_patterns = config.get("channel_patterns", [])
        if channel_lower and channel_patterns:
            for pattern in channel_patterns:
                if pattern.lower() in channel_lower:
                    score += channel_weight
                    break

        category_scores[cat_name] = score

    # Find best category
    if not category_scores:
        return rules.get("default_category", "standard"), 0.0

    best_category = max(category_scores, key=category_scores.get)  # type: ignore[arg-type]
    best_score = category_scores[best_category]

    # If score is too low, return standard
    if best_score < 0.1:
        return rules.get("default_category", "standard"), best_score

    return best_category, best_score


def _build_display_tags(
    tags: list[str],
    hashtags: list[str],
    max_tags: int = 6,
    min_length: int = 3,
) -> list[str]:
    """Build cleaned, deduplicated display tags for UI.

    Merges video tags and hashtags, removes duplicates, filters by length,
    and limits to a reasonable number for display.

    Args:
        tags: Video tags from metadata
        hashtags: Hashtags extracted from description
        max_tags: Maximum number of tags to return (default 6)
        min_length: Minimum character length for tags (default 3)

    Returns:
        List of cleaned display tags, limited to max_tags
    """
    # Normalize and deduplicate
    seen: set[str] = set()
    display_tags: list[str] = []

    # Process tags first (they're usually more relevant)
    for tag in tags:
        normalized = tag.lower().strip()
        if len(normalized) >= min_length and normalized not in seen:
            seen.add(normalized)
            # Keep original casing for display
            display_tags.append(tag.strip())

    # Then add hashtags that aren't duplicates
    for hashtag in hashtags:
        normalized = hashtag.lower().strip()
        if len(normalized) >= min_length and normalized not in seen:
            seen.add(normalized)
            # Capitalize first letter for display consistency
            display_tags.append(hashtag.capitalize())

    return display_tags[:max_tags]


def extract_video_context(
    info: dict[str, Any],
    description: str,
    channel: str | None = None,
    title: str | None = None,
) -> VideoContext:
    """Extract video context from yt-dlp info dict.

    Uses weighted scoring to detect category independently from persona.
    Category detection is more lenient (OR-like) while persona selection
    is a simple mapping.

    Args:
        info: The yt-dlp info dictionary
        description: Video description text (for hashtag extraction)
        channel: Channel name (optional, improves detection accuracy)
        title: Video title (optional, improves detection accuracy)

    Returns:
        VideoContext with category, persona, and tags
    """
    # Extract category (yt-dlp returns categories as a list)
    categories = info.get("categories", [])
    youtube_category = categories[0] if categories else None

    # Extract tags
    tags = info.get("tags", []) or []

    # Extract hashtags from description
    hashtags = _extract_hashtags(description)

    # Get channel and title from info if not provided
    if channel is None:
        channel = info.get("uploader") or info.get("channel")
    if title is None:
        title = info.get("title")

    # Detect category using weighted scoring (NEW)
    category, confidence = _detect_category(
        youtube_category=youtube_category,
        tags=tags,
        hashtags=hashtags,
        channel=channel,
        title=title,
    )

    # Build display tags
    display_tags = _build_display_tags(tags, hashtags)

    logger.info(
        "Video context: category=%s (confidence=%.2f), youtube_category=%s, tags=%d, hashtags=%d",
        category,
        confidence,
        youtube_category,
        len(tags),
        len(hashtags),
    )

    return VideoContext(
        youtube_category=youtube_category,
        category=category,
        tags=tags,
        display_tags=display_tags,
    )


# -----------------------------------------------------------------------------
# Video Data Classes
# -----------------------------------------------------------------------------


@dataclass
class Chapter:
    """A chapter/section from the video."""

    start_time: float  # seconds
    end_time: float  # seconds
    title: str


@dataclass
class SubtitleSegment:
    """A single subtitle/caption segment."""

    text: str
    start: float  # seconds
    duration: float  # seconds


@dataclass(frozen=True)
class SubtitleTrack:
    """A json3 caption track chosen by ``_pick_subtitle_url``.

    Carries provenance alongside the URL: ``kind`` is the yt-dlp bucket the
    track came from ("manual" for creator uploads, "auto-generated" for ASR)
    and ``lang`` the caption key that matched (e.g. ``"ar-SA"``), so the
    transcript trail can say exactly which track was tried.
    """

    url: str
    kind: str  # "manual" | "auto-generated"
    lang: str


@dataclass
class VideoData:
    """Complete video data extracted from yt-dlp.

    Attributes:
        video_id: YouTube video ID
        title: Video title
        channel: Channel/uploader name
        duration: Video duration in seconds
        thumbnail_url: URL to video thumbnail
        description: Full video description text
        chapters: Creator-defined chapters with timestamps
        subtitles: Subtitle segments with timestamps
        upload_date: Upload date in YYYYMMDD format
        context: Video context with category, persona, and tags
        language: Detected primary language (ISO 639-1) or None
        captions_rate_limited: True when the timedtext fetch was HTTP 429
        caption_track: "manual" | "auto-generated" | None (no track picked)
        caption_lang: Caption key of the picked track (e.g. "ar-SA") or None
        caption_fetch_error: "http_<n>" | "request" | "parse" | "empty" | None
    """

    video_id: str
    title: str
    channel: str
    duration: int  # exact seconds
    thumbnail_url: str | None
    description: str
    chapters: list[Chapter] = field(default_factory=list)
    subtitles: list[SubtitleSegment] = field(default_factory=list)
    upload_date: str | None = None  # YYYYMMDD format
    context: VideoContext | None = None  # Phase 1: Video context extraction
    # Detected primary language (ISO 639-1) — resolved from yt-dlp's audio-lang
    # tag, title/description script, or available caption track keys. ``None``
    # means "no signal" and downstream falls back to English.
    language: str | None = None
    # True when the timedtext subtitle fetch was HTTP-429 rate-limited. The
    # transcript fetcher uses this to negative-cache the caption endpoints
    # (further caption calls in the window are doomed for the whole IP).
    captions_rate_limited: bool = False
    # Which caption track was picked and how its fetch went. Kept apart from
    # ``subtitles`` because an empty list is ambiguous — "no track offered",
    # "track offered but the fetch failed" and "track fetched but held no
    # speech" need different fallbacks and different transcriptMeta.
    caption_track: str | None = None  # "manual" | "auto-generated"
    caption_lang: str | None = None  # matched caption key, e.g. "ar-SA"
    caption_fetch_error: str | None = None  # "http_<n>" | "request" | "parse" | "empty"
    # json3 URL of the picked track while its fetch is deferred
    # (``extract_video_data(with_captions=False)`` → ``fetch_video_captions``).
    caption_url: str | None = None

    @property
    def has_chapters(self) -> bool:
        """Check if video has creator-defined chapters."""
        return len(self.chapters) > 0

    @property
    def transcript_text(self) -> str:
        """Get full transcript as plain text."""
        return " ".join(seg.text for seg in self.subtitles)

    def get_chapter_transcript(self, chapter_index: int) -> str:
        """Get transcript text for a specific chapter."""
        if chapter_index < 0 or chapter_index >= len(self.chapters):
            return ""

        chapter = self.chapters[chapter_index]
        segments = [
            seg for seg in self.subtitles if chapter.start_time <= seg.start < chapter.end_time
        ]
        return " ".join(seg.text for seg in segments)


def _build_yt_dlp_opts() -> dict[str, Any]:
    """Build yt-dlp metadata/subtitle options; YOUTUBE_PROXY_URL applies when set."""
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        # Subtitle options — request common languages (not 'all', which downloads 50+ tracks).
        # The pipeline detects language from the subtitle content.
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [
            "en",
            "en-US",
            "en-GB",
            "he",
            "ar",
            "fa",
            "ur",  # RTL
            "es",
            "fr",
            "de",
            "it",
            "pt",  # Western European
            "ru",
            "uk",
            "pl",
            "cs",  # Slavic
            "ja",
            "ko",
            "zh",
            "zh-Hans",
            "zh-Hant",  # East Asian
            "hi",
            "bn",
            "ta",
            "te",  # South Asian
            "tr",
            "th",
            "vi",
            "id",  # Other major
        ],
        "subtitlesformat": "json3",  # Best format for parsing
    }

    proxy_url = ytdlp_proxy_url()
    if proxy_url:
        opts["proxy"] = proxy_url

    return opts


def _parse_chapters(info: dict[str, Any]) -> list[Chapter]:
    """Parse chapters from yt-dlp info dict."""
    chapters = []
    raw_chapters = info.get("chapters") or []

    for ch in raw_chapters:
        start = ch.get("start_time", 0)
        end = ch.get("end_time", start)
        title = ch.get("title", "Untitled")

        chapters.append(
            Chapter(
                start_time=float(start),
                end_time=float(end),
                title=title.strip(),
            )
        )

    return chapters


_SUBTITLE_MAX_BYTES = 10 * 1024 * 1024


def _get_subtitle_json_sync(url: str, proxy_url: str | None) -> dict:
    """One timedtext GET through one exit (None = direct), size-capped.

    Raises requests' HTTPError on a non-2xx status and ValueError on an
    oversized or undecodable body.
    """
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
        stream=True,
        proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
    )
    response.raise_for_status()
    # Read with size limit to prevent memory exhaustion from oversized responses
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > _SUBTITLE_MAX_BYTES:
            response.close()
            raise ValueError(f"Subtitle response exceeds {_SUBTITLE_MAX_BYTES} bytes limit")
        chunks.append(chunk)
    return json.loads(b"".join(chunks))


@tenacity.retry(
    stop=tenacity.stop_after_attempt(2),
    wait=tenacity.wait_fixed(2),
    retry=tenacity.retry_if_exception_type(requests.exceptions.HTTPError),
    before_sleep=lambda retry_state: logger.warning(
        "Subtitle fetch retry %d after error: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    ),
)
def _fetch_subtitle_single_exit_sync(url: str) -> dict:
    """Direct or single-exit fetch: one same-IP retry after 2 s on an HTTP error."""
    return _get_subtitle_json_sync(url, ytdlp_proxy_url())


def _http_status_of(exc: BaseException | None) -> int | None:
    """HTTP status carried by a requests HTTPError, unwrapping tenacity's RetryError.

    ``None`` when the exception is not an HTTP error or has no response attached.
    """
    if isinstance(exc, tenacity.RetryError):
        exc = exc.last_attempt.exception()
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def _is_timedtext_exit_blocked(exc: Exception) -> bool:
    """True for an IP-scoped timedtext failure worth another exit: a 429 or the bot check."""
    return _http_status_of(exc) == 429 or is_exit_blocked(exc)


def _fetch_subtitle_data_sync(url: str) -> dict:
    """Fetch a timedtext json3 body through YOUTUBE_PROXY_URL's exits.

    SYNC — must be called from asyncio.to_thread (via _extract_video_data_sync).

    Never direct when a proxy is set — that would hit timedtext from the host
    IP the proxy exists to hide. With several sticky exits
    (YOUTUBE_PROXY_EXIT_COUNT) a 429 or bot check moves on to the next exit at
    once — the URL is not bound to the exit that produced it
    (``ip=0.0.0.0``) — so a 429 reaches the caller only when every tried exit
    was blocked. Direct connections and a single exit keep the same-IP retry.
    """
    exit_urls = ytdlp_proxy_exit_urls()
    if len(exit_urls) > 1:
        return try_proxy_exits(
            exit_urls,
            partial(_get_subtitle_json_sync, url),
            _is_timedtext_exit_blocked,
            "Timedtext fetch",
        )
    return _fetch_subtitle_single_exit_sync(url)


def _parse_json3_events(data: dict) -> list[SubtitleSegment]:
    """Turn a timedtext json3 body into segments, skipping non-speech events."""
    segments: list[SubtitleSegment] = []
    for event in data.get("events", []):
        if "segs" not in event:
            continue
        text = "".join(seg["utf8"] for seg in event.get("segs", []) if "utf8" in seg).strip()
        if text and text != "\n":
            segments.append(
                SubtitleSegment(
                    text=text,
                    start=event.get("tStartMs", 0) / 1000.0,
                    duration=event.get("dDurationMs", 0) / 1000.0,
                )
            )
    return segments


def _fetch_subtitles_from_url_sync(url: str) -> tuple[list[SubtitleSegment], str | None]:
    """Fetch and parse subtitles from a URL (json3 format).

    SYNC — must be called from asyncio.to_thread (via _extract_video_data_sync).

    Returns:
        (segments, error_code) — ``error_code`` is classified here, at the
        except site, so callers never re-inspect exception types:
        ``"http_<status>"`` for an HTTP failure (tenacity wrapper unwrapped),
        ``"request"`` for any other transport failure, ``"parse"`` when the
        body could not be decoded, ``None`` when the fetch itself succeeded.
        Zero segments with no error is NOT an error here — the caller knows
        whether a track was actually offered and labels that ``"empty"``.
    """
    try:
        data = _fetch_subtitle_data_sync(url)
    except (requests.exceptions.HTTPError, tenacity.RetryError) as e:
        status = _http_status_of(e)
        code = f"http_{status}" if status is not None else "request"
        logger.warning("Subtitle fetch HTTP error (%s): %s", code, e)
        return [], code
    except ValueError as e:
        # json.JSONDecodeError is a ValueError; the size guard in
        # _get_subtitle_json_sync raises a plain one.
        logger.warning("Subtitle fetch parse error: %s", e)
        return [], "parse"
    except Exception as e:
        logger.warning("Subtitle fetch error (request): %s", e)
        return [], "request"

    if not data:
        return [], None

    try:
        return _parse_json3_events(data), None
    except Exception as e:
        logger.warning("Failed to parse subtitles: %s", e)
        return [], "parse"


def _clean_subtitle_text(text: str) -> str:
    """Clean subtitle text by removing artifacts."""
    # Remove common artifacts
    text = re.sub(r"\[Music\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[Applause\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[Laughter\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"♪.*?♪", "", text)  # Music notes

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# Issue #15: Retry logic for transient yt-dlp failures
@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
    retry=tenacity.retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    before_sleep=lambda retry_state: logger.warning(
        "yt-dlp extraction retry %d after error: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    ),
)
def _extract_with_retry(url: str, opts: dict[str, Any]) -> dict[str, Any] | None:
    """Extract video info with retry logic for transient failures."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _extract_info_via_exits(video_id: str) -> dict[str, Any] | None:
    """``extract_info`` through YOUTUBE_PROXY_URL's exits.

    YouTube's bot check and its 429 are scoped to the exit IP, so with several
    sticky exits (YOUTUBE_PROXY_EXIT_COUNT) either moves on to the next one;
    private / removed / age-gated videos fail on the first exit as before.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = _build_yt_dlp_opts()
    exit_urls = ytdlp_proxy_exit_urls()
    if len(exit_urls) <= 1:
        return _extract_with_retry(url, opts)
    return try_proxy_exits(
        exit_urls,
        lambda proxy_url: _extract_with_retry(url, {**opts, "proxy": proxy_url}),
        is_exit_blocked,
        f"Metadata extract for {video_id}",
    )


def _extract_video_info_sync(video_id: str) -> tuple[VideoData, SubtitleTrack | None]:
    """One yt-dlp ``extract_info`` → metadata + the picked caption track (not yet fetched).

    Raises:
        TranscriptError: If video is unavailable, live, or extraction fails
    """
    try:
        info = _extract_info_via_exits(video_id)
    except Exception as e:
        raise TranscriptError(
            f"Failed to extract video information: {e}",
            ErrorCode.VIDEO_UNAVAILABLE,
        ) from e

    if not info:
        raise TranscriptError(
            "Failed to extract video information",
            ErrorCode.VIDEO_UNAVAILABLE,
        )

    # Check for live streams
    if info.get("is_live"):
        raise TranscriptError("Live streams are not supported", ErrorCode.LIVE_STREAM)

    description = info.get("description") or ""
    chapters = _parse_chapters(info)
    logger.info("Video %s: found %d chapters", video_id, len(chapters))

    # Resolve the video's primary language *before* picking subtitles. yt-dlp
    # also offers English auto-translations for every foreign-language video;
    # picking those would silently mislabel the audio as English.
    auto_captions = info.get("automatic_captions", {})
    manual_captions = info.get("subtitles", {})
    detected_language = resolve_video_language(info, manual_captions, auto_captions)
    track = _pick_subtitle_url(detected_language, manual_captions, auto_captions)

    context = extract_video_context(info, description)
    logger.info(
        "Video %s: category=%s, tags=%d", video_id, context.category, len(context.display_tags)
    )

    video_data = VideoData(
        video_id=video_id,
        title=info.get("title", "Unknown Title"),
        channel=info.get("uploader") or info.get("channel") or "Unknown Channel",
        duration=int(info.get("duration") or 0),
        thumbnail_url=_best_thumbnail(info, video_id),
        description=description,
        chapters=chapters,
        upload_date=info.get("upload_date"),
        context=context,
        language=detected_language,
        caption_track=track.kind if track else None,
        caption_lang=track.lang if track else None,
        caption_url=track.url if track else None,
    )
    return video_data, track


def _best_thumbnail(info: dict[str, Any], video_id: str) -> str:
    """Highest-quality thumbnail yt-dlp listed, else the standard YouTube URL."""
    for thumb in reversed(info.get("thumbnails", [])):  # Usually sorted by quality
        if thumb.get("url"):
            return thumb["url"]
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"


def _apply_captions(video_data: VideoData, track: SubtitleTrack | None) -> None:
    """Fetch the picked track and store segments + fetch outcome on ``video_data``."""
    subtitles, caption_fetch_error = _fetch_picked_track(
        video_data.video_id, track, video_data.language
    )
    video_data.subtitles = subtitles
    video_data.captions_rate_limited = caption_fetch_error == "http_429"
    video_data.caption_fetch_error = caption_fetch_error
    video_data.caption_url = None


def _extract_video_data_sync(video_id: str) -> VideoData:
    """
    Extract video data using yt-dlp (synchronous).

    This function extracts all available video metadata in a single call:
    - Title, channel, duration, thumbnail
    - Creator-defined chapters
    - Full description text
    - Subtitles with timestamps
    - Video context (category, tags)

    Args:
        video_id: YouTube video ID

    Returns:
        VideoData with all extracted information

    Raises:
        TranscriptError: If video is unavailable or extraction fails
    """
    video_data, track = _extract_video_info_sync(video_id)
    _apply_captions(video_data, track)
    return video_data


def _fetch_picked_track(
    video_id: str,
    track: SubtitleTrack | None,
    detected_language: str | None,
) -> tuple[list[SubtitleSegment], str | None]:
    """Fetch and clean the picked caption track.

    Returns ``(segments, caption_fetch_error)``. A track that was offered but
    came back with zero segments and no transport/parse error is labelled
    ``"empty"`` here rather than in the fetch helper — only this layer knows
    whether a track existed at all.
    """
    if track is None:
        logger.warning("Video %s: no subtitles URL found", video_id)
        return [], None

    subtitles, error = _fetch_subtitles_from_url_sync(track.url)
    for seg in subtitles:
        seg.text = _clean_subtitle_text(seg.text)
    if error is None and not subtitles:
        error = "empty"
    logger.info(
        "Video %s: extracted %d subtitle segments (lang=%s, track=%s/%s, error=%s)",
        video_id,
        len(subtitles),
        detected_language or "unknown",
        track.kind,
        track.lang,
        error,
    )
    return subtitles, error


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


def _is_machine_translation(url: str) -> bool:
    """True for YouTube's on-demand machine translation of another track.

    yt-dlp files those under the target language's key, marked only by a
    ``tlang`` query parameter on the URL.
    """
    return "tlang" in parse_qs(urlsplit(url).query)


def _json3_url(fmts: list[dict[str, Any]], *, translated: bool) -> str | None:
    """URL of the first json3 variant that is (or is not) a machine translation."""
    for fmt in fmts:
        url = fmt.get("url")
        if fmt.get("ext") == "json3" and url and _is_machine_translation(url) == translated:
            return url
    return None


def _find_track(
    lang_code: str,
    sources: tuple[tuple[str, dict[str, Any]], ...],
    *,
    translated: bool,
) -> SubtitleTrack | None:
    """First json3 track keyed ``lang_code`` or a regional variant (``ar-SA``)."""
    for kind, captions in sources:
        for key, fmts in captions.items():
            if key != lang_code and not key.startswith(lang_code + "-"):
                continue
            url = _json3_url(fmts, translated=translated)
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


async def extract_video_data(video_id: str, *, with_captions: bool = True) -> VideoData:
    """
    Extract video data using yt-dlp (async wrapper).

    Runs the blocking yt-dlp call in a thread pool to avoid
    blocking the event loop.

    This function extracts all available video metadata in a single call:
    - Title, channel, duration (exact seconds), thumbnail
    - Creator-defined chapters (timestamps + titles)
    - Full description text
    - Subtitles/captions with timestamps
    - Video context (category, tags)

    ``with_captions=False`` returns right after ``extract_info``: the picked
    track rides on ``caption_url`` and ``fetch_video_captions`` fetches it
    later, so the caption fetch (and its 429 retries) stays out of the
    metadata wall.

    Args:
        video_id: YouTube video ID
        with_captions: Fetch the picked caption track before returning

    Returns:
        VideoData with all extracted information

    Raises:
        TranscriptError: If video is unavailable or extraction fails

    Example:
        video_data = await extract_video_data("dQw4w9WgXcQ")
        # video_data.title -> "Rick Astley - Never Gonna Give You Up"
        # video_data.duration -> 212 (seconds)
        # video_data.has_chapters -> True
        # video_data.chapters[0].start_time -> 0
        # video_data.chapters[0].title -> "Intro"
    """
    if with_captions:
        return await asyncio.to_thread(_extract_video_data_sync, video_id)
    video_data, _track = await asyncio.to_thread(_extract_video_info_sync, video_id)
    return video_data


async def fetch_video_captions(video_data: VideoData) -> None:
    """Fetch the track ``extract_video_data(with_captions=False)`` picked, in place."""
    track = None
    if video_data.caption_url and video_data.caption_track and video_data.caption_lang:
        track = SubtitleTrack(
            url=video_data.caption_url, kind=video_data.caption_track, lang=video_data.caption_lang
        )
    await asyncio.to_thread(_apply_captions, video_data, track)
