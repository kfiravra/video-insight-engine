"""FastAPI application for the vie-assistant service."""

from __future__ import annotations

import hmac
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from llm_common.context import bind_usage_ids, llm_feature_var
from llm_common.middleware import (
    REQUEST_ID_HEADER,
    SESSION_ID_HEADER,
    add_request_context_middleware,
    valid_correlation_id,
)
from src.bootstrap import lifespan
from src.config import settings
from src.exceptions import AppError, NotFoundError, ValidationError
from src.logging_config import configure_structlog, get_logger
from src.models.requests import (
    ActionRequest,
    ChatRequest,
    LibraryChatRequest,
    LibrarySearchRequest,
)
from src.models.responses import ActionResponse
from src.services.assistant import AssistantService
from src.services.observability import chat_trace, span
from src.services.rag import RAGService

# Trackers are imported alongside their check functions so tests can clear the
# shared buckets via this module (src.server._rate_tracker etc.).
from src.services.rate_limit import (  # noqa: F401 — trackers re-exported for tests
    _action_rate_tracker,
    _check_action_rate_limit,
    _check_library_rate_limit,
    _check_rate_limit,
    _library_rate_tracker,
    _rate_tracker,
)

# Configure structured logging before anything else
configure_structlog(json_format=settings.LOG_FORMAT == "json")
logger = get_logger(__name__)


