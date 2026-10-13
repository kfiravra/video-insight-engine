"""LiteLLM CustomLogger callback that captures all LLM calls to MongoDB.

Usage:
    from llm_common import MongoDBUsageCallback
    import litellm

    # Sync mode (summarizer - pymongo)
    callback = MongoDBUsageCallback(db, service="summarizer", mode="sync")
    litellm.callbacks = [callback]

    # Async mode (explainer - motor)
    callback = MongoDBUsageCallback(db, service="explainer", mode="async")
    await callback.start_async()
    litellm.callbacks = [callback]
"""

import asyncio
import hashlib
import os
from datetime import UTC, datetime

import litellm
import structlog
from litellm.integrations.custom_logger import CustomLogger

from llm_common.alerts import deliver_alert
from llm_common.buffer import AsyncBuffer, SyncBuffer
from llm_common.context import (
    llm_feature_var,
    llm_refused_model_var,
    llm_request_id_var,
    llm_user_id_var,
    llm_video_id_var,
    llm_video_summary_id_var,
)
from llm_common.models import UsageRecord, compute_cache_savings_usd, extract_provider
from llm_common.refusal import refusal_of


def _safe_int(obj: object, attr: str) -> int:
    """Return ``obj.attr`` if it is an int, else 0.

    ``getattr(MagicMock(), "x", 0)`` returns a Mock (not 0) because the
    attribute exists on the mock. Without an explicit isinstance check
    we'd silently leak Mocks into the record under test.
    """
    val = getattr(obj, attr, 0)
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    return 0


def _cache_read_tokens(usage: object) -> int:
    """Prompt-cache reads: Anthropic's ``cache_read_input_tokens``, else OpenAI's
    ``prompt_tokens_details.cached_tokens`` (LiteLLM leaves the former None there)."""
    reads = _safe_int(usage, "cache_read_input_tokens")
    if reads:
        return reads
    return _safe_int(getattr(usage, "prompt_tokens_details", None), "cached_tokens")


def _rate_key(kwargs: dict, model: str) -> str:
    """The cache-rate map's key for ``model``: LiteLLM 1.105 hands the callback the
    bare name ("claude-sonnet-5-5") with the provider apart (``custom_llm_provider``)."""
    if "/" in model:
        return model
    params = kwargs.get("litellm_params")
    provider = kwargs.get("custom_llm_provider") or (
        params.get("custom_llm_provider") if isinstance(params, dict) else None
    )
    return f"{provider}/{model}" if isinstance(provider, str) and provider else model


logger = structlog.get_logger(__name__)

DEFAULT_COST_THRESHOLD = 0.50


def cost_threshold_from_env() -> float:
    """Per-call high-cost alert threshold: ``ALERT_COST_THRESHOLD_USD`` or the default.

    Read at construction (not import) so a service's settings and tests can
    override it. A malformed value falls back to the default with a warning
    rather than breaking LLM usage tracking at boot.
    """
    raw = os.environ.get("ALERT_COST_THRESHOLD_USD", "").strip()
    if not raw:
        return DEFAULT_COST_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        logger.warning("alert_cost_threshold_invalid", value=raw, default=DEFAULT_COST_THRESHOLD)
        return DEFAULT_COST_THRESHOLD
    if value < 0:
        logger.warning("alert_cost_threshold_negative", value=value, default=DEFAULT_COST_THRESHOLD)
        return DEFAULT_COST_THRESHOLD
    return value


# Process-wide handle to the active usage buffer. Lets out-of-band emitters
# (transcription calls provider SDKs directly, bypassing LiteLLM's callback)
# write to the same llm_usage stream. Set by the most-recently-constructed
# MongoDBUsageCallback — each service constructs exactly one.
_active_buffer: SyncBuffer | AsyncBuffer | None = None

# Strong references to in-flight manual-emit tasks. ``create_task`` only keeps a
# weak reference, so without this a fire-and-forget task can be garbage-collected
# before it runs (RUF006). Tasks discard themselves on completion.
_pending_manual_tasks: set[asyncio.Task] = set()


def register_active_buffer(buffer: SyncBuffer | AsyncBuffer) -> None:
    """Register the process-wide usage buffer for manual emits."""
    global _active_buffer
    _active_buffer = buffer


