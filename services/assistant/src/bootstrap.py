"""Application lifespan — dependency wiring for the vie-assistant service.

Extracted from ``server.py`` so the FastAPI module stays focused on routes.
Builds Mongo/Qdrant/RAG/LLM dependencies, registers the /action tools, and
tears everything down on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from llm_common.sentry_init import init_sentry_from_settings

from src.config import settings, validate_internal_secret
from src.logging_config import get_logger
from src.repositories.notes_repository import NotesRepository
from src.repositories.qdrant_repository import QdrantRepository
from src.repositories.video_repository import MongoVideoRepository
from src.services.api_client import ApiClient
from src.services.assistant import AssistantService
from src.services.context_builder import ContextBuilder
from src.services.llm_provider import LLMProvider
from src.services.observability import flush_langfuse, init_langfuse
from src.services.rag import RAGService
from src.tools.concept_explain import ConceptExplainTool
from src.tools.folder_organizer import FolderOrganizerTool
from src.tools.library_organizer import LibraryOrganizerTool
from src.tools.navigator import NavigatorTool
from src.tools.note_taker import NoteTakerTool
from src.tools.quiz_generator import QuizGeneratorTool
from src.tools.video_generator import VideoGeneratorTool

if TYPE_CHECKING:
    from llm_common import MongoDBUsageCallback

logger = get_logger(__name__)


def _init_sentry_from_settings() -> bool:
    """Boot Sentry from assistant settings.

    Thin local wrapper over the shared :func:`init_sentry_from_settings` —
    kept so tests can patch this single entry point. Empty DSN no-ops.
    """
    return init_sentry_from_settings(settings, service="vie-assistant")


async def _register_usage_callback(db: AsyncIOMotorDatabase) -> MongoDBUsageCallback | None:
    """Register the LLM usage callback; ``None`` when tracking is unavailable."""
    try:
        import litellm
        from llm_common import MongoDBUsageCallback

        # Async mode: this service uses Motor (async). Sync mode would hand the
        # SyncBuffer's background thread a Motor collection, whose insert_many
        # needs the event loop -> "no current event loop in thread" on flush.
        # Every assistant call is user chat (rag_chat, library_chat, actions,
        # tools), so the prompt preview is never stored (docs/PRIVACY.md).
        callback = MongoDBUsageCallback(
            db, service="assistant", mode="async", store_prompt_preview=False
        )
        await callback.start_async()
        litellm.callbacks = [callback]
        logger.info("llm_usage_callback_registered", mode="async")
        return callback
    except ImportError:
        logger.warning("llm_common_not_installed_usage_tracking_disabled")
    except Exception as exc:
        logger.warning("llm_usage_callback_failed", error=str(exc))
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialise dependencies and register callbacks."""
    validate_internal_secret()

    # Sentry first — every other lifespan step's boot errors flow through it.
    try:
        sentry_enabled = _init_sentry_from_settings()
        logger.info("sentry_init", enabled=sentry_enabled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentry_init_failed", error=str(exc))

    # Langfuse observability — no-op when LANGFUSE_PUBLIC_KEY/SECRET_KEY are unset.
    init_langfuse()

    # MongoDB
    mongo_client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = mongo_client.get_default_database()
    video_repo = MongoVideoRepository(db)
    logger.info("mongodb_connected")

    # Qdrant
    qdrant_repo = QdrantRepository(
        url=settings.QDRANT_URL,
        collection=settings.QDRANT_COLLECTION,
    )

    # RAG (preload embedding model). Encoder name is single-sourced with the
    # summarizer's index side; the floor keeps off-topic noise out of prompts.
    rag_service = RAGService(
        qdrant_repo=qdrant_repo,
        model_name=settings.EMBEDDING_MODEL_NAME,
        min_score=settings.RAG_MIN_SCORE,
    )
    try:
        await rag_service.preload_model()
        logger.info("sentence_transformer_model_preloaded")
    except Exception as exc:
        logger.warning("sentence_transformer_preload_failed", error=str(exc))

    usage_callback = await _register_usage_callback(db)

    # Outbound vie-api client for folder/library actions (reuses INTERNAL_SECRET).
    api_client = ApiClient(settings.VIE_API_URL, settings.INTERNAL_SECRET)
    app.state.api_client = api_client

    # Assemble services
    llm = LLMProvider()  # primary (Sonnet) — tools keep their audited tier
    # The chat + agentic loop use their own provider so they can run on a
    # cheaper/faster model (LLM_CHAT_MODEL, default Haiku) without downgrading
    # the Sonnet-pinned tools that share `llm` below.
    chat_llm = LLMProvider(
        model=settings.llm_chat_model,
        fallback_models=settings.llm_fallback_models,
    )
    context_builder = ContextBuilder()
    notes_repo = NotesRepository(db)
    assistant_service = AssistantService(
        llm=chat_llm,
        rag=rag_service,
        video_repo=video_repo,
        context_builder=context_builder,
        settings=settings,
        api_client=api_client,
    )

    # Register /action tools — uses primary (Sonnet) by default; quiz uses fast tier internally.
    assistant_service.register_tool(NavigatorTool(video_repo=video_repo))
    assistant_service.register_tool(QuizGeneratorTool(llm=llm))
    assistant_service.register_tool(NoteTakerTool(notes_repo=notes_repo))
    assistant_service.register_tool(ConceptExplainTool(rag=rag_service, llm=llm))
    # vie-api-backed action tools (folders, library organize, generate).
    assistant_service.register_tool(FolderOrganizerTool(api_client))
    assistant_service.register_tool(LibraryOrganizerTool(api_client, llm))
    assistant_service.register_tool(VideoGeneratorTool(api_client))

    app.state.assistant_service = assistant_service
    # Expose rag_service directly for /library/search (no LLM needed).
    app.state.rag_service = rag_service

    logger.info(
        "assistant_service_ready",
        chat_model=chat_llm.model,
        tool_model=llm.model,
        port=settings.ASSISTANT_PORT,
    )

    yield

    # Cleanup
    await flush_langfuse()
    await api_client.aclose()
    mongo_client.close()
    if usage_callback:
        try:
            await usage_callback.shutdown_async()
        except Exception as exc:
            logger.warning("callback_shutdown_failed", error=str(exc))
