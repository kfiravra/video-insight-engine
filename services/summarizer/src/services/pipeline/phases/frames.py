"""Phase 2b: Frames — scene extraction, scoring, OCR, vision analysis, presigned URLs.

Runs in parallel with the transcript phase. Both only need youtube_id
and video_data from metadata; the one link is the visual tier, which waits
(at most 3 s, at Step 6b) for the tier probe the transcript's arrival starts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, AsyncGenerator, Awaitable, Callable

from llm_common.context import llm_feature_var

from src.config import settings
from src.services.media.scene_extractor import (
    ReselectHook,
    cleanup_temp_dir,
    extract_scene_keyframes,
    persist_vision_descriptions,
)
from src.services.media.visual_tier import VisualTier, resolve_tier, tier_settings
from src.services.pipeline.pipeline_timing import mark_step
from src.services.pipeline.scene_frames import process_scene_frames

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


async def _run_vision_analysis(ctx: PipelineContext, selected_frames: list[dict]) -> None:
    """Run vision LLM analysis on top frames (non-critical)."""
    if not settings.FRAME_VISION_ENABLED:
        return
    if not selected_frames:
        return
    if ctx.frame_descriptions:
        # HIGH tier: the reselect hook already described the full candidate
        # set pre-hires — a second pass would spend tokens on nothing.
        return
    if not any(f.get("path") for f in selected_frames):
        # Manifest cache hit: frames live only in S3. Descriptions (if any)
        # were restored from the manifest by the caller; nothing to analyze.
        logger.info("Vision analysis skipped: no local frame files (manifest cache hit)")
        return

    try:
        from src.services.media.frame_analyzer import analyze_frames_with_vision

        llm_feature_var.set("summarize:frames")
        descriptions = await analyze_frames_with_vision(
            selected_frames,
            ctx.llm_service.provider,
            max_frames=settings.FRAME_VISION_MAX_FRAMES,
            timeout=settings.FRAME_VISION_TIMEOUT,
        )
        ctx.frame_descriptions = descriptions
        logger.info("Vision analysis: %d frame descriptions generated", len(descriptions))
    except Exception as e:
        logger.warning("Vision analysis failed (non-critical): %s", e)


def _is_presenter_frame(frame: dict, desc_by_index: dict) -> bool:
    """True when vision marked the frame presenter-dominated filler."""
    desc = desc_by_index.get(frame.get("index"))
    if not desc:
        return False
    scene_type = str(desc.get("scene_type") or "").strip().lower()
    visual_subject = str(desc.get("visual_subject") or "").strip().lower()
    educational = str(desc.get("educational_value") or "").strip()
    is_filler = visual_subject == "presenter" or scene_type in {
        "talking_head",
        "intro",
        "outro",
    }
    return is_filler and not educational


def _make_reselect_hook(
    ctx: PipelineContext, vision_max: int
) -> Callable[[list[dict]], Awaitable[list[dict]]]:
    """Build the HIGH-tier hook: describe all candidates, drop presenter shots.

    Runs on the low-res detection JPEGs BEFORE hires refinement, so refused
    frames never cost a 720p re-extract or an S3 upload. Descriptions land on
    ctx.frame_descriptions for downstream reuse (transcript annotations,
    extraction prompt, assembly captions).
    """

    async def _reselect(candidates: list[dict]) -> list[dict]:
        from src.services.media.frame_analyzer import analyze_frames_with_vision

        llm_feature_var.set("summarize:frames")
        descriptions = await analyze_frames_with_vision(
            candidates,
            ctx.llm_service.provider,
            max_frames=min(vision_max, len(candidates)),
            timeout=settings.FRAME_VISION_TIMEOUT,
        )
        if not descriptions:
            return candidates
        ctx.frame_descriptions = descriptions

        desc_by_index = {d.get("original_index"): d for d in descriptions}
        kept = [f for f in candidates if not _is_presenter_frame(f, desc_by_index)]
        dropped = len(candidates) - len(kept)

        floor = settings.FRAME_RESELECT_FLOOR
        if len(kept) < floor:
            refused = sorted(
                (f for f in candidates if f not in kept),
                key=lambda f: f.get("total_score", 0),
                reverse=True,
            )
            kept.extend(refused[: floor - len(kept)])
        logger.info("Vision reselect: %d presenter frames dropped, %d kept", dropped, len(kept))
        return kept

    return _reselect


class _TierGate:
    """The run's visual tier, decided once at Step 6b of scene extraction.

    That is the latest moment the tier is needed and so the longest the tier
    probe (``ctx.tier_probe_task``) has had to land; ``resolve_tier`` waits for
    it at most 3 s, then the metadata rule decides. Until then (cache hit,
    zero frames) the tier stays "standard".
    """

    def __init__(self, ctx: PipelineContext) -> None:
        self._ctx = ctx
        self.tier: VisualTier = "standard"

    async def _resolve(self) -> VisualTier:
        video_data = self._ctx.video_data
        assert video_data is not None
        video_context = video_data.context
        return await resolve_tier(
            getattr(self._ctx, "tier_probe_task", None),
            video_data.title or "",
            category=video_context.category if video_context else None,
            tags=list(video_context.display_tags) if video_context else None,
        )

    async def reselect(self) -> tuple[int | None, ReselectHook | None]:
        """Step 6b's (overselect_count, hook): HIGH tier with vision on, else none."""
        started = time.monotonic()
        try:
            self.tier = await self._resolve()
        finally:
            mark_step("frames.tier_wait", started)
        if self.tier != "high" or not settings.FRAME_VISION_ENABLED:
            return None, None
        knobs = tier_settings("high")
        overselect = int(knobs.get("overselect", settings.FRAME_OVERSELECT_COUNT))
        return overselect, _make_reselect_hook(self._ctx, int(knobs.get("visionMax", overselect)))


