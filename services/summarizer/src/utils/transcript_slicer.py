"""Transcript slicing utilities for chapter-level transcript extraction.

This module provides functions to extract transcript text for specific time ranges,
enabling:
- Per-chapter transcript storage for RAG embedding
- Transcript display alongside generated content
- Debug/audit trail for LLM input vs output
"""

from typing import Any


def segments_in_range(
    segments: list[dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
) -> list[dict[str, Any]]:
    """
    Segments whose start falls within [start_seconds, end_seconds).

    Args:
        segments: List of transcript segments with text, startMs, endMs
        start_seconds: Range start time in seconds
        end_seconds: Range end time in seconds (exclusive)

    Returns:
        The matching segments, in their original order
    """
    start_ms = start_seconds * 1000
    end_ms = end_seconds * 1000
    return [seg for seg in segments if start_ms <= seg.get("startMs", 0) < end_ms]


def slice_transcript_for_chapter(
    segments: list[dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
) -> str:
    """
    Extract transcript text for a specific time range.

    Uses segment startMs to determine inclusion - a segment is included
    if its start time falls within [start_seconds, end_seconds).

    Args:
        segments: List of transcript segments with text, startMs, endMs
        start_seconds: Chapter start time in seconds
        end_seconds: Chapter end time in seconds

    Returns:
        Concatenated transcript text for the time range, space-separated
    """
    texts: list[str] = []
    for seg in segments_in_range(segments, start_seconds, end_seconds):
        cleaned = str(seg.get("text") or "").strip()
        if cleaned:
            texts.append(cleaned)
    return " ".join(texts)


def slice_transcript_for_chapters(
    segments: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
) -> list[str]:
    """
    Extract transcript text for multiple chapters.

    Args:
        segments: List of transcript segments with text, startMs, endMs
        chapters: List of chapter dicts with startSeconds and endSeconds

    Returns:
        List of transcript strings, one per chapter in order
    """
    if not chapters:
        return []

    return [
        slice_transcript_for_chapter(
            segments,
            start_seconds=ch.get("startSeconds", 0),
            end_seconds=ch.get("endSeconds", 0),
        )
        for ch in chapters
    ]
