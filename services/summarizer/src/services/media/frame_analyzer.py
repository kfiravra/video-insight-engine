"""Vision LLM analysis for scored keyframes.

Sends top-N frames as base64 images to a vision-capable LLM,
receives structured scene descriptions (type, content, text, value).

CPU cost: ~0 (just base64 encoding). LLM cost: ~$0.02-0.03 for 8 frames.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.services.pipeline.pipeline_timing import record_llm_failure

if TYPE_CHECKING:
    from src.services.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

VISION_ANALYSIS_PROMPT = """\
Analyze these video keyframes. For each frame, provide:
- scene_type: one of slide, code, diagram, whiteboard, talking_head, \
product_demo, card_reveal, product_closeup, collectible_display, food_cooking, location, \
screen_recording, equipment, chart, table, intro, outro, other
- content: 1-sentence description of what's shown (be specific — names, labels, code snippets)
- text_visible: any text you can read on screen (exact transcription, empty string if none)
- educational_value: why this frame matters for understanding the video (null when the frame \
shows nothing beyond a person talking)
- visual_subject: "content" when the subject matter (card, product, screen, dish, place) \
dominates the frame; "presenter" when a person dominates with no subject matter shown; \
"mixed" when both share the frame

IMPORTANT: a hand holding a card, product, or object up to the camera is card_reveal / \
product_closeup / product_demo with visual_subject "content" — NOT talking_head — even when \
the presenter is partly visible. Use talking_head ONLY when no subject matter is discernible.

Return a JSON array with one object per frame, in the same order as the images.
Each object must have keys: frame_index (0-based), scene_type, content, text_visible, \
educational_value, visual_subject.

Example:
[
  {"frame_index": 0, "scene_type": "card_reveal", "content": "Alternate-art Shanks card held \
up to the camera", "text_visible": "Shanks OP17-120", "educational_value": "The chase pull of \
the box", "visual_subject": "content"},
  {"frame_index": 1, "scene_type": "talking_head", "content": "Presenter speaking to camera", \
"text_visible": "", "educational_value": null, "visual_subject": "presenter"}
]