def record_manual_usage(record: UsageRecord) -> None:
    """Write a usage record built outside LiteLLM into the active buffer.

    For costs LiteLLM never sees — Whisper/Gemini transcription call provider
    SDKs directly. Run-attribution fields (request/video/user/video_summary id)
    and an unset feature are filled from the same context vars
    :meth:`MongoDBUsageCallback._build_record` reads, so a manual emit inherits
    the run's attribution automatically. No-op (with a warning) when no buffer
    is registered — e.g. a unit test with no callback. Never raises into the
    caller; tracking must never break the work it tracks.
    """
    buffer = _active_buffer
    if buffer is None:
        logger.warning(
            "manual_usage_no_buffer",
            feature=record.feature,
            model=record.model,
        )
        return
    try:
        if record.feature == "unknown":
            record.feature = llm_feature_var.get()
        if record.request_id is None:
            record.request_id = llm_request_id_var.get()
        if record.video_id is None:
            record.video_id = llm_video_id_var.get()
        if record.user_id is None:
            record.user_id = llm_user_id_var.get()
        if record.video_summary_id is None:
            record.video_summary_id = llm_video_summary_id_var.get()
        result = buffer.add(record.model_dump())
        # AsyncBuffer.add returns a coroutine. Transcription only runs under the
        # sync buffer today, but schedule it on a running loop if present so an
        # async-mode caller doesn't silently drop the record.
        if asyncio.iscoroutine(result):
            try:
                task = asyncio.get_running_loop().create_task(result)
                _pending_manual_tasks.add(task)
                task.add_done_callback(_pending_manual_tasks.discard)
            except RuntimeError:
                result.close()
                logger.warning("manual_usage_async_no_loop", model=record.model)
    except Exception as e:  # noqa: BLE001 — tracking must never break the caller
        logger.error("manual_usage_emit_failed", error=str(e))


