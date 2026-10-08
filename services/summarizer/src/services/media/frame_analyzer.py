"""Vision LLM analysis for scored keyframes.

Sends the top-N frames as base64 images to a vision-capable LLM in parallel
batches and returns structured scene descriptions (type, content, text, value).
Prompt: ``prompts/vision.txt`` (registry name ``summarizer:vision``).

The stage takes as long as its slowest call and output tokens set a call's
wall time (~16 s per 1k), so frames are spread over up to five calls of at
most eight (pipeline-1min A18). A failed call costs only its own frames.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.services.pipeline.pipeline_timing import record_llm_failure
from src.services.pipeline.prompt_builder import load_prompt_text
from src.utils.llm_retry import TRANSIENT_LLM_ERRORS, retry_after_seconds

if TYPE_CHECKING:
    from src.services.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "vision.txt"

VISION_BATCH_SIZE = 8
VISION_MAX_PARALLEL = 5
# Up to this many frames go in one call; more are split over at least two, so
# STANDARD's 8 frames run as 2 x 4 (pipeline-1min g1 deviation from "8 per call").
_SINGLE_CALL_MAX_FRAMES = 4

# Output ceilings per frame type (D12): ~85 tokens observed per scene/dish
# frame, ~190 per screen recording, whose text_visible runs long. A ceiling
# costs nothing unless hit; hitting it loses the whole call (kPN564Kol14).
_OUT_TOKENS_PER_FRAME = 250
_OUT_TOKENS_PER_TEXT_FRAME = 500
_TEXT_HEAVY_SCORE = 0.15  # frame_ocr: slides/code typically score > 0.15
_BATCH_TOKEN_OVERHEAD = 200
_MIN_BATCH_TOKENS = 1000
# An unparseable reply is most often one cut at max_tokens.
_RETRY_TOKEN_FACTOR = 1.5
_MAX_ATTEMPTS = 2
_RETRY_PAUSE_SECONDS = 1.0
_SECONDS_PER_FRAME = 4.0
_SPAN_NAME = "frame_vision"

_MAX_FRAME_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB safety cap


@dataclass(frozen=True)
class _VisionBatch:
    """One vision call: its content blocks and the frames behind its labels."""

    number: int
    total: int
    content: list[dict[str, Any]]
    metadata: list[dict]
    max_tokens: int
    timeout: float


@dataclass(frozen=True)
class _Failure:
    """Why an attempt produced no descriptions, and how to retry it."""

    retryable: bool
    pause: float | None = None  # None = _RETRY_PAUSE_SECONDS
    grow_tokens: bool = False


# ─── Frame selection and encoding ───


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


def _encode_frames(frames: list[dict]) -> tuple[list[dict], dict[int, str]]:
    """Frames whose image could be read, and their data URIs keyed by ``id(frame)``."""
    encoded: dict[int, str] = {}
    for frame in frames:
        data_uri = _encode_frame_base64(frame["path"])
        if data_uri:
            encoded[id(frame)] = data_uri
    return [f for f in frames if id(f) in encoded], encoded


# ─── Batch planning ───


def _timestamp(frame: dict) -> float:
    return float(frame.get("timestamp") or 0.0)


def vision_batch_size(frame_count: int) -> int:
    """Frames per call: all in one up to 4, else at most 8 over at least 2 calls."""
    if frame_count <= _SINGLE_CALL_MAX_FRAMES:
        return max(frame_count, 1)
    return min(VISION_BATCH_SIZE, math.ceil(frame_count / 2))


def plan_vision_batches(frames: list[dict]) -> list[list[dict]]:
    """Split frames into calls: in time order, frame i goes to call i mod k.

    Strided, so every call spans the whole video and a cluster of text-heavy
    frames (a slide deck, a code walkthrough) spreads over the calls instead
    of making one of them the slowest. Each call's frames stay chronological.
    The replay harness splits a recorded single-call output with this plan.
    With batching off it is today's single call, frames in the given order.
    """
    if not frames:
        return []
    if not settings.FRAME_VISION_PARALLEL:
        return [list(frames)]
    ordered = sorted(frames, key=_timestamp)
    count = math.ceil(len(ordered) / vision_batch_size(len(ordered)))
    return [ordered[start::count] for start in range(count)]


def _frame_token_budget(frame: dict) -> int:
    text_score = float(frame.get("text_score") or 0.0)
    if text_score >= _TEXT_HEAVY_SCORE:
        return _OUT_TOKENS_PER_TEXT_FRAME
    return _OUT_TOKENS_PER_FRAME


def batch_max_tokens(frames: list[dict]) -> int:
    """Output ceiling for one call, sized by how much text each frame shows."""
    budget = _BATCH_TOKEN_OVERHEAD + sum(_frame_token_budget(f) for f in frames)
    return max(_MIN_BATCH_TOKENS, budget)


def _frame_label(position: int, timestamp: float) -> str:
    seconds = int(timestamp)
    return f"Frame {position} (at {seconds // 60}:{seconds % 60:02d}):"


def _count_line(frame_count: int) -> str:
    """The per-call half of the index mapping (the prompt file stays static)."""
    if frame_count == 1:
        return "This request has 1 frame: Frame 0. Return exactly 1 object."
    return (
        f"This request has {frame_count} frames: Frame 0 to Frame {frame_count - 1}. "
        f"Return exactly {frame_count} objects."
    )


def _build_content(
    prompt: str, frames: list[dict], encoded: dict[int, str], positions: dict[int, int]
) -> tuple[list[dict[str, Any]], list[dict]]:
    """(content blocks, per-label metadata) for one call's frames."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    metadata: list[dict] = []
    for local, frame in enumerate(frames):
        timestamp = _timestamp(frame)
        content.append({"type": "text", "text": _frame_label(local, timestamp)})
        content.append({"type": "image_url", "image_url": {"url": encoded[id(frame)]}})
        metadata.append(
            {
                "index": positions[id(frame)],
                "timestamp_sec": timestamp,
                "s3_url": frame.get("s3_url", ""),
                "original_index": frame.get("index"),
            }
        )
    content.append({"type": "text", "text": _count_line(len(frames))})
    return content, metadata


