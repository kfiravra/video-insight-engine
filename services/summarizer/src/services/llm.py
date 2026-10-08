"""LLM service for video summarization.

Uses LiteLLM via LLMProvider for multi-provider support (Anthropic, OpenAI, Gemini).
Pipeline modules (triage, extractor, enrichment, synthesis) use call_llm()
for all LLM interactions.
"""

import asyncio
import logging
from typing import Any

from src.config import settings
from src.services.llm_messages import UserContent
from src.services.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class LLMService:
    """Service for LLM-based video processing.

    Uses LLMProvider for multi-provider support (Anthropic, OpenAI, Gemini).
    All API calls are native async via LiteLLM's acompletion().
    """

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    @property
    def provider(self) -> LLMProvider:
        """Get the underlying LLM provider."""
        return self._provider

    @property
    def fast_model(self) -> str:
        """Get the configured fast model from the provider."""
        return self._provider.fast_model

    @property
    def model(self) -> str:
        """Get the configured default model name."""
        return self._provider.model

    @property
    def fallback_model(self) -> str | None:
        """Cross-provider fallback ``call_llm_with_retry`` may route to, if any."""
        return self._provider.fallback_model

    async def call_llm_fast(
        self,
        prompt: UserContent,
        max_tokens: int = 4096,
        timeout: float | None = None,
        json_mode: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Make an async LLM call using the fast model.

        Args:
            prompt: The prompt to send (string or content blocks, see ``LLMProvider.complete``)
            max_tokens: Maximum tokens in response
            timeout: Per-call timeout override (seconds). Falls back to 15s default.
            json_mode: When True, request JSON-only output from the model.
            span_name: When non-None, record the call as a Langfuse generation
                span. Best-effort — observability failures are swallowed.
            span_metadata: Extra metadata merged into the generation span.
            temperature: Sampling temperature; ``None`` sends none (provider default).
            system_prompt: Optional system prompt (never carries a cache breakpoint).

        Returns:
            Generated text content
        """
        effective_timeout = timeout if timeout is not None else 15.0
        async with asyncio.timeout(effective_timeout):
            return await self._provider.complete_fast(
                prompt,
                max_tokens=max_tokens,
                timeout=effective_timeout,
                json_mode=json_mode,
                span_name=span_name,
                span_metadata=span_metadata,
                temperature=temperature,
                system_prompt=system_prompt,
            )

    async def call_llm(
        self,
        prompt: UserContent,
        max_tokens: int = 2000,
        timeout: float | None = None,
        json_mode: bool = False,
        span_name: str | None = None,
        span_metadata: dict[str, Any] | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Make an async LLM call.

        Args:
            prompt: The prompt to send (string or content blocks, see ``LLMProvider.complete``)
            max_tokens: Maximum tokens in response
            timeout: Per-call timeout override (seconds). Falls back to LLM_TIMEOUT_SECONDS.
            json_mode: When True, request JSON-only output from the model.
            span_name: When non-None, record the call as a Langfuse generation
                span. Best-effort — observability failures are swallowed.
            span_metadata: Extra metadata merged into the generation span.
            temperature: Sampling temperature; ``None`` sends none (provider default).
            system_prompt: Optional system prompt (never carries a cache breakpoint).

        Returns:
            Generated text content

        Raises:
            TimeoutError: If LLM call exceeds timeout
        """
        effective_timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS
        async with asyncio.timeout(effective_timeout):
            return await self._provider.complete(
                prompt,
                max_tokens=max_tokens,
                timeout=effective_timeout,
                json_mode=json_mode,
                span_name=span_name,
                span_metadata=span_metadata,
                temperature=temperature,
                system_prompt=system_prompt,
            )