class MongoDBUsageCallback(CustomLogger):
    """LiteLLM callback that buffers usage records to MongoDB."""

    def __init__(
        self,
        database,
        service: str = "unknown",
        mode: str = "sync",
        cost_threshold: float | None = None,
    ):
        self._service = service
        self._mode = mode
        self._cost_threshold = (
            cost_threshold if cost_threshold is not None else cost_threshold_from_env()
        )
        self._usage_col = database["llm_usage"]
        self._alerts_col = database["llm_alerts"]

        if mode == "sync":
            self._buffer = SyncBuffer(self._usage_col)
        else:
            self._buffer = AsyncBuffer(self._usage_col)

        # Expose this buffer for manual (non-LiteLLM) emits — see
        # record_manual_usage. The transcription path uses it for Whisper/Gemini.
        register_active_buffer(self._buffer)

    async def start_async(self) -> None:
        """Start the async buffer flush loop. Call in lifespan for async mode."""
        if isinstance(self._buffer, AsyncBuffer):
            await self._buffer.start()

    def shutdown_sync(self) -> None:
        """Flush remaining records. Call on shutdown for sync mode."""
        if isinstance(self._buffer, SyncBuffer):
            self._buffer.shutdown()

    async def shutdown_async(self) -> None:
        """Flush remaining records. Call on shutdown for async mode."""
        if isinstance(self._buffer, AsyncBuffer):
            await self._buffer.shutdown()

    def _build_record(self, kwargs: dict, response_obj, start_time, end_time) -> dict:
        """Build a UsageRecord dict from LiteLLM callback args."""
        try:
            model = kwargs.get("model", "unknown")
            messages = kwargs.get("messages", [])

            # Extract prompt preview
            prompt_text = ""
            if messages:
                last_msg = messages[-1] if isinstance(messages, list) else messages
                if isinstance(last_msg, dict):
                    prompt_text = str(last_msg.get("content", ""))[:200]

            prompt_hash = (
                hashlib.sha256(prompt_text.encode()).hexdigest()[:16] if prompt_text else ""
            )

            # Extract usage from response
            tokens_in = 0
            tokens_out = 0
            cache_creation_tokens = 0
            cache_read_tokens = 0
            if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
                usage = response_obj.usage
                tokens_in = _safe_int(usage, "prompt_tokens")
                tokens_out = _safe_int(usage, "completion_tokens")
                # Anthropic prompt cache fields. Surfaced under these names by
                # both the native Anthropic SDK and LiteLLM's translation.
                cache_creation_tokens = _safe_int(usage, "cache_creation_input_tokens")
                cache_read_tokens = _cache_read_tokens(usage)

            # Calculate cost
            cost = 0.0
            try:
                cost = litellm.completion_cost(completion_response=response_obj) or 0.0
            except Exception as e:
                logger.debug("cost_calculation_failed", model=model, error=str(e))

            # Duration
            duration_ms = 0
            if start_time and end_time:
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

            # Check for streaming
            is_stream = kwargs.get("stream", False)

            # Cache hit: prefer LiteLLM's hidden flag, fall back to a non-zero
            # cache_read_tokens — the latter is the authoritative provider-side
            # signal for Anthropic prompt caching.
            cache_hit = False
            if response_obj and hasattr(response_obj, "_hidden_params"):
                cache_hit = bool(getattr(response_obj._hidden_params, "cache_hit", False))
            if not cache_hit and cache_read_tokens > 0:
                cache_hit = True

            cache_savings_usd = compute_cache_savings_usd(
                _rate_key(kwargs, model), cache_read_tokens
            )
            refusal = refusal_of(response_obj)

            record = UsageRecord(
                model=model,
                provider=extract_provider(model),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                feature=llm_feature_var.get(),
                timestamp=datetime.now(UTC),
                success=True,
                duration_ms=duration_ms,
                request_id=llm_request_id_var.get(),
                video_id=llm_video_id_var.get(),
                user_id=llm_user_id_var.get(),
                video_summary_id=llm_video_summary_id_var.get(),
                is_stream=is_stream,
                service=self._service,
                prompt_preview=prompt_text,
                prompt_hash=prompt_hash,
                cache_hit=cache_hit,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_savings_usd=cache_savings_usd,
                litellm_version=getattr(litellm, "version", ""),
                refusal=refusal is not None,
                refusal_category=refusal.category if refusal is not None else None,
                fallback_from=llm_refused_model_var.get(),
            )
            return record.model_dump()
        except Exception as e:
            logger.error("record_build_failed", error=str(e))
            return UsageRecord(
                model=kwargs.get("model", "unknown"),
                provider=extract_provider(kwargs.get("model", "unknown")),
                service=self._service,
                feature=llm_feature_var.get(),
                request_id=llm_request_id_var.get(),
                video_id=llm_video_id_var.get(),
                user_id=llm_user_id_var.get(),
                video_summary_id=llm_video_summary_id_var.get(),
            ).model_dump()

    def _build_alert(self, record: dict) -> dict:
        """Build alert document from a usage record."""
        return {
            "type": "high_cost_call",
            "cost_usd": record.get("cost_usd", 0),
            "model": record.get("model"),
            "feature": record.get("feature"),
            "service": record.get("service"),
            "threshold": self._cost_threshold,
            "timestamp": datetime.now(UTC),
        }

    def _check_cost_alert_sync(self, record: dict) -> None:
        """Write alert if cost exceeds threshold (sync/pymongo)."""
        cost = record.get("cost_usd", 0)
        if cost > self._cost_threshold:
            logger.warning(
                "high_cost_alert",
                cost_usd=cost,
                model=record.get("model"),
                threshold=self._cost_threshold,
            )
            alert = self._build_alert(record)
            try:
                self._alerts_col.insert_one(alert)
            except Exception as e:
                logger.error("alert_write_failed", error=str(e))
            # Webhook fires even when the Mongo write fails — the whole point
            # of an alert channel is surviving degraded infrastructure.
            deliver_alert(alert)

    async def _check_cost_alert_async(self, record: dict) -> None:
        """Write alert if cost exceeds threshold (async/motor)."""
        cost = record.get("cost_usd", 0)
        if cost > self._cost_threshold:
            logger.warning(
                "high_cost_alert",
                cost_usd=cost,
                model=record.get("model"),
                threshold=self._cost_threshold,
            )
            alert = self._build_alert(record)
            try:
                await self._alerts_col.insert_one(alert)
            except Exception as e:
                logger.error("alert_write_failed", error=str(e))
            # deliver_alert is blocking urllib — keep it off the event loop.
            await asyncio.to_thread(deliver_alert, alert)

    # ── Sync callbacks (used by summarizer) ──

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            record = self._build_record(kwargs, response_obj, start_time, end_time)
            if self._mode == "sync":
                self._buffer.add(record)
                self._check_cost_alert_sync(record)
        except Exception as e:
            logger.error("callback_success_failed", error=str(e))

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            record = self._build_record(kwargs, response_obj, start_time, end_time)
            record["success"] = False
            record["error_message"] = str(kwargs.get("exception", "unknown"))
            if self._mode == "sync":
                self._buffer.add(record)
        except Exception as e:
            logger.error("callback_failure_failed", error=str(e))

    # ── Async callbacks (called by LiteLLM for acompletion()) ──

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            record = self._build_record(kwargs, response_obj, start_time, end_time)
            if self._mode == "async":
                await self._buffer.add(record)
                await self._check_cost_alert_async(record)
            else:
                # Sync buffer add is thread-safe (uses threading.Lock internally).
                self._buffer.add(record)
                # Cost alert uses pymongo sync client — safe to run in thread pool
                # because pymongo MongoClient is thread-safe.
                try:
                    await asyncio.to_thread(self._check_cost_alert_sync, record)
                except Exception as e:
                    logger.error("cost_alert_failed", error=str(e), mode="sync-via-async")
        except Exception as e:
            logger.error("callback_success_failed", error=str(e))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            record = self._build_record(kwargs, response_obj, start_time, end_time)
            record["success"] = False
            record["error_message"] = str(kwargs.get("exception", "unknown"))
            if self._mode == "async":
                await self._buffer.add(record)
            else:
                self._buffer.add(record)
        except Exception as e:
            logger.error("callback_failure_failed", error=str(e))
