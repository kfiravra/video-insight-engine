"""Emit transcription cost to the LLM usage ledger + Langfuse.

Whisper and Gemini call provider SDKs directly, bypassing LiteLLM's
``MongoDBUsageCallback`` — so transcription spend is otherwise invisible
($0 in the ledger, no Langfuse generation). This module is the single place
both providers record a cost row and a generation nested under the active
pipeline trace. Attribution (user/video/run ids) is inherited automatically
from the context vars the surrounding pipeline already set.

The same emit also lands the call in the run's ``pipeline.timing``
(``llmCalls``/``costUsd``, or ``llmFailures``) so timing totals include
transcription. Every emit is best-effort: a tracking failure must never
break transcription.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_common import UsageRecord, record_manual_usage
from llm_common.models import compute_transcription_cost_usd

from src.services.observability import error_status, finish_generation, generation, usage_details
from src.services.pipeline.pipeline_timing import record_transcription_call

logger = logging.getLogger(__name__)


def _failure_name(success: bool, error: BaseException | None) -> str | None:
    """Timing failure label: ``None`` for a success, else the exception class."""
    if success:
        return None
    return type(error).__name__ if error is not None else "TranscriptionError"


def open_transcription_generation(*, provider: str, model: str) -> Any | None:
    """Start the Langfuse generation of a transcription call, just before the call.

    Pass the handle to :func:`emit_transcription_usage`, which finishes it, so
    the generation's start and end are the call's. ``None`` outside a run trace.
    """
    try:
        return generation(f"transcription:{provider}", model=model)
    except Exception as exc:  # noqa: BLE001 — observability is non-critical
        logger.debug("Transcription Langfuse generation not opened: %s", exc)
        return None


def emit_transcription_usage(
    *,
    provider: str,
    model: str,
    feature: str,
    audio_seconds: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    duration_ms: int = 0,
    success: bool = True,
    error: BaseException | None = None,
    handle: Any | None = None,
) -> None:
    """Record a transcription call to ``llm_usage`` and Langfuse.

    Args:
        provider: Vendor label for the cost row (``"openai"`` / ``"google"``).
        model: Pricing key (``"whisper-1"``, ``"gemini-2.5-flash-lite"``).
        feature: ``summarize:transcript:*`` — keeps the row off ``"unknown"``.
        audio_seconds: Billed audio duration (Whisper); 0 for token-priced.
        tokens_in/tokens_out: Token counts (Gemini); 0 for duration-priced.
        duration_ms: Wall-clock latency of the call, for the row + trace.
        success: ``False`` records a failed attempt at $0 (failed calls bill
            nothing) so the run still shows the attempt.
        error: The exception behind a failed attempt (its class name is
            stored on the timing failure row).
        handle: The generation :func:`open_transcription_generation` opened
            before the call; without one a zero-length generation is logged.
    """
    unit = "audio_seconds" if audio_seconds > 0 else "tokens"
    cost = (
        compute_transcription_cost_usd(
            model,
            audio_seconds=audio_seconds,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        if success
        else 0.0
    )

    try:
        record_manual_usage(
            UsageRecord(
                model=model,
                provider=provider,
                feature=feature,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                unit=unit,
                audio_seconds=audio_seconds,
                duration_ms=duration_ms,
                success=success,
            )
        )
    except Exception as exc:  # noqa: BLE001 — ledger emit is non-critical
        logger.debug("Transcription usage ledger emit skipped: %s", exc)

    # A call that failed before its generation opened (e.g. the download) still
    # shows as one failed generation; it then has no duration of its own.
    if handle is None:
        handle = open_transcription_generation(provider=provider, model=model)
    finish_generation(
        handle,
        output="",
        usage=usage_details(tokens_in, tokens_out),
        cost_usd=cost,
        metadata={
            "unit": unit,
            "audioSeconds": round(audio_seconds, 1),
            "latencyMs": duration_ms,
            "success": success,
        },
        level=None if success else "ERROR",
        status_message=error_status(error) if error is not None else None,
    )

    try:
        record_transcription_call(
            feature=feature,
            provider=provider,
            model=model,
            wall_ms=duration_ms,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost,
            error=_failure_name(success, error),
        )
    except Exception as exc:  # noqa: BLE001 — timing is non-critical
        logger.debug("Transcription timing record skipped: %s", exc)