async def run_phase_frames(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Extract scene keyframes, score, select, run OCR + vision, and prepare for assembly.

    Reads the run's low-res file (``ctx.lowres_video``, started by the metadata
    phase), runs FFmpeg scene detection, scores frames locally, selects ~25
    best, refines them from the run's 720p file, uploads to S3, runs OCR and
    vision analysis in parallel, and stores enriched frames on context.
    """
    if not settings.SCENE_EXTRACTION_ENABLED:
        return

    if not ctx.video_data:
        return

    temp_dir = None
    try:
        # Adaptive frame effort: HIGH tier over-selects + vision-reselects
        # (the visuals ARE the content), LOW skips vision (frames are
        # decoration), STANDARD keeps the classic top-8 vision pass. The
        # tier is decided at Step 6b, not here (it waits for the tier probe).
        gate = _TierGate(ctx) if settings.FRAME_TIER_ENABLED else None
        extraction_result = await extract_scene_keyframes(
            ctx.youtube_id,
            duration_seconds=ctx.video_data.duration,
            hires_video=ctx.hires_video,
            lowres_video=ctx.lowres_video,
            resolve_reselect=gate.reselect if gate is not None else None,
            skip_cache=getattr(ctx, "cold_media", False),
        )
        tier = gate.tier if gate is not None else "standard"

        all_frames = extraction_result.get("all_frames", [])
        selected_frames = extraction_result.get("selected_frames", [])
        temp_dir = all_frames[0].get("temp_dir") if all_frames else None

        if not all_frames:
            return

        cached_descriptions = extraction_result.get("vision_descriptions") or []
        if cached_descriptions and not ctx.frame_descriptions:
            ctx.frame_descriptions = cached_descriptions
            logger.info(
                "Restored %d vision descriptions from the frame manifest", len(cached_descriptions)
            )

        # Run OCR/presigned-URLs and vision analysis in parallel.
        # Vision needs local file paths (before cleanup), so it runs here.
        # LOW tier skips vision entirely; HIGH already described frames in
        # the reselect hook (guarded inside _run_vision_analysis).
        ocr_task = process_scene_frames(extraction_result, ctx.youtube_id)
        vision_task = (
            _run_vision_analysis(ctx, selected_frames) if tier != "low" else asyncio.sleep(0)
        )

        (enriched_result, event_str), _ = await asyncio.gather(
            ocr_task,
            vision_task,
        )
        yield event_str

        # Fresh run with descriptions: persist them so the next manifest
        # cache hit (regen, bypassCache) keeps the same visual context.
        if ctx.frame_descriptions and not cached_descriptions:
            await persist_vision_descriptions(ctx.youtube_id, ctx.frame_descriptions)

        # Store on context for assembly
        ctx.scene_frames_for_assembly = enriched_result.get("selected_frames", [])
        ctx.scene_frames_all = enriched_result.get("all_frames", [])
        ctx.scene_frames_gallery = enriched_result.get("gallery_frames", [])

        logger.info(
            "Frame extraction: %d all, %d selected, %d gallery, %d vision descriptions",
            len(ctx.scene_frames_all),
            len(ctx.scene_frames_for_assembly),
            len(ctx.scene_frames_gallery),
            len(ctx.frame_descriptions),
        )
    except Exception as e:
        logger.warning("Frame extraction failed (non-critical): %s", e)
    finally:
        # Always clean up temp frame files (even on failure)
        if temp_dir:
            await cleanup_temp_dir(temp_dir)
