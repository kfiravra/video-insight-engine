"""Tests for LLMService with LiteLLM multi-provider support."""

import pytest
from unittest.mock import AsyncMock

from src.services.llm import LLMService


class TestLLMService:
    """Tests for LLMService class with LLMProvider."""

    def test_init(self, mock_llm_provider):
        """Test service initialization with LLMProvider."""
        service = LLMService(mock_llm_provider)
        assert service._provider is mock_llm_provider

    def test_provider_property(self, mock_llm_provider):
        """Test provider property returns the underlying provider."""
        service = LLMService(mock_llm_provider)
        assert service.provider is mock_llm_provider

    def test_fast_model_property(self, mock_llm_provider):
        """Test fast_model property returns provider's fast model."""
        mock_llm_provider.fast_model = "claude-3-haiku"
        service = LLMService(mock_llm_provider)
        assert service.fast_model == "claude-3-haiku"

    @pytest.mark.asyncio
    async def test_call_llm_success(self, mock_llm_provider):
        """Test successful LLM call."""
        mock_llm_provider.complete = AsyncMock(return_value="Hello world")
        service = LLMService(mock_llm_provider)
        result = await service.call_llm("test prompt")
        assert result == "Hello world"
        mock_llm_provider.complete.assert_called_once_with(
            "test prompt",
            max_tokens=2000,
            timeout=60.0,
            json_mode=False,
            cache_static=None,
            span_name=None,
            span_metadata=None,
            temperature=None,
            system_prompt=None,
        )

    @pytest.mark.asyncio
    async def test_call_llm_custom_max_tokens(self, mock_llm_provider):
        """Test LLM call with custom max_tokens."""
        mock_llm_provider.complete = AsyncMock(return_value="response")
        service = LLMService(mock_llm_provider)
        await service.call_llm("prompt", max_tokens=4096)
        mock_llm_provider.complete.assert_called_once_with(
            "prompt",
            max_tokens=4096,
            timeout=60.0,
            json_mode=False,
            cache_static=None,
            span_name=None,
            span_metadata=None,
            temperature=None,
            system_prompt=None,
        )
