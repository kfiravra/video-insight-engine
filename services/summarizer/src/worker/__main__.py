"""Worker entrypoint: ``python -m src.worker`` (executes this ``__main__.py``).

Boots the runner, connects to RabbitMQ with reconnect, declares topology, and
consumes from ``vie.pipeline.jobs``. Each message is handed to ``WorkerRunner``
which drives the existing summarizer pipeline (lock + Redis Streams broker)
exactly the way the SSE producer does — so frontend SSE consumers attach as
followers regardless of which path started the work.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.pool import Pool

from src.config import settings
from src.logging_config import configure_structlog, get_logger
from src.worker.payload import VideoJobPayload
from src.worker.pipeline import drive_pipeline
from src.worker.runner import WorkerRunner
from src.worker.sentry_bootstrap import init_sentry_for_worker
from src.worker.topology import QueueTopology, queue_arguments

configure_structlog(json_format=settings.LOG_FORMAT == "json", service_name="vie-summarizer-worker")
logger = get_logger(__name__)


# Backward-compat alias — keep _drive_pipeline as a thin reference for any
# tests or callers that still reach into __main__.
_drive_pipeline = drive_pipeline


# ─── Republish for retry ────────────────────────────────────────────────────


class _Republisher:
    """Holds a confirm channel reused across republishes."""

    def __init__(self, channel_pool: Pool[aio_pika.abc.AbstractChannel]) -> None:
        self._channel_pool = channel_pool

    async def __call__(self, payload: VideoJobPayload) -> None:
        body = payload.model_dump_json(by_alias=True).encode("utf-8")
        async with self._channel_pool.acquire() as channel:
            exchange = await channel.get_exchange(QueueTopology.exchange)
            await exchange.publish(
                aio_pika.Message(
                    body=body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    priority=payload.priority,
                    message_id=payload.request_id,
                    headers={"x-attempt": payload.attempt, "x-tier": payload.tier},
                ),
                routing_key=QueueTopology.routing_key,
            )


# ─── Liveness heartbeat ─────────────────────────────────────────────────────

# The worker has no HTTP surface, so the container healthcheck watches this
# file's mtime (see docker-compose.yml vie-summarizer-worker healthcheck). A
# wedged event loop stops the touches and the container flips unhealthy even
# though PID 1 is still alive.
HEARTBEAT_PATH = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/vie-worker-heartbeat"))
HEARTBEAT_INTERVAL_SECONDS = 30.0


async def _heartbeat_loop(shutdown: asyncio.Event) -> None:
    """Touch the heartbeat file until shutdown; write failures are non-fatal."""
    while not shutdown.is_set():
        try:
            HEARTBEAT_PATH.touch()
        except OSError as e:
            logger.warning("worker_heartbeat_write_failed", error=str(e))
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


# ─── Consumer loop ──────────────────────────────────────────────────────────


async def _consume(
    runner: WorkerRunner,
    channel: aio_pika.abc.AbstractChannel,
    shutdown: asyncio.Event,
) -> None:
    """Subscribe to the main queue and process until SIGTERM."""
    queue = await channel.declare_queue(
        QueueTopology.queue,
        durable=True,
        arguments=queue_arguments(),
    )

    async with queue.iterator() as q_iter:
        async for raw in q_iter:
            if shutdown.is_set():
                # Cooperative shutdown — return the message and let the broker
                # redeliver to another worker.
                await raw.nack(requeue=True)
                break
            assert isinstance(raw, AbstractIncomingMessage)
            await _handle_with_logging(runner, raw)


async def _handle_with_logging(runner: WorkerRunner, message: AbstractIncomingMessage) -> None:
    """Wraps process_message with structured logging."""
    request_id = message.message_id or "n/a"
    try:
        outcome = await runner.process_message(message)
        logger.info("worker_message_handled outcome=%s request=%s", outcome.value, request_id)
    except Exception:
        logger.exception("worker_handler_unexpected_error")
        try:
            await message.reject(requeue=False)
        except Exception:  # pragma: no cover - cleanup-best-effort
            pass


# ─── Topology bootstrap ─────────────────────────────────────────────────────


async def _declare_topology(channel: aio_pika.abc.AbstractChannel) -> None:
    """Idempotently declare exchanges + queues + DLQ binding."""
    await channel.declare_exchange(
        QueueTopology.exchange,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    dlx = await channel.declare_exchange(
        QueueTopology.dlx,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    dlq = await channel.declare_queue(QueueTopology.dlq, durable=True)
    await dlq.bind(dlx, routing_key=QueueTopology.dlq_routing_key)

    main = await channel.declare_queue(
        QueueTopology.queue,
        durable=True,
        arguments=queue_arguments(),
    )
    exchange = await channel.get_exchange(QueueTopology.exchange)
    await main.bind(exchange, routing_key=QueueTopology.routing_key)


def _setup_usage_callback() -> object | None:
    """Register the LLM usage callback so the queue path records the cost ledger.

    The standalone worker does not run the FastAPI lifespan, so without this it
    drops every ``llm_usage`` row (including transcription spend). Mirrors the
    app wiring in ``src.main`` (sync mode, reusing the cached Mongo client).
    Returns the callback so the caller can flush it on shutdown, or ``None`` when
    tracking could not be set up — a setup failure must never crash the worker.
    """
    try:
        import litellm
        from llm_common import MongoDBUsageCallback

        from src.dependencies import get_mongo_client

        client = get_mongo_client()
        db = client.get_default_database()
        callback = MongoDBUsageCallback(db, service="summarizer", mode="sync")
        litellm.callbacks = [callback]
        logger.info("worker_llm_usage_callback_registered mode=%s", "sync")
        return callback
    except ImportError:
        logger.warning("worker_llm_common_not_installed usage tracking disabled")
        return None
    except Exception as e:  # noqa: BLE001 - tracking must not crash the worker
        logger.warning("worker_llm_usage_callback_failed error=%s", e)
        return None


def _shutdown_usage_callback(callback: object | None) -> None:
    """Flush the sync usage buffer on worker shutdown — best-effort."""
    if callback is None:
        return
    try:
        callback.shutdown_sync()
    except Exception as e:  # noqa: BLE001 - cleanup best-effort
        logger.warning("worker_llm_usage_callback_shutdown_failed error=%s", e)


def _redact_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.password:
            netloc = f"{parsed.username or ''}:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "amqp://(unparseable)"


async def _preload_embeddings() -> None:
    """Load the SentenceTransformer before the first job, like the API does.

    Otherwise the first job's Qdrant writes pay the model load (A4). Non-fatal:
    embeddings feed RAG, not the pipeline's own output.
    """
    if not settings.QDRANT_ENABLED:
        return
    try:
        from src.services.vector.embedding import _get_model

        await asyncio.to_thread(_get_model)
        logger.info("worker_embeddings_preloaded")
    except Exception as e:  # noqa: BLE001 — a broken model must not keep the worker down
        logger.warning("worker_embeddings_preload_failed", error=str(e))


async def main() -> None:
    """Connect, declare topology, and spin N concurrent consumers."""
    # Sentry first — boot-time exceptions in Langfuse init or aio-pika should
    # surface in Sentry rather than disappearing into journald. Use structlog
    # kwargs so `enabled`/`error` stay queryable per the log-shape contract
    # (services/summarizer/tests/test_logging_shape.py).
    try:
        enabled = init_sentry_for_worker()
        logger.info("worker_sentry_init", enabled=enabled)
    except Exception as e:  # noqa: BLE001
        logger.warning("worker_sentry_init_failed", error=str(e))

    # Initialize Langfuse observability — no-op when keys are unset. Done
    # before any pipeline runs so the first job's spans attach to a live
    # client instead of silently dropping.
    try:
        from src.services.observability import init_langfuse

        client = init_langfuse()
        logger.info("worker_langfuse_init enabled=%s", client is not None)
    except Exception as e:
        logger.warning("worker_langfuse_init_failed error=%s", e)

    # Register the LLM usage callback so the queue path captures the full cost
    # ledger (the worker bypasses the FastAPI lifespan that wires this in app).
    usage_callback = _setup_usage_callback()
    await _preload_embeddings()

    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
    logger.info("worker_connected url=%s", _redact_url(settings.RABBITMQ_URL))

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: shutdown.set())

    async with connection:
        async with connection.channel() as topo_channel:
            await _declare_topology(topo_channel)

        channel_pool: Pool[aio_pika.abc.AbstractChannel] = Pool(
            connection.channel,
            max_size=2,
            loop=loop,
        )
        republisher = _Republisher(channel_pool)
        runner = WorkerRunner(
            run_pipeline=_drive_pipeline,
            republish=republisher,
            max_retries=settings.WORKER_MAX_RETRIES,
            retry_backoff_seconds=settings.WORKER_RETRY_BACKOFF_SECONDS,
        )

        consumers: list[asyncio.Task[None]] = []
        for idx in range(settings.WORKER_CONCURRENCY):
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=settings.WORKER_PREFETCH)
            consumers.append(
                asyncio.create_task(
                    _consume(runner, channel, shutdown),
                    name=f"worker-consumer-{idx}",
                ),
            )
        logger.info("worker_consumers_started count=%d", settings.WORKER_CONCURRENCY)

        heartbeat = asyncio.create_task(_heartbeat_loop(shutdown), name="worker-heartbeat")

        await shutdown.wait()
        logger.info("worker_shutdown_signal_received")
        for c in consumers:
            c.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        # Heartbeat loop exits on its own once the shutdown event is set.
        await heartbeat
        await channel_pool.close()

    # Flush any buffered usage records before exit so the final jobs' cost
    # ledger isn't lost on container stop.
    _shutdown_usage_callback(usage_callback)

    # Drain Langfuse buffer so in-flight spans aren't lost on container stop.
    try:
        from src.services.observability import flush_langfuse

        await flush_langfuse()
    except Exception as e:
        logger.warning("worker_langfuse_flush_failed error=%s", e)

    logger.info("worker_shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