def _prepare_batches(
    prompt: str, frames: list[dict], encoded: dict[int, str], timeout: float
) -> list[_VisionBatch]:
    planned = plan_vision_batches(frames)
    positions = {id(frame): i for i, frame in enumerate(sorted(frames, key=_timestamp))}
    batches: list[_VisionBatch] = []
    for number, batch_frames in enumerate(planned, start=1):
        content, metadata = _build_content(prompt, batch_frames, encoded, positions)
        batches.append(
            _VisionBatch(
                number=number,
                total=len(planned),
                content=content,
                metadata=metadata,
                max_tokens=batch_max_tokens(batch_frames),
                timeout=max(timeout, _SECONDS_PER_FRAME * len(batch_frames)),
            )
        )
    return batches


# ─── Calls ───


def load_vision_prompt() -> str:
    """Registry-first vision prompt; records its version on the active trace."""
    return load_prompt_text(PROMPT_PATH)


def _vision_provider(llm_provider: LLMProvider) -> LLMProvider:
    """The caller's provider (primary model), unless LLM_VISION_MODEL pins one.

    Vision stays on the primary model (Sonnet) — pipeline-1min principle 5.
    Tests rely on the autouse ``_disable_stage_model_overrides`` fixture in
    ``tests/conftest.py`` so MagicMock providers reach the call unmodified.
    """
    vision_model = settings.get_stage_model("vision")
    if not vision_model:
        return llm_provider
    from src.services.llm_provider import LLMProvider as _LLMProvider

    return _LLMProvider(model=vision_model, fast_model=vision_model)


async def _send_batch(
    provider: LLMProvider, batch: _VisionBatch, attempt: int, max_tokens: int
) -> str:
    return await asyncio.wait_for(
        provider.complete_with_messages(
            [{"role": "user", "content": batch.content}],
            max_tokens=max_tokens,
            timeout=batch.timeout,
            use_fast_model=False,
            span_name=_SPAN_NAME,
            span_metadata={
                "frameCount": len(batch.metadata),
                "batch": batch.number,
                "batches": batch.total,
                "attempt": attempt,
                "maxAttempts": _MAX_ATTEMPTS,
            },
        ),
        timeout=batch.timeout + 5,  # outer safety net
    )


async def _attempt_batch(
    provider: LLMProvider, batch: _VisionBatch, attempt: int, max_tokens: int
) -> list[dict] | _Failure:
    """One call for one batch: its descriptions, or why there are none."""
    started = time.monotonic()
    where = f"batch {batch.number}/{batch.total}, attempt {attempt}/{_MAX_ATTEMPTS}"
    try:
        raw = await _send_batch(provider, batch, attempt, max_tokens)
    except asyncio.TimeoutError as e:
        # The outer wait_for cancels the provider coroutine, so the provider
        # never records this failure — record it here.
        record_llm_failure(
            span=_SPAN_NAME, model=provider.model, error=e, start_monotonic=started, attempt=attempt
        )
        logger.warning("Vision %s timed out after %.0fs", where, batch.timeout)
        return _Failure(retryable=True)
    except TRANSIENT_LLM_ERRORS as e:
        logger.warning("Vision %s failed (transient): %s", where, e)
        return _Failure(retryable=True, pause=retry_after_seconds(e))
    except Exception as e:
        # 400/401/404 or a programming error: another attempt cannot fix it.
        logger.warning("Vision %s failed (%s): %s", where, type(e).__name__, e)
        return _Failure(retryable=False)
    descriptions = parse_vision_response(raw, batch.metadata)
    if descriptions:
        return descriptions
    logger.warning("Vision %s returned no usable descriptions", where)
    return _Failure(retryable=True, grow_tokens=True)