Return ONLY the JSON array — no markdown fences, no explanation.\
"""


_MAX_FRAME_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB safety cap


def _encode_frame_base64(path: str) -> str | None:
    """Read a frame file and encode as base64 data URI."""
    try:
        file_size = os.path.getsize(path)
        if file_size > _MAX_FRAME_SIZE_BYTES:
            logger.warning("Frame too large (%d bytes), skipping: %s", file_size, path)
            return None
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
        mime = mime_map.get(ext, "image/jpeg")
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except OSError as e:
        logger.debug("Failed to encode frame %s: %s", path, e)
        return None


def _select_top_frames(frames: list[dict], max_frames: int) -> list[dict]:
    """Select top frames by total_score, filtering out invalid paths."""
    valid = [f for f in frames if f.get("path") and os.path.isfile(f["path"])]
    sorted_frames = sorted(valid, key=lambda f: f.get("total_score", 0), reverse=True)
    return sorted_frames[:max_frames]


async def analyze_frames_with_vision(
    frames: list[dict],
    llm_provider: LLMProvider,
    max_frames: int = 8,
    timeout: float = 60.0,
) -> list[dict]:
    """Send top-scored frames to vision LLM for scene analysis.

    Args:
        frames: Scored frame dicts with 'path' and 'total_score'.
        llm_provider: LLMProvider instance (uses primary model).
        max_frames: Maximum frames to send (cost control).
        timeout: Per-call timeout in seconds.

    Returns:
        List of frame description dicts with scene_type, content, etc.
        Returns empty list on failure (non-critical).
    """
    top_frames = _select_top_frames(frames, max_frames)
    if not top_frames:
        return []

    # Build multipart content: text prompt + images with timestamp context
    content: list[dict[str, Any]] = [{"type": "text", "text": VISION_ANALYSIS_PROMPT}]

    frame_metadata: list[dict] = []
    for i, frame in enumerate(top_frames):
        data_uri = _encode_frame_base64(frame["path"])
        if not data_uri:
            continue

        ts = frame.get("timestamp", 0)
        mins = int(ts) // 60
        secs = int(ts) % 60
        content.append({"type": "text", "text": f"Frame {i} (at {mins}:{secs:02d}):"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": data_uri},
            }
        )
        frame_metadata.append(
            {
                "index": i,
                "timestamp_sec": ts,
                "s3_url": frame.get("s3_url", ""),
                "original_index": frame.get("index"),
            }
        )

    if not frame_metadata:
        return []

    messages = [{"role": "user", "content": content}]

    # Per-stage vision model override (settings.LLM_VISION_MODEL).  2026-05-19
    # benchmark (`reports/fast-model-bench-20260519-074647.md`) found
    # `anthropic/claude-haiku-4-5-20251001` reaches 0.875 scene-match vs the
    # Sonnet baseline at ~30% of Sonnet's cost.  When unset, falls back to
    # the caller's provider (primary model). Tests rely on the autouse
    # ``_disable_stage_model_overrides`` fixture in ``tests/conftest.py`` to
    # keep this None so MagicMock providers reach the call unmodified.
    effective_provider = llm_provider
    vision_model = settings.get_stage_model("vision")
    if vision_model:
        from src.services.llm_provider import LLMProvider as _LLMProvider

        effective_provider = _LLMProvider(model=vision_model, fast_model=vision_model)

    started = time.monotonic()
    # The deadline must scale with the batch like the token cap does: a
    # 40-frame HIGH-tier batch generates ~10K output tokens plus 40 base64
    # images of ingest — a fixed 90s deadline times out routinely, silently
    # collapsing the adaptive-tier feature to its local-selection fallback.
    effective_timeout = max(timeout, 4.0 * len(frame_metadata))
    try:
        # ~250 output tokens per frame description; a fixed cap silently
        # truncates larger batches → JSONDecodeError → total loss of ALL
        # descriptions. Scale with the batch.
        raw = await asyncio.wait_for(
            effective_provider.complete_with_messages(
                messages,
                max_tokens=max(2000, 250 * len(frame_metadata)),
                timeout=effective_timeout,
                use_fast_model=False,
                span_name="frame_vision",
                span_metadata={"frameCount": len(frame_metadata)},
            ),
            timeout=effective_timeout + 5,  # outer safety net
        )
        logger.info(
            "frame_vision.complete",
            extra={
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "frames": len(frame_metadata),
                "timeout_s": effective_timeout,
            },
        )
        return parse_vision_response(raw, frame_metadata)
    except asyncio.TimeoutError as e:
        record_llm_failure(
            span="frame_vision",
            model=effective_provider.model,
            error=e,
            start_monotonic=started,
            attempt=1,
        )
        logger.warning(
            "Vision analysis timed out",
            extra={
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "timeout_s": effective_timeout,
                "frames": len(frame_metadata),
            },
        )
        return []
    except Exception as e:
        logger.warning(
            "Vision analysis failed (non-critical): %s",
            e,
            extra={
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "frames": len(frame_metadata),
            },
        )
        return []


def parse_vision_response(raw_text: str, frame_metadata: list[dict]) -> list[dict]:
    """Parse vision LLM response into structured frame descriptions.

    Args:
        raw_text: Raw LLM output (expected JSON array).
        frame_metadata: Metadata from original frames (timestamp, s3_url).

    Returns:
        List of enriched frame description dicts.
    """
    if not raw_text or not raw_text.strip():
        return []

    text = raw_text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse vision response as JSON: %s", e)
        return []

    if not isinstance(parsed, list):
        logger.warning("Vision response is not a list: %s", type(parsed).__name__)
        return []

    results: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        frame_idx = item.get("frame_index", len(results))
        if not isinstance(frame_idx, int) or frame_idx < 0 or frame_idx >= len(frame_metadata):
            meta = {}
        else:
            meta = frame_metadata[frame_idx]

        results.append(
            {
                "frame_index": frame_idx,
                "scene_type": item.get("scene_type", "other"),
                "content": item.get("content", ""),
                "text_visible": item.get("text_visible", ""),
                "educational_value": item.get("educational_value"),
                "visual_subject": item.get("visual_subject", ""),
                "timestamp_sec": meta.get("timestamp_sec", 0),
                "s3_url": meta.get("s3_url", ""),
                "original_index": meta.get("original_index"),
            }
        )

    return results


def format_visual_annotation(desc: dict) -> str:
    """Format a frame description as a transcript annotation.

    Returns:
        String like '[VISUAL at 3:42: Python function implementing binary search]'
    """
    ts = desc.get("timestamp_sec", 0)
    mins = int(ts) // 60
    secs = int(ts) % 60

    content = desc.get("content", "")[:300]
    text_visible = desc.get("text_visible", "")[:200]

    parts = [content]
    if text_visible:
        parts.append(f'Text: "{text_visible}"')

    return f"[VISUAL at {mins}:{secs:02d}: {'; '.join(parts)}]"
