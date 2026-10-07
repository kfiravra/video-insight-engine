"""Gemini Flash audio transcription for videos without captions.

Faster and cheaper alternative to Whisper. Used when:
1. No captions exist on YouTube
2. GEMINI_API_KEY is configured
3. Falls through to Whisper on failure

Cost: ~$0.04 per 26-min video (vs ~$0.16 for Whisper)
Speed: 30-90 seconds (vs 5-15 min for Whisper)
"""

import asyncio
import json
import logging
import tempfile
import time
import uuid
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import (
    ErrorCode,
    NormalizedTranscript,
    TranscriptSegment,
)
from src.services.media.download_utils import download_youtube_audio
from src.services.pipeline.pipeline_timing import record_download
from src.services.transcription.usage import emit_transcription_usage
from src.utils.language_utils import (
    detect_language_by_script,
    detect_language_from_text,
)

logger = logging.getLogger(__name__)

TEMP_DIR = Path(tempfile.gettempdir()) / "vie-gemini"

# Default Gemini model for audio transcription.
# This is independent of the generic LLM_FAST_MODEL setting because
# Gemini transcription uses the Gemini File API which only works with
# Gemini models — not OpenAI, Anthropic, etc.
GEMINI_TRANSCRIPTION_MODEL = "gemini-2.5-flash-lite"

# Dedicated token limit for transcription output.
# Transcription needs far more tokens than summarization (LLM_MAX_TOKENS=4096).
# A 173-min video needs ~5,000-10,000+ tokens for its transcript JSON.
# 65536 is the max output for gemini-2.5-flash-lite.
GEMINI_TRANSCRIPTION_MAX_TOKENS = 65536

# MIME type mapping for common audio formats from yt-dlp
MIME_TYPES: dict[str, str] = {
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}

TRANSCRIPTION_PROMPT = """\
Transcribe this audio accurately. Return a JSON array of segments.
Each segment should be 20-40 seconds of speech with format:
{"text": "segment text", "startMs": 0, "endMs": 30000}

Rules:
- Include ALL spoken words, do not skip or summarize
- Transcribe VERBATIM in the original spoken language — do NOT translate to
  English (a non-English video must stay in its own language and script)
- Timestamps must be in milliseconds
- Segments should not overlap
- Clean up filler words (um, uh) but keep the content accurate
- If you hear music or silence, skip those sections

Return ONLY the JSON array, no markdown formatting or explanation.
Example: [{"text": "Hello everyone...", "startMs": 0, "endMs": 25000}]"""

MUSIC_TRANSCRIPTION_PROMPT = """\
Transcribe this audio accurately, focusing on ALL lyrics and singing.
Return a JSON array of segments.
Each segment should be 20-40 seconds with format:
{"text": "segment text", "startMs": 0, "endMs": 30000}

Rules:
- Include ALL sung lyrics, spoken words, and vocal content
- Transcribe lyrics exactly as sung, preserving the language
- For instrumental sections with no vocals, use "[Instrumental]"
- Timestamps must be in milliseconds
- Segments should not overlap
- Do NOT skip music sections — the lyrics ARE the content

Return ONLY the JSON array, no markdown formatting or explanation.
Example: [{"text": "Never gonna give you up, never gonna let you down", "startMs": 0, "endMs": 25000}]"""


