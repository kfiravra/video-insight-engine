"""Pydantic models for LLM usage records."""

from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class UsageRecord(BaseModel):
    """A single LLM API call record stored in MongoDB."""

    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    feature: str = "unknown"
    # Cost unit discriminator. Token-priced LLM calls use the default
    # "tokens"; duration-priced transcription (Whisper) uses "audio_seconds"
    # and carries the billed seconds in ``audio_seconds``. Additive + optional
    # so existing token rows are unchanged and readers that ignore it still work.
    unit: str = "tokens"
    audio_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    success: bool = True
    duration_ms: int = 0
    request_id: str | None = None
    video_id: str | None = None
    user_id: str | None = None
    video_summary_id: str | None = None
    is_stream: bool = False
    service: str = "unknown"
    prompt_preview: str = ""
    prompt_hash: str = ""
    cache_hit: bool = False
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cache_savings_usd: float = 0.0
    litellm_version: str = ""
    error_message: str | None = None
    # A call the model declined (HTTP 200, see ``llm_common.refusal``), and the
    # call that answered for a refused model on its previous model (the refused
    # model's id). Additive: rows written before them read as no refusal.
    refusal: bool = False
    refusal_category: str | None = None
    fallback_from: str | None = None


# Per-model input pricing (USD per 1M tokens) for the cache-savings estimate.
# Cached reads bill at ~10% of normal input; the savings are the delta between
# what we would have paid (input rate) and what was actually billed (cache_read).
_CACHE_RATES_USD_PER_M: dict[str, dict[str, float]] = {
    "anthropic/claude-sonnet-4-6": {"input": 3.00, "cache_read": 0.30},
    "anthropic/claude-sonnet-4-5": {"input": 3.00, "cache_read": 0.30},
    "anthropic/claude-haiku-4-5-20251001": {"input": 1.00, "cache_read": 0.10},
    "anthropic/claude-haiku-4-5": {"input": 1.00, "cache_read": 0.10},
}


_LOGGED_MISSING_RATE_MODELS: set[str] = set()


def compute_cache_savings_usd(model: str, cache_read_tokens: int) -> float:
    """Estimate USD saved by serving cached input tokens.

    Returns 0.0 for unmapped models — better to under-report than to invent
    savings from a guess. When an Anthropic model is missing from the rate
    map (typical signal: a model rename in config that wasn't mirrored here),
    log once per model so the drift is visible without flooding the buffer.
    """
    if cache_read_tokens <= 0:
        return 0.0
    rates = _CACHE_RATES_USD_PER_M.get(model)
    if not rates:
        if model.startswith("anthropic/") and model not in _LOGGED_MISSING_RATE_MODELS:
            _LOGGED_MISSING_RATE_MODELS.add(model)
            logger.info(
                "cache.rate_missing",
                model=model,
                hint="add to llm_common.models._CACHE_RATES_USD_PER_M",
            )
        return 0.0
    delta = rates["input"] - rates["cache_read"]
    return (cache_read_tokens / 1_000_000) * delta


# Transcription pricing (USD). Whisper bills per minute of audio; Gemini
# transcription bills per token. Kept here alongside the cache rates so all
# LLM-adjacent pricing lives in one module. Gemini input uses the audio-input
# rate since a transcription's input is dominated by audio tokens.
_TRANSCRIPTION_RATES_USD: dict[str, dict[str, float]] = {
    "whisper-1": {"per_minute": 0.006},
    "gemini-2.5-flash-lite": {"input_per_m": 0.30, "output_per_m": 0.40},
}


_LOGGED_MISSING_TRANSCRIPTION_MODELS: set[str] = set()


def compute_transcription_cost_usd(
    model: str,
    *,
    audio_seconds: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> float:
    """USD cost of a transcription call.

    Whisper bills per minute of audio (``audio_seconds``); Gemini bills per
    token. Returns 0.0 for an unmapped model and logs once per model so a
    config drift (e.g. a new transcription model) is visible without flooding
    the buffer — mirrors :func:`compute_cache_savings_usd`.
    """
    rates = _TRANSCRIPTION_RATES_USD.get(model)
    if not rates:
        if model not in _LOGGED_MISSING_TRANSCRIPTION_MODELS:
            _LOGGED_MISSING_TRANSCRIPTION_MODELS.add(model)
            logger.info(
                "transcription.rate_missing",
                model=model,
                hint="add to llm_common.models._TRANSCRIPTION_RATES_USD",
            )
        return 0.0
    if "per_minute" in rates:
        return (max(audio_seconds, 0.0) / 60.0) * rates["per_minute"]
    return (max(tokens_in, 0) / 1_000_000) * rates.get("input_per_m", 0.0) + (
        max(tokens_out, 0) / 1_000_000
    ) * rates.get("output_per_m", 0.0)


def extract_provider(model: str) -> str:
    """Extract provider from model string (e.g., 'anthropic/claude-...' -> 'anthropic')."""
    return model.split("/")[0] if "/" in model else "unknown"