async def _describe_batch(
    provider: LLMProvider, batch: _VisionBatch, semaphore: asyncio.Semaphore
) -> list[dict]:
    """The batch's descriptions after at most one retry; [] when both attempts fail."""
    max_tokens = batch.max_tokens
    async with semaphore:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            outcome = await _attempt_batch(provider, batch, attempt, max_tokens)
            if not isinstance(outcome, _Failure):
                return outcome
            if not outcome.retryable or attempt == _MAX_ATTEMPTS:
                break
            if outcome.grow_tokens:
                max_tokens = int(max_tokens * _RETRY_TOKEN_FACTOR)
            pause = _RETRY_PAUSE_SECONDS if outcome.pause is None else outcome.pause
            await asyncio.sleep(pause)
    return []


async def analyze_frames_with_vision(
    frames: list[dict],
    llm_provider: LLMProvider,
    max_frames: int = 8,
    timeout: float = 60.0,
) -> list[dict]:
    """Send top-scored frames to the vision LLM for scene analysis.

    Args:
        frames: Scored frame dicts with 'path' and 'total_score'.
        llm_provider: LLMProvider instance (uses primary model).
        max_frames: Maximum frames to send (cost control).
        timeout: Per-call timeout floor in seconds (scaled up per frame).

    Returns:
        Frame description dicts (scene_type, content, …) in time order. The
        batches that failed contribute nothing; [] when all failed (non-critical).
    """
    frames_to_send, encoded = _encode_frames(_select_top_frames(frames, max_frames))
    if not frames_to_send:
        return []
    batches = _prepare_batches(load_vision_prompt(), frames_to_send, encoded, timeout)
    provider = _vision_provider(llm_provider)
    semaphore = asyncio.Semaphore(VISION_MAX_PARALLEL)
    started = time.monotonic()
    results = await asyncio.gather(*(_describe_batch(provider, b, semaphore) for b in batches))
    descriptions = sorted((d for batch in results for d in batch), key=lambda d: d["frame_index"])
    logger.info(
        "frame_vision.complete",
        extra={
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "frames": len(frames_to_send),
            "batches": len(batches),
            "failed_batches": sum(1 for batch in results if not batch),
            "descriptions": len(descriptions),
        },
    )
    return descriptions


# ─── Parsing ───


def _decode_items(raw_text: str | None) -> list | None:
    """The reply's JSON array (markdown fences tolerated), or None."""
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse vision response as JSON: %s", e)
        return None
    if not isinstance(parsed, list):
        logger.warning("Vision response is not a list: %s", type(parsed).__name__)
        return None
    return parsed


def _description(item: dict, meta: dict) -> dict:
    return {
        "frame_index": meta["index"],
        "scene_type": item.get("scene_type", "other"),
        "content": item.get("content", ""),
        "text_visible": item.get("text_visible", ""),
        "educational_value": item.get("educational_value"),
        "visual_subject": item.get("visual_subject", ""),
        "timestamp_sec": meta.get("timestamp_sec", 0),
        "s3_url": meta.get("s3_url", ""),
        "original_index": meta.get("original_index"),
    }


def parse_vision_response(raw_text: str | None, frame_metadata: list[dict]) -> list[dict]:
    """Parse vision LLM response into structured frame descriptions.

    ``frame_index`` in the reply is the call's own label number; it is mapped
    back through ``frame_metadata`` (whose ``index`` is the frame's position
    across all calls). An item whose index names no frame of this call, or a
    frame already described, is dropped — kept, it would carry no timestamp
    and surface as a caption at 0:00.

    Args:
        raw_text: Raw LLM output (expected JSON array).
        frame_metadata: Per label: index, timestamp_sec, s3_url, original_index.

    Returns:
        List of enriched frame description dicts.
    """
    items = _decode_items(raw_text)
    if items is None:
        return []
    results: list[dict] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        local = item.get("frame_index", len(results))
        valid = isinstance(local, int) and not isinstance(local, bool)
        if not valid or not 0 <= local < len(frame_metadata) or local in seen:
            logger.info("Vision item dropped: frame_index %r matches no frame", local)
            continue
        seen.add(local)
        results.append(_description(item, {"index": local, **frame_metadata[local]}))
    return results