def _usage_token(usage_md: object, attr: str) -> int:
    """Read an int token count off ``usage_metadata``, type-guarded.

    A missing field or a bare ``MagicMock`` (in tests) yields 0 rather than a
    Mock that would blow up ``int()`` downstream.
    """
    value = getattr(usage_md, attr, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _get_mime_type(audio_path: Path) -> str:
    """Get MIME type from file extension."""
    ext = audio_path.suffix.lower()
    mime = MIME_TYPES.get(ext)
    if mime is None:
        logger.warning("Unknown audio extension '%s', defaulting to audio/webm", ext)
        return "audio/webm"
    return mime


def _download_audio_raw_sync(video_id: str) -> Path:
    """Download audio from YouTube via stream-copy extraction (no re-encode).

    Unlike Whisper's download which re-encodes to MP3, this keeps the source
    audio codec: FFmpegExtractAudio with ``preferredcodec: "best"`` remuxes
    (webm/opus → .opus, mp4/aac → .m4a) without transcoding, and Gemini
    accepts those containers natively.

    Args:
        video_id: YouTube video ID

    Returns:
        Path to downloaded audio file (raw format)

    Raises:
        TranscriptError: If download fails after all retries
    """
    TEMP_DIR.mkdir(exist_ok=True)
    download_id = uuid.uuid4().hex[:8]
    file_stem = f"{video_id}_{download_id}"
    output_path = TEMP_DIR / f"{file_stem}.%(ext)s"

    ydl_opts = {
        # bestaudio has no match when YouTube's SABR experiment strips audio-only
        # format URLs from the android client (2026-09) — /best falls back to a
        # progressive muxed stream.
        "format": "bestaudio/best",
        # "best" stream-copies the audio track (no re-encode); a muxed /best
        # download gets its video track dropped here so Gemini never receives
        # video bytes.
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
        "outtmpl": str(output_path),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "continuedl": False,
    }

    started = time.monotonic()
    download_youtube_audio(video_id, ydl_opts, TEMP_DIR, file_stem)

    # Find the downloaded file (extension varies by source format)
    downloaded = [p for p in TEMP_DIR.glob(f"{file_stem}.*") if not p.suffix.endswith(".part")]
    record_download(
        kind="audio", purpose="gemini", start_monotonic=started,
        path=downloaded[0] if downloaded else None, ok=bool(downloaded),
    )
    if not downloaded:
        raise TranscriptError(
            "Audio download completed but file not found",
            ErrorCode.UNKNOWN_ERROR,
        )

    audio_path = downloaded[0]
    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    logger.info("Downloaded raw audio: %s (%.1fMB)", audio_path.name, file_size_mb)
    return audio_path


def _recover_truncated_json(text: str, start_idx: int) -> list[dict]:
    """Attempt to recover segments from a truncated JSON array.

    When Gemini's output is cut off mid-JSON, find the last complete object
    and close the array. This salvages all fully-transmitted segments.

    Args:
        text: The full response text
        start_idx: Index of the opening '[' in text

    Returns:
        Parsed list of segment dicts

    Raises:
        ValueError: If no complete segments can be recovered
    """
    array_content = text[start_idx:]

    # Find the last complete object by searching backwards for '}' that likely
    # closes a segment. Only attempt json.loads at segment boundaries (where
    # '}' is followed by ',' or whitespace/end-of-string) to avoid O(n*m)
    # repeated parsing on large responses with many '}' inside text values.
    last_brace = array_content.rfind("}")
    while last_brace > 0:
        after = array_content[last_brace + 1 : last_brace + 3].lstrip()
        if after == "" or after.startswith(",") or after.startswith("\n"):
            candidate = array_content[: last_brace + 1] + "]"
            try:
                result = json.loads(candidate)
                if isinstance(result, list):
                    logger.warning(
                        "Recovered %d segments from truncated Gemini response "
                        "(response was %d chars, truncated at char %d)",
                        len(result),
                        len(text),
                        start_idx + last_brace,
                    )
                    return result
            except json.JSONDecodeError:
                pass
        # Try the next '}' backwards
        last_brace = array_content.rfind("}", 0, last_brace)

    raise ValueError(
        f"No complete segments recoverable from truncated response "
        f"(start: {repr(text[:200])}, end: {repr(text[-200:])})"
    )


def _parse_ndjson_response(text: str) -> list[dict]:
    """Parse newline-delimited JSON objects into a list.

    Gemini sometimes returns individual JSON objects separated by newlines
    instead of a JSON array. Each line is a standalone segment object.

    Args:
        text: Response text starting with '{' and containing newline-separated objects

    Returns:
        List of parsed dicts

    Raises:
        ValueError: If no valid objects can be parsed
    """
    segments: list[dict] = []
    for line in text.split("\n"):
        line = line.strip().rstrip(",")
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                segments.append(obj)
        except json.JSONDecodeError:
            continue

    if not segments:
        raise ValueError(f"No parseable JSON objects in newline-delimited response: {text[:200]}")

    logger.info("Parsed %d segments from newline-delimited JSON response", len(segments))
    return segments


def _parse_gemini_response(response_text: str) -> list[dict]:
    """Parse Gemini's JSON response into segment dicts.

    Handles common LLM response quirks:
    - Markdown code blocks wrapping the JSON
    - Extra text before/after the JSON array

    Args:
        response_text: Raw text from Gemini completion

    Returns:
        List of segment dicts with text, startMs, endMs

    Raises:
        ValueError: If response cannot be parsed as JSON segments
    """
    text = response_text.strip()

    # Strip markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    # Find the JSON array boundaries
    start_idx = text.find("[")

    if start_idx == -1:
        # No array found — check for newline-delimited JSON objects:
        # {"text": "...", ...}\n{"text": "...", ...}
        if text.startswith("{"):
            segments = _parse_ndjson_response(text)
        else:
            raise ValueError(f"No JSON array found in response: {text[:200]}")
    else:
        end_idx = text.rfind("]")

        if end_idx != -1 and end_idx > start_idx:
            # Normal case: complete JSON array
            json_str = text[start_idx : end_idx + 1]
            try:
                segments = json.loads(json_str)
            except json.JSONDecodeError as parse_err:
                # rfind("]") may have found a stray ] inside text (e.g. "[music]").
                # Fall through to truncation recovery.
                try:
                    segments = _recover_truncated_json(text, start_idx)
                except ValueError:
                    raise ValueError(
                        f"JSON parse failed and recovery failed: {parse_err}"
                    ) from parse_err
        else:
            # No closing ] at all — response was truncated
            segments = _recover_truncated_json(text, start_idx)

    if not isinstance(segments, list):
        raise ValueError(f"Expected JSON array, got {type(segments)}")

    # Validate and normalize segments
    normalized = []
    for seg in segments:
        if not isinstance(seg, dict) or "text" not in seg:
            continue
        seg_text = str(seg["text"]).strip()
        if not seg_text:
            continue
        normalized.append(
            {
                "text": seg_text,
                "startMs": int(seg.get("startMs", 0)),
                "endMs": int(seg.get("endMs", 0)),
            }
        )

    return normalized


def _get_gemini_model() -> str:
    """Get the Gemini model to use for audio transcription.

    Uses GEMINI_TRANSCRIPTION_MODEL by default. If the user has explicitly
    configured a Gemini model via LLM_FAST_MODEL (starts with 'gemini/'),
    uses that instead (stripping the 'gemini/' prefix for the google-genai SDK).
    """
    configured = settings.LLM_FAST_MODEL  # explicit override, not the property
    if configured and configured.startswith("gemini/"):
        return configured[len("gemini/") :]
    return GEMINI_TRANSCRIPTION_MODEL


async def transcribe_with_gemini(
    video_id: str,
    is_music: bool = False,
) -> NormalizedTranscript:
    """Full async workflow: download raw audio -> upload to Gemini -> transcribe.

    Uses the google-genai SDK directly for reliable file upload and
    audio-based completion (LiteLLM's file reference doesn't work with Gemini).

    Args:
        video_id: YouTube video ID
        is_music: If True, use music-specific prompt that transcribes lyrics

    Returns:
        NormalizedTranscript with source="gemini"

    Raises:
        TranscriptError: If any step fails
    """
    if not settings.GEMINI_API_KEY:
        raise TranscriptError(
            "GEMINI_API_KEY not configured",
            ErrorCode.UNKNOWN_ERROR,
        )

    audio_path: Path | None = None
    upload_result = None

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as e:
        raise TranscriptError(
            f"Failed to initialize Gemini client: {e}",
            ErrorCode.UNKNOWN_ERROR,
        ) from e

    try:
        prompt = MUSIC_TRANSCRIPTION_PROMPT if is_music else TRANSCRIPTION_PROMPT
        logger.info(
            "Starting Gemini transcription for %s (music_mode=%s)",
            video_id,
            "ON" if is_music else "OFF",
        )

        # Download raw audio (blocking, run in thread)
        audio_path = await asyncio.to_thread(_download_audio_raw_sync, video_id)

        mime_type = _get_mime_type(audio_path)
        file_size = audio_path.stat().st_size
        logger.info(
            "Audio ready: %s (%.1fMB, %s)",
            audio_path.name,
            file_size / 1024 / 1024,
            mime_type,
        )

        # Upload file to Gemini File API
        logger.info("Uploading audio to Gemini File API")
        upload_result = await asyncio.wait_for(
            client.aio.files.upload(
                file=audio_path,
                config=genai_types.UploadFileConfig(mime_type=mime_type),
            ),
            timeout=settings.GEMINI_UPLOAD_TIMEOUT,
        )
        logger.info("File uploaded: %s (uri: %s)", upload_result.name, upload_result.uri)

        # Transcribe using Gemini with file reference
        model_name = _get_gemini_model()
        logger.info("Generating transcription with model: %s", model_name)
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=[
                genai_types.Content(
                    parts=[
                        genai_types.Part.from_text(text=prompt),
                        genai_types.Part.from_uri(
                            file_uri=upload_result.uri,
                            mime_type=mime_type,
                        ),
                    ]
                ),
            ],
            config=genai_types.GenerateContentConfig(
                max_output_tokens=GEMINI_TRANSCRIPTION_MAX_TOKENS,
            ),
        )

        # Real token counts for the usage ledger (google.genai bypasses
        # LiteLLM, so this is the only place the cost is observable).
        usage_md = getattr(response, "usage_metadata", None)
        tokens_in = _usage_token(usage_md, "prompt_token_count")
        tokens_out = _usage_token(usage_md, "candidates_token_count")

        # Safely extract response text — response.text raises ValueError
        # when Gemini returns no candidates (blocked content, empty audio, etc.)
        try:
            response_text = response.text
        except ValueError:
            # Extract block/finish reason for diagnostics
            block_reason = None
            finish_reason = None
            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                finish_reason = getattr(candidate, "finish_reason", None)
            if hasattr(response, "prompt_feedback"):
                block_reason = getattr(response.prompt_feedback, "block_reason", None)
            logger.error(
                "Gemini returned empty response for %s (block_reason=%s, finish_reason=%s)",
                video_id,
                block_reason,
                finish_reason,
            )
            raise TranscriptError(
                f"Gemini returned no transcription (block_reason={block_reason}, "
                f"finish_reason={finish_reason})",
                ErrorCode.NO_TRANSCRIPT,
            )

        if not response_text or not response_text.strip():
            logger.error("Gemini returned empty/whitespace-only text for %s", video_id)
            raise TranscriptError(
                "Gemini returned empty transcription text",
                ErrorCode.NO_TRANSCRIPT,
            )

        logger.info("Gemini response received: %s chars", len(response_text))

        # Parse JSON response into segments
        try:
            raw_segments = _parse_gemini_response(response_text)
        except (ValueError, json.JSONDecodeError) as e:
            appears_truncated = (
                "]" not in response_text[response_text.find("[") :]
                if "[" in response_text
                else True
            )
            logger.error(
                "Failed to parse Gemini response: %s | truncated=%s | start=%s | end=%s",
                e,
                appears_truncated,
                repr(response_text[:500]),
                repr(response_text[-200:]),
            )
            raise TranscriptError(
                f"Gemini returned unparseable response: {e}",
                ErrorCode.UNKNOWN_ERROR,
            )

        if not raw_segments:
            raise TranscriptError(
                "Gemini returned no transcription segments",
                ErrorCode.NO_TRANSCRIPT,
            )

        segments = []
        for seg in raw_segments:
            try:
                segments.append(
                    TranscriptSegment(
                        text=seg["text"],
                        startMs=seg["startMs"],
                        endMs=seg["endMs"],
                    )
                )
            except (KeyError, TypeError) as e:
                logger.warning("Skipping malformed Gemini segment: %s | %s", e, seg)

        if not segments:
            raise TranscriptError(
                "All Gemini segments were malformed",
                ErrorCode.NO_TRANSCRIPT,
            )

        raw_text = " ".join(seg.text for seg in segments)

        # Detect the original language now that the prompt keeps the transcript
        # in its source script, and carry it so the pipeline doesn't re-derive it
        # downstream. Text-first (langdetect) then script-fallback — the same
        # order every other call site uses (transcript_fetcher, transcript.py).
        # langdetect distinguishes languages the coarse script heuristic can't
        # (e.g. Japanese vs Chinese, or any Latin-script non-English language);
        # script detection is the zero-dependency safety net for short or
        # langdetect-unsupported transcripts.
        detected_language = detect_language_from_text(raw_text) or detect_language_by_script(
            raw_text
        )

        logger.info(
            "Gemini transcription complete: %s chars, %s segments (language=%s)",
            len(raw_text),
            len(segments),
            detected_language,
        )

        emit_transcription_usage(
            provider="google",
            model=model_name,
            feature="summarize:transcript:gemini",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=True,
        )

        return NormalizedTranscript(
            text=raw_text,
            segments=segments,
            source="gemini",
            language=detected_language,
        )

    except Exception:
        emit_transcription_usage(
            provider="google",
            model=_get_gemini_model(),
            feature="summarize:transcript:gemini",
            success=False,
        )
        raise
    finally:
        # Clean up remote Gemini file (best effort).
        if upload_result is not None:
            try:
                await client.aio.files.delete(name=upload_result.name)
            except Exception as e:
                logger.warning("Failed to delete Gemini file %s: %s", upload_result.name, e)
        # Clean up local audio file
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
                logger.debug("Cleaned up audio file: %s", audio_path)
            except Exception as e:
                logger.warning("Failed to cleanup audio file: %s", e)
