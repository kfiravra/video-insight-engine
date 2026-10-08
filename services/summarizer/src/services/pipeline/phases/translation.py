"""Phase 8: Translation — build the source-language artifact for the FE toggle.

Generation is English-canonical, so the assembled tabs/meta are already the
English primary. This phase calls ``translate_to_source`` which walks that
English output, translates every translatable prose string into the detected
source language (batched Haiku calls), and returns the unchanged English dict
with the translated artifact nested under ``sourceLanguage``. On any failure
(LLM error, mirror, length mismatch) the returned dict has no ``sourceLanguage``
key and the phase no-ops — the FE then simply renders no language toggle.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.config import settings
from src.models.schemas import ProcessingStatus
from src.services.cache.response_cache import response_cache
from src.services.pipeline.pipeline_helpers import sse_event
from src.services.pipeline.translation import translate_text, translate_to_source
from src.services.status_callback import send_video_status_background

if TYPE_CHECKING:
    from src.repositories.mongodb_repository import MongoDBVideoRepository
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

TRANSLATION_FEATURE = "summarize:translation"


async def _translate_title(ctx: PipelineContext) -> None:
    """Give the primary view an English title and keep the original under the
    source block, so the title follows the language toggle like everything else.

    The original YouTube title is in the source language; it is the one field
    that travels source→English (the rest of the surface is English→source).
    """
    title = ctx.video_data.title if ctx.video_data else None
    if not title or not ctx.source_language_code or not isinstance(ctx.source_language, dict):
        return
    en_title = await translate_text(ctx.llm_service, title, ctx.source_language_code, "en")
    if isinstance(ctx.assembled_meta, dict):
        ctx.assembled_meta["videoTitle"] = en_title or title
    sl_meta = ctx.source_language.get("meta")
    if isinstance(sl_meta, dict):
        sl_meta["videoTitle"] = title


async def run_phase_translation(
    ctx: PipelineContext,
    repository: MongoDBVideoRepository,
    video_summary_id: str,
) -> AsyncGenerator[str, None]:
    """Translate the English output into the source language for the FE toggle."""
    if not ctx.source_language_code:
        return
    # Its own tag: extraction now runs in a task, so this inline phase would
    # otherwise inherit the metadata phase's "summarize:metadata" and the cost
    # ledger + pipeline.timing would book translation spend under metadata.
    llm_feature_var.set(TRANSLATION_FEATURE)

    yield sse_event("phase", {"phase": "translation"})

    # Generation is English-canonical, so ``assembled_tabs``/``assembled_meta``
    # are already the English primary. Translate a deep copy into the source
    # language and attach it under ``sourceLanguage``; the English top-level is
    # unchanged. (`meta` is a superset of synthesis — the FE reconstructs
    # synthesis from meta via buildSynthesisFromMeta.)
    output = {
        "tabs": ctx.assembled_tabs or [],
        "meta": ctx.assembled_meta or {},
    }
    result = await translate_to_source(ctx.llm_service, output, ctx.source_language_code)

    if "sourceLanguage" not in result:
        # Failure / no-op — English stays the only view, no toggle. This is a
        # deliberate terminal outcome (mirror detected / LLM returned None after
        # retries), so finalize the doc: assembly left it "processing" for the
        # non-English path, and English-only is the accepted final surface.
        logger.warning(
            "[pipeline] Translation produced no sourceLanguage (target=%s); "
            "finalizing English-only",
            ctx.source_language_code,
        )
        await asyncio.to_thread(
            repository.update_status,
            video_summary_id,
            ProcessingStatus.COMPLETED,
        )
        send_video_status_background(video_summary_id, None, "completed")
        return

    ctx.source_language = result["sourceLanguage"]

    # Title both ways: an English title for the primary view, the original kept
    # under the source block, so the title swaps with the toggle too.
    await _translate_title(ctx)

    saved = await asyncio.to_thread(
        repository.save_structured_result,
        video_summary_id,
        {
            "tabs": ctx.assembled_tabs,
            "meta": ctx.assembled_meta,
            "sourceLanguage": ctx.source_language,
            "language": "en",
            "isRTL": False,
            # The translation phase owns the "completed" transition for
            # non-English videos (assembly left the doc "processing").
            "status": "completed",
        },
    )
    if not saved:
        # A global purge deleted the row mid-run; do not repopulate Redis for it.
        ctx.row_deleted = True
        logger.warning("pipeline_row_deleted_mid_run video_summary_id=%s", video_summary_id)
        return
    send_video_status_background(video_summary_id, None, "completed")

    logger.info(
        "[pipeline] Translation complete: built %s sourceLanguage block",
        result["sourceLanguage"]["code"],
    )

    # Now write the final English-primary payload to Redis. Assembly phase
    # intentionally skipped the cache for non-English videos so the
    # source-language tabs never become the cached shape; this write owns
    # the cache for the full TTL and ensures FE cache hits see the toggle.
    # An eval-user run (D25) never becomes the shared cached response.
    if settings.REDIS_ENABLED and not getattr(ctx, "eval_run", False):
        from src.routes.cached_response import build_frontend_response

        frontend_response = build_frontend_response(
            {
                "status": "completed",
                "youtubeId": ctx.youtube_id,
                "title": ctx.video_data.title if ctx.video_data else None,
                "creator": ctx.video_data.channel if ctx.video_data else None,
                "duration": ctx.video_data.duration if ctx.video_data else None,
                "thumbnailUrl": ctx.video_data.thumbnail_url if ctx.video_data else None,
                "meta": ctx.assembled_meta,
                "tabs": ctx.assembled_tabs,
                "sourceLanguage": ctx.source_language,
                "language": "en",
                "isRTL": False,
            }
        )
        try:
            await response_cache.set_response(ctx.youtube_id, frontend_response)
        except (OSError, ConnectionError) as e:
            logger.debug("Redis cache failed (non-critical): %s", e)
    # Qdrant output chunks were already indexed (in English) by the assembly
    # phase — generation is English-canonical, so the tabs are unchanged here.
