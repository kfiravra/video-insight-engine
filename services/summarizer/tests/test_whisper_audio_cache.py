"""Whisper's translate-reuse audio cache: only a non-English run parks its mp3 there.

Whisper names the detected language ("english", "spanish"). The cache is read
(and deleted) only by ``translate_audio_to_english``, which runs for non-English
sources only, so an English run that parked its audio would leak the file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.services.transcription.whisper_transcriber import transcribe_with_whisper

VIDEO_ID = "vid123"
MODULE = "src.services.transcription.whisper_transcriber"


async def _transcribe_small_file(tmp_path: Path, language: str) -> None:
    audio_path = tmp_path / f"{VIDEO_ID}.mp3"
    audio_path.write_bytes(b"fake audio")
    whisper_result = {
        "text": "Some words.",
        "segments": [{"text": "Some words.", "start": 0.0, "end": 1.0}],
        "language": language,
        "duration": 1.0,
    }
    with (
        patch(f"{MODULE}.TEMP_DIR", tmp_path),
        patch(f"{MODULE}._download_audio_sync", return_value=audio_path),
        patch(f"{MODULE}._transcribe_sync", return_value=whisper_result),
        patch(f"{MODULE}.emit_transcription_usage"),
    ):
        await transcribe_with_whisper(VIDEO_ID)


async def test_should_not_cache_audio_when_whisper_detects_english(tmp_path: Path) -> None:
    await _transcribe_small_file(tmp_path, "english")

    assert list(tmp_path.iterdir()) == []


async def test_should_cache_audio_when_whisper_detects_a_non_english_language(
    tmp_path: Path,
) -> None:
    await _transcribe_small_file(tmp_path, "spanish")

    assert (tmp_path / f"{VIDEO_ID}_cached.mp3").exists()
