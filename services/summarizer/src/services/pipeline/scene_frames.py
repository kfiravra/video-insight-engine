"""Shared scene-frame processing helpers.

Single OCR pass for frame metadata (the OCR text also feeds the
``<visual_annotations>`` block, see ``visual_annotations.py``).
Handles the 3-tier frame structure: all_frames, selected_frames, gallery_frames.
"""

from __future__ import annotations

import asyncio
import logging

from src.services.media.s3_client import s3_client
from src.services.media.frame_ocr import extract_text_from_frames, enrich_transcript_with_ocr
from src.services.pipeline.pipeline_helpers import sse_event
from src.utils.constants import YOUTUBE_ID_RE

logger = logging.getLogger(__name__)


async def process_scene_frames(
    extraction_result: dict | list,
    youtube_id: str,
    clean_text: str | None = None,
) -> tuple[dict, str, str | None]:
    """Run OCR, generate presigned URLs, optionally enrich transcript.

    Accepts the 3-tier dict from extract_scene_keyframes() or a legacy
    flat list for backward compatibility.

    Args:
        extraction_result: Dict with all_frames/selected_frames/gallery_frames,
            or a flat list of frame dicts (legacy).
        youtube_id: YouTube video ID.
        clean_text: If provided, OCR results enrich this transcript text.

    Returns:
        (enriched_result, sse_event_string, updated_clean_text_or_None).
        enriched_result has same shape as input (3-tier dict).
    """
    # Validate youtube_id (defense-in-depth)
    if not YOUTUBE_ID_RE.match(youtube_id):
        logger.warning("Invalid youtube_id in process_scene_frames: %s", youtube_id)
        empty = {"all_frames": [], "selected_frames": [], "gallery_frames": []}
        return empty, "", None

    # Handle legacy flat list input
    if isinstance(extraction_result, list):
        extraction_result = {
            "all_frames": extraction_result,
            "selected_frames": extraction_result,
            "gallery_frames": extraction_result[:12],
        }

    all_frames = extraction_result.get("all_frames", [])
    selected_frames = extraction_result.get("selected_frames", [])
    gallery_frames = extraction_result.get("gallery_frames", [])

    # Single OCR pass on ALL frames (for transcript enrichment, non-critical)
    ocr_frames = [f for f in all_frames if f.get("path")]
    ocr_results: list[dict] = []
    try:
        if ocr_frames:
            ocr_results = await asyncio.to_thread(extract_text_from_frames, ocr_frames)
    except Exception as e:
        logger.warning("Scene OCR failed (non-critical): %s", e)
    ocr_map = {r["index"]: r for r in ocr_results} if ocr_results else {}

    # Enrich transcript if clean_text provided
    updated_text = None
    if clean_text is not None and ocr_results:
        updated_text = enrich_transcript_with_ocr(clean_text, ocr_results)
        logger.info("Scene OCR: enriched transcript with %d text frames", len(ocr_results))

    # Generate presigned URLs for SELECTED frames only (those with s3_key)
    enriched_selected: list[dict] = []
    for f in selected_frames:
        s3_key = f.get("s3_key")
        if not s3_key:
            continue
        url = s3_client.generate_presigned_url(s3_key)
        ocr = ocr_map.get(f.get("index"), {})
        enriched_selected.append(
            {
                **f,
                "s3_url": url,
                "ocr_text": ocr.get("ocr_text"),
                "text_density": ocr.get("text_density", 0.0),
            }
        )

    # Enrich gallery frames with presigned URLs
    enriched_gallery: list[dict] = []
    selected_url_map = {f.get("index"): f.get("s3_url", "") for f in enriched_selected}
    for f in gallery_frames:
        idx = f.get("index")
        url = selected_url_map.get(idx)
        if not url and f.get("s3_key"):
            url = s3_client.generate_presigned_url(f["s3_key"])
        ocr = ocr_map.get(idx, {})
        enriched_gallery.append(
            {
                **f,
                "s3_url": url or "",
                "ocr_text": ocr.get("ocr_text"),
                "text_density": ocr.get("text_density", 0.0),
            }
        )

    # Enrich all_frames with OCR data (no presigned URLs — they have no S3 keys)
    enriched_all: list[dict] = []
    for f in all_frames:
        ocr = ocr_map.get(f.get("index"), {})
        enriched_all.append(
            {
                **f,
                "ocr_text": ocr.get("ocr_text"),
                "text_density": ocr.get("text_density", 0.0),
            }
        )

    # Build SSE event with SELECTED frames only (those with S3 URLs)
    event_str = sse_event(
        "frames",
        {
            "videoId": youtube_id,
            "frames": [
                {
                    "index": f.get("index", 0),
                    "timestamp": f.get("timestamp", 0.0),
                    "url": f.get("s3_url", ""),
                    "s3Key": f.get("s3_key", ""),
                    "ocrText": f.get("ocr_text"),
                    "textDensity": f.get("text_density", 0.0),
                }
                for f in enriched_selected
            ],
        },
    )

    result = {
        "all_frames": enriched_all,
        "selected_frames": enriched_selected,
        "gallery_frames": enriched_gallery,
    }

    return result, event_str, updated_text
