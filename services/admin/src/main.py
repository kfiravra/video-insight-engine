"""vie-admin FastAPI app — LLM usage monitoring and system health."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorDatabase

from src.auth import ApiKeyMiddleware
from src.config import settings
from src.dependencies import close_mongo_client, get_database, init_mongo_client
from src.routes.alerts import router as alerts_router
from src.routes.auth import router as auth_router
from src.routes.health import router as health_router
from src.routes.queue import router as queue_router
from src.routes.shares import router as shares_router
from src.routes.tiers import router as tiers_router
from src.routes.usage import router as usage_router
from src.routes.users import router as users_router
from src.routes.videos import router as videos_router
from src.services.alert_evaluator import alert_evaluator_loop
from src.services.health_checker import health_poller_loop

logger = structlog.get_logger(__name__)

_health_task: asyncio.Task | None = None
_alert_task: asyncio.Task | None = None


def _init_sentry() -> bool:
    """Boot the Sentry SDK. Returns ``True`` only when actually initialized.

    Mirrors the contract of ``llm_common.sentry_init`` used by the other
    Python services: empty ``SENTRY_DSN`` → no-op, and init failures must
    never take the service down. The SDK is imported lazily so trimmed
    images without sentry-sdk still boot cleanly.
    """
    if not settings.SENTRY_DSN:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT or "development",
            release=settings.SENTRY_RELEASE,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
        )
        sentry_sdk.set_tag("service", "vie-admin")
        return True
    except ImportError:
        logger.info("sentry_sdk_not_installed")
        return False
    except Exception as exc:  # noqa: BLE001 - boot must not depend on observability
        logger.warning("sentry_init_failed", error=str(exc))
        return False


async def _drop_legacy_ttl_index(db: AsyncIOMotorDatabase) -> None:
    """Drop llm_usage's legacy 90-day TTL index if it survives from an older deploy.

    llm_usage is the financial ledger — retained indefinitely (2026-07-06
    decision, docs/llm-cost-model.md §Ledger Retention). Only drop when the
    existing ``timestamp_1`` index actually carries ``expireAfterSeconds``:
    unconditionally dropping would rebuild the ledger's timestamp index on
    every boot and leave a no-index window if startup is interrupted.
    """
    try:
        index_info = await db.llm_usage.index_information()
        if "expireAfterSeconds" in index_info.get("timestamp_1", {}):
            await db.llm_usage.drop_index("timestamp_1")
            logger.info("llm_usage_legacy_ttl_index_dropped")
    except Exception as e:  # noqa: BLE001 — boot must survive index inspection failures
        logger.warning("llm_usage_ttl_drop_check_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _health_task, _alert_task
    logger.info("sentry_init", enabled=_init_sentry())
    init_mongo_client()

    # Create TTL and query indexes in parallel
    db = get_database()

    await _drop_legacy_ttl_index(db)

    try:
        await asyncio.gather(
            db.llm_usage.create_index("timestamp"),
            db.health_history.create_index("timestamp", expireAfterSeconds=2_592_000),  # 30 days
            db.llm_usage.create_index([("cost_usd", -1)]),
            db.llm_usage.create_index([("model", 1), ("timestamp", -1)]),
            db.llm_usage.create_index([("video_id", 1)]),
            db.llm_usage.create_index([("service", 1)]),
            db.llm_usage.create_index([("prompt_hash", 1)]),
            # Per-run grouping (request_id) and per-user reconciliation (user_id).
            # Both keys are populated by the unified cost ledger going forward.
            db.llm_usage.create_index([("request_id", 1), ("timestamp", -1)]),
            db.llm_usage.create_index([("user_id", 1), ("timestamp", -1)]),
            db.llm_usage.create_index([("video_summary_id", 1)]),
            # Alert cooldown lookups + UI alert feed query by (type, recency).
            db.llm_alerts.create_index([("type", 1), ("timestamp", -1)]),
        )
    except Exception as e:
        logger.warning("index_creation_failed", error=str(e))

    # Start health poller + aggregate alert evaluator
    _health_task = asyncio.create_task(health_poller_loop())
    _alert_task = asyncio.create_task(alert_evaluator_loop())

    yield

    for task in (_health_task, _alert_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await close_mongo_client()


app = FastAPI(
    title="vie-admin",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# CORS — restrict to known origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:8002",
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Auth middleware
app.add_middleware(ApiKeyMiddleware)

# Register routers
app.include_router(auth_router)
app.include_router(usage_router)
app.include_router(health_router)
app.include_router(alerts_router)
app.include_router(shares_router)
app.include_router(tiers_router)
app.include_router(users_router)
app.include_router(queue_router)
app.include_router(videos_router)


@app.get("/health")
async def root_health():
    return {"status": "healthy", "service": "vie-admin"}


# Mount static files for React UI (if built)
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