def _correlation_ids(req: Request) -> tuple[str | None, str | None]:
    """The gateway's ``(request_id, session_id)``, each dropped unless it is a safe id."""
    return (
        valid_correlation_id(req.headers.get(REQUEST_ID_HEADER)),
        valid_correlation_id(req.headers.get(SESSION_ID_HEADER)),
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(title="vie-assistant", lifespan=lifespan)
    add_request_context_middleware(application)

    # --- Exception handlers ---

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "code": exc.code},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": "Validation error", "details": exc.errors()},
        )

    @application.exception_handler(Exception)
    async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "code": "INTERNAL_ERROR"},
        )

    # --- Routes ---

    @application.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "service": "vie-assistant",
            "model": settings.llm_model,
        }

    @application.post("/chat", response_model=None)
    async def chat(
        request: ChatRequest,
        req: Request,
        x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
    ) -> StreamingResponse | JSONResponse:
        """Stream a chat response about a video via SSE."""
        if not hmac.compare_digest(x_internal_secret, settings.INTERNAL_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")

        # vie-api forwards X-User-Id; X-Session-Id is optional and groups
        # the turns of one chat surface under one Langfuse session (D8).
        user_id = req.headers.get("X-User-Id")

        # Bucket per upstream user so one user can't exhaust (or share) another
        # user's quota on the same video; video id only as a fallback when the
        # header isn't forwarded.
        if _check_rate_limit(user_id or request.video_id):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded — try again shortly",
                    "code": "RATE_LIMIT_EXCEEDED",
                },
            )

        service: AssistantService | None = getattr(req.app.state, "assistant_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Service not ready")

        request_id, session_id = _correlation_ids(req)

        # Propagate tracking context onto every llm_usage row for this request
        # (the service adds the video's YouTube id once it loaded the video).
        llm_feature_var.set("assistant:rag_chat")
        bind_usage_ids(user_id=user_id, request_id=request_id, video_summary_id=request.video_id)

        return StreamingResponse(
            service.chat(
                video_id=request.video_id,
                message=request.message,
                history=request.conversation_history,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                confirm_token=request.confirm_token,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @application.post("/library/chat", response_model=None)
    async def library_chat(
        request: LibraryChatRequest,
        req: Request,
        x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
    ) -> StreamingResponse | JSONResponse:
        """Stream a library-wide chat response across the user's videos via SSE.

        Trusts the caller (vie-api) to derive ``video_ids`` server-side from the
        user's owned videos. Same SSE event shapes as ``/chat``.
        """
        if not hmac.compare_digest(x_internal_secret, settings.INTERNAL_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Bucket per upstream user so the per-LLM-call limit can't be bypassed
        # by varying the video_ids set; shared "anonymous" bucket as fallback.
        user_id = req.headers.get("X-User-Id")
        rate_key = user_id or "anonymous"
        if _check_rate_limit(rate_key):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded — try again shortly",
                    "code": "RATE_LIMIT_EXCEEDED",
                },
            )

        service: AssistantService | None = getattr(req.app.state, "assistant_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Service not ready")

        request_id, session_id = _correlation_ids(req)

        # Propagate tracking context onto every llm_usage row for this request.
        llm_feature_var.set("assistant:library_chat")
        bind_usage_ids(user_id=user_id, request_id=request_id)

        return StreamingResponse(
            service.library_chat(
                video_ids=list(request.video_ids),
                message=request.message,
                history=request.conversation_history,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                inventory=[lv.model_dump() for lv in request.library],
                confirm_token=request.confirm_token,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @application.post("/library/search")
    async def library_search(
        request: LibrarySearchRequest,
        req: Request,
        x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
    ) -> JSONResponse:
        """Semantic search across a library of videos.

        Trusts the caller (Node api gateway) to enforce ``video_ids``
        ownership before forwarding. Pure retrieval — no LLM.
        """
        if not hmac.compare_digest(x_internal_secret, settings.INTERNAL_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Bucket per upstream caller (vie-api forwards X-User-Id) so the
        # 60/min guarantee can't be bypassed by varying the video_ids set.
        # Falls back to a shared "anonymous" bucket when the header isn't
        # forwarded — strictly tighter than the previous payload-derived key.
        rate_key = req.headers.get("X-User-Id") or "anonymous"
        if _check_library_rate_limit(rate_key):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded — try again shortly",
                    "code": "RATE_LIMIT_EXCEEDED",
                },
            )

        rag: RAGService | None = getattr(req.app.state, "rag_service", None)
        if rag is None:
            raise HTTPException(status_code=503, detail="Service not ready")

        results = await rag.search_library(
            query=request.query,
            video_ids=list(request.video_ids),
            top_k=request.top_k,
            sources=list(request.sources) if request.sources else None,
        )
        return JSONResponse(
            status_code=200,
            content={"results": [r.model_dump() for r in results]},
        )

    @application.post("/action")
    async def action(
        request: ActionRequest,
        req: Request,
        x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
    ) -> JSONResponse:
        """Perform a structured action on a video.

        Dispatches to a registered tool based on ``action`` (save_note,
        quiz_me, find_moment, explain). The caller (vie-api) is expected to
        have verified ownership before forwarding.
        """
        if not hmac.compare_digest(x_internal_secret, settings.INTERNAL_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")

        user_id = req.headers.get("X-User-Id")
        request_id, session_id = _correlation_ids(req)
        rate_key = user_id or request.video_id or "anonymous"
        if _check_action_rate_limit(rate_key):
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded — try again shortly",
                    "code": "RATE_LIMIT_EXCEEDED",
                },
            )

        service: AssistantService | None = getattr(req.app.state, "assistant_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Service not ready")

        # Propagate tracking context onto every llm_usage row for this request
        # (dispatch_action adds the video's YouTube id once it loaded the video).
        llm_feature_var.set(f"assistant:action:{request.action}")
        bind_usage_ids(user_id=user_id, request_id=request_id, video_summary_id=request.video_id)

        trace_id = uuid4().hex[:12]
        async with chat_trace(
            video_summary_id=request.video_id,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            tags=["action", f"action:{request.action}"],
            metadata={"action": request.action, "traceId": trace_id},
        ):
            async with span(f"action:{request.action}"):
                try:
                    result = await service.dispatch_action(
                        action=request.action,
                        video_id=request.video_id,
                        params=dict(request.params),
                        user_id=user_id,
                    )
                except ValidationError as exc:
                    logger.warning(
                        "action_validation_failed",
                        trace_id=trace_id,
                        action=request.action,
                        error=exc.message,
                    )
                    return JSONResponse(
                        status_code=exc.status_code,
                        content=ActionResponse(
                            success=False,
                            action=request.action,
                            data=None,
                            error=exc.message,
                            trace_id=trace_id,
                        ).model_dump(),
                    )
                except NotFoundError as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content=ActionResponse(
                            success=False,
                            action=request.action,
                            data=None,
                            error=exc.message,
                            trace_id=trace_id,
                        ).model_dump(),
                    )
                except AppError as exc:
                    logger.exception(
                        "action_app_error",
                        trace_id=trace_id,
                        action=request.action,
                    )
                    return JSONResponse(
                        status_code=exc.status_code,
                        content=ActionResponse(
                            success=False,
                            action=request.action,
                            data=None,
                            error=exc.message,
                            trace_id=trace_id,
                        ).model_dump(),
                    )

                return JSONResponse(
                    status_code=200,
                    content=ActionResponse(
                        success=True,
                        action=request.action,
                        data=result,
                        error=None,
                        trace_id=trace_id,
                    ).model_dump(),
                )

    return application


app = create_app()
