import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from llm_common.sentry_init import init_sentry_from_settings

from src.config import settings
from src.dependencies import get_mongo_client, get_video_repository
from src.logging_config import configure_structlog, get_logger
from src.middleware import add_request_context_middleware
from src.models.schemas import (
    PlaylistExtractRequest,
    PlaylistExtractResponse,
    PlaylistVideoInfo,
    SummarizeRequest,
    SummarizeResponse,
)
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes.frames import router as frames_router
from src.routes.override import router as override_router
from src.routes.purge import router as purge_router
from src.routes.stream import router as stream_router
from src.services.cache.response_cache import response_cache
from src.utils.worker_pool import shutdown_pool

# Configure structured logging (JSON in production, console in development)
configure_structlog(json_format=settings.LOG_FORMAT == "json")
logger = get_logger(__name__)


def _init_sentry_from_settings() -> bool:
    """Boot Sentry from summarizer settings.

    Thin local wrapper over the shared :func:`init_sentry_from_settings` —
    kept so tests can patch this single entry point. Empty ``SENTRY_DSN``
    no-ops downstream (production-safe default for dev/CI).
    """
    return init_sentry_from_settings(settings, service="vie-summarizer")


_usage_callback = None


def _start_stall_sweeper() -> asyncio.Task | None:
    """Launch the stall sweeper loop (HTTP process only; the worker never imports main).

    Best-effort: a wiring failure logs and leaves the app serving — sweeping
    is self-healing, not a dependency of the request path.
    """
    if not settings.STALL_SWEEP_ENABLED:
        logger.info("stall_sweeper_disabled")
        return None
    try:
        from src.services.stall_sweeper import build_default_sweeper, stall_sweeper_loop

        repository = MongoDBVideoRepository(get_mongo_client().get_default_database())
        sweeper = build_default_sweeper(repository, settings.STALL_THRESHOLD_MINUTES)
        return asyncio.create_task(
            stall_sweeper_loop(sweeper, settings.STALL_SWEEP_INTERVAL_SECONDS),
            name="stall-sweeper",
        )
    except Exception as e:
        logger.warning("stall_sweeper_start_failed", error=str(e))
        return None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: register LLM usage tracking callback and preload models."""
    global _usage_callback

    # Validate secrets before anything else
    from src.config import validate_secrets

    validate_secrets()

    # Canonical pipeline version (packages/shared/src/config/pipeline-version.json)
    # — the api logs the same key at boot; the two lines must match.
    logger.info("pipeline_version", version=settings.PIPELINE_VERSION)

    # Sentry first — so a failure inside any subsequent boot step surfaces
    # with full stack traces in Sentry rather than vanishing into journald.
    try:
        sentry_enabled = _init_sentry_from_settings()
        logger.info("sentry_init", enabled=sentry_enabled)
    except Exception as exc:  # noqa: BLE001 - boot must not depend on observability
        logger.warning("sentry_init_failed", error=str(exc))

    # Preload spaCy model to avoid blocking the event loop on first request.
    # Fail hard if model missing — pipeline will crash on first video otherwise.
    try:
        from src.services.transcript.cleaner import _get_nlp

        await asyncio.to_thread(_get_nlp)
        logger.info("spacy_model_preloaded")
    except Exception as e:
        logger.error(
            "spacy_preload_failed — pipeline will not function without spaCy model", error=str(e)
        )
        raise RuntimeError(f"Cannot start without spaCy model: {e}") from e

    # Preload SentenceTransformer model at startup (avoids cold-start latency
    # and repeated model load logs on first embedding request per worker).
    try:
        from src.services.vector.embedding import _get_model

        await asyncio.to_thread(_get_model)
        logger.info("sentence_transformer_model_preloaded")
    except Exception as e:
        # Non-fatal: embedding is used for RAG, not critical pipeline path
        logger.warning("sentence_transformer_preload_failed", error=str(e))

    try:
        import litellm
        from llm_common import MongoDBUsageCallback

        client = get_mongo_client()
        db = client.get_default_database()
        _usage_callback = MongoDBUsageCallback(db, service="summarizer", mode="sync")
        litellm.callbacks = [_usage_callback]
        logger.info("llm_usage_callback_registered", mode="sync")
    except ImportError:
        logger.warning("llm_common not installed, usage tracking disabled")
    except Exception as e:
        logger.warning("llm_usage_callback_failed", error=str(e))

    # Initialize Langfuse observability — no-op when keys are unset.
    try:
        from src.services.observability import init_langfuse

        client = init_langfuse()
        logger.info("langfuse_init", enabled=client is not None)
    except Exception as e:
        logger.warning("langfuse_init_failed", error=str(e))

    sweeper_task = _start_stall_sweeper()

    yield

    if sweeper_task is not None:
        sweeper_task.cancel()
        try:
            await sweeper_task
        except asyncio.CancelledError:
            pass

    # Shutdown worker pool
    try:
        shutdown_pool()
    except Exception as e:
        logger.warning("worker_pool_shutdown_failed", error=str(e))

    # Close Redis connection pool
    try:
        await response_cache.close()
    except Exception as e:
        logger.warning("redis_shutdown_failed", error=str(e))

    if _usage_callback:
        try:
            _usage_callback.shutdown_sync()
        except Exception as e:
            logger.warning("callback_shutdown_failed", error=str(e))

    # Drain Langfuse buffer so in-flight spans aren't lost on container stop.
    try:
        from src.services.observability import flush_langfuse

        await flush_langfuse()
    except Exception as e:
        logger.warning("langfuse_flush_failed", error=str(e))


app = FastAPI(title="vie-summarizer", lifespan=lifespan)
# Add middleware
add_request_context_middleware(app)

# Register routers
app.include_router(stream_router)
app.include_router(override_router)
app.include_router(purge_router)
app.include_router(frames_router)


@app.get("/health")
async def health():
    """Health check endpoint with DB and S3 connectivity verification."""
    # Check MongoDB
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        db_status = "connected"
    except Exception as e:
        logger.warning("mongodb_health_check_failed", error=str(e))
        db_status = "disconnected"

    # Check S3 (optional - don't fail health if S3 unavailable)
    s3_status = "not_configured"
    if settings.AWS_ENDPOINT_URL or settings.AWS_ACCESS_KEY_ID:
        try:
            from src.services.media.s3_client import s3_client

            s3_health = await s3_client.health_check()
            s3_status = s3_health.get("status", "unknown")
        except Exception as e:
            logger.warning("s3_health_check_failed", error=str(e))
            s3_status = "error"

    # Overall status: healthy if DB is connected (S3 is optional)
    overall_status = "healthy" if db_status == "connected" else "degraded"

    return {
        "status": overall_status,
        "service": "vie-summarizer",
        "model": settings.llm_model,
        "database": db_status,
        "s3": s3_status,
    }


@app.get("/")
async def root():
    return {"service": "vie-summarizer", "version": "0.1.0"}


@app.post("/summarize", response_model=SummarizeResponse, status_code=202)
async def summarize(
    request: SummarizeRequest,
    repository: Annotated[MongoDBVideoRepository, Depends(get_video_repository)],
):
    """
    Accept video summarization request.

    This endpoint registers the request and stores provider config.
    Actual processing happens via the streaming endpoint (GET /summarize/stream/{id})
    which the frontend connects to for real-time progress updates.

    NOTE: No background processing is started here - the streaming route handles
    all the actual summarization work. This prevents duplicate processing.
    """
    logger.info("Received summarize request: providers=%s", request.providers)

    # Store provider config in database for streaming route to use
    if request.providers:
        repository.set_provider_config(
            request.videoSummaryId,
            {
                "default": request.providers.default,
                "fast": request.providers.fast,
                "fallback": request.providers.fallback,
            },
        )

    return SummarizeResponse(
        status="accepted",
        videoSummaryId=request.videoSummaryId,
    )


@app.post("/playlist/extract", response_model=PlaylistExtractResponse)
async def extract_playlist(request: PlaylistExtractRequest):
    """
    Extract playlist metadata using yt-dlp.

    Uses extract_flat mode for fast metadata-only extraction.
    Returns playlist info and list of videos with positions.
    """
    from src.services.video.playlist import extract_playlist_data

    logger.info("Extracting playlist: %s (max=%s)", request.playlist_id, request.max_videos)

    try:
        playlist = await extract_playlist_data(request.playlist_id, max_videos=request.max_videos)

        return PlaylistExtractResponse(
            playlist_id=playlist.playlist_id,
            title=playlist.title,
            channel=playlist.channel,
            thumbnail_url=playlist.thumbnail_url,
            total_videos=playlist.total_videos,
            videos=[
                PlaylistVideoInfo(
                    video_id=v.video_id,
                    title=v.title,
                    position=v.position,
                    duration=v.duration,
                    thumbnail_url=v.thumbnail_url,
                )
                for v in playlist.videos
            ],
        )
    except ValueError as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(e))
