"""Tests for LLMProvider multi-provider abstraction."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.llm_provider import (
    LLMProvider,
    Message,
    get_llm_provider,
)


class TestMessage:
    """Tests for Message model."""

    def test_message_creation(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_system_role(self):
        msg = Message(role="system", content="You are helpful")
        assert msg.role == "system"


class TestLLMProvider:
    """Tests for LLMProvider class."""

    def test_init_default_model(self):
        """Test initialization with default model."""
        with patch("src.services.llm_provider.settings") as mock_settings:
            mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
            mock_settings.llm_fallback_models = None
            mock_settings.LLM_TIMEOUT_SECONDS = 60.0

            provider = LLMProvider()
            assert provider.model == "anthropic/claude-sonnet-4-6"

    def test_init_custom_model(self):
        """Test initialization with custom model."""
        with patch("src.services.llm_provider.settings") as mock_settings:
            mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
            mock_settings.llm_fallback_models = None
            mock_settings.LLM_TIMEOUT_SECONDS = 60.0

            provider = LLMProvider(model="openai/gpt-4o")
            assert provider.model == "openai/gpt-4o"

    def test_extract_provider(self):
        """Test provider extraction from model string."""
        with patch("src.services.llm_provider.settings") as mock_settings:
            mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
            mock_settings.llm_fallback_models = None
            mock_settings.LLM_TIMEOUT_SECONDS = 60.0

            provider = LLMProvider()
            assert provider._extract_provider("anthropic/claude-sonnet-4-6") == "anthropic"
            assert provider._extract_provider("openai/gpt-4o") == "openai"
            assert provider._extract_provider("gemini/gemini-1.5-pro") == "gemini"
            assert provider._extract_provider("no-slash") == "unknown"

    @pytest.mark.asyncio
    async def test_complete_success(self):
        """Test successful completion."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Hello world"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                result = await provider.complete("Test prompt")

                assert result == "Hello world"
                mock_acompletion.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_with_messages(self):
        """Test completion with message list."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Response"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                messages = [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "Hello"},
                ]
                result = await provider.complete_with_messages(messages)

                assert result == "Response"

    @pytest.mark.asyncio
    async def test_complete_with_message_objects(self):
        """Test completion with Message objects."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Response"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                messages = [
                    Message(role="system", content="You are helpful"),
                    Message(role="user", content="Hello"),
                ]
                result = await provider.complete_with_messages(messages)

                assert result == "Response"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("use_fast_model", [False, True])
    async def test_should_never_send_litellm_fallbacks_when_fallback_configured(
        self, use_fast_model
    ):
        """D4: the cross-provider fallback lives in call_llm_with_retry, so
        LiteLLM never swaps models inside one request."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Response"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fast_model = "anthropic/claude-haiku-4-5-20251001"
                mock_settings.llm_fallback_models = ["openai/gpt-4o"]
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                await provider.complete_with_messages(
                    [{"role": "user", "content": "hi"}], use_fast_model=use_fast_model
                )

                assert "fallbacks" not in mock_acompletion.call_args.kwargs

    def test_should_expose_first_fallback_model_for_the_retry_wrapper(self):
        provider = LLMProvider(
            model="anthropic/claude-sonnet-4-6", fallback_models=["openai/gpt-4o", "gemini/x"]
        )

        assert provider.fallback_model == "openai/gpt-4o"


class TestUseFastModel:
    """Tests for the ``use_fast_model`` switch on complete_with_messages."""

    @pytest.mark.asyncio
    async def test_use_fast_model_routes_to_fast_model(self):
        """When use_fast_model=True, the call must target the fast model
        instead of the primary."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "haiku-response"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fast_model = "anthropic/claude-haiku-4-5-20251001"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                messages = [{"role": "user", "content": "hi"}]
                result = await provider.complete_with_messages(messages, use_fast_model=True)

                assert result == "haiku-response"
                call_kwargs = mock_acompletion.call_args.kwargs
                assert call_kwargs["model"] == "anthropic/claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_use_fast_model_default_false_keeps_primary(self):
        """Default behavior (omit use_fast_model) must keep the primary model."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "sonnet-response"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fast_model = "anthropic/claude-haiku-4-5-20251001"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                messages = [{"role": "user", "content": "hi"}]
                await provider.complete_with_messages(messages)

                call_kwargs = mock_acompletion.call_args.kwargs
                assert call_kwargs["model"] == "anthropic/claude-sonnet-4-6"


class TestGetLLMProvider:
    """Tests for get_llm_provider factory function."""

    def test_get_llm_provider_singleton(self):
        """Test that get_llm_provider returns cached instance."""
        # Reset global
        import src.services.llm_provider as llm_module

        llm_module._default_provider = None

        with patch("src.services.llm_provider.settings") as mock_settings:
            mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
            mock_settings.llm_fallback_models = None
            mock_settings.LLM_TIMEOUT_SECONDS = 60.0

            provider1 = get_llm_provider()
            provider2 = get_llm_provider()

            assert provider1 is provider2


class TestPromptCaching:
    """Tests for Anthropic prompt cache plumbing — the static extraction
    prompt must reach the wire as a system-message block with cache_control.
    Without this contract, the extractor pays full token cost for every
    batch instead of getting the documented ~50% input savings.
    """

    @pytest.mark.asyncio
    async def test_anthropic_model_emits_system_block_with_cache_control(self):
        """Sonnet calls with cache_static must produce a system message
        containing cache_control: ephemeral."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "ok"
            mock_response.choices[0].finish_reason = "stop"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "anthropic/claude-sonnet-4-6"
                mock_settings.llm_fast_model = "anthropic/claude-haiku-4-5-20251001"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                await provider.complete(
                    "dynamic prompt body",
                    cache_static="STATIC SCHEMA + RULES — cacheable prefix",
                )

                kwargs = mock_acompletion.call_args.kwargs
                messages = kwargs["messages"]

                system_msgs = [m for m in messages if m.get("role") == "system"]
                assert system_msgs, "Anthropic call with cache_static must emit a system message"

                content = system_msgs[0]["content"]
                # The system content must be a list of blocks, with cache_control
                # set on the static prefix block (Anthropic prompt-cache contract).
                assert isinstance(content, list)
                assert content[0]["cache_control"] == {"type": "ephemeral"}
                assert "STATIC SCHEMA" in content[0]["text"]

                user_msgs = [m for m in messages if m.get("role") == "user"]
                assert user_msgs and user_msgs[-1]["content"] == "dynamic prompt body"

    @pytest.mark.asyncio
    async def test_non_anthropic_model_inlines_cache_static(self):
        """Non-Anthropic providers don't support prompt caching — the
        static prefix is concatenated into the user message so the call
        still works (just without cache savings)."""
        with patch("src.services.llm_provider.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "ok"
            mock_response.choices[0].finish_reason = "stop"
            mock_acompletion.return_value = mock_response

            with patch("src.services.llm_provider.settings") as mock_settings:
                mock_settings.llm_model = "openai/gpt-4o-mini"
                mock_settings.llm_fast_model = "openai/gpt-4o-mini"
                mock_settings.llm_fallback_models = None
                mock_settings.LLM_TIMEOUT_SECONDS = 60.0

                provider = LLMProvider()
                await provider.complete("dyn", cache_static="STATIC PREFIX")

                kwargs = mock_acompletion.call_args.kwargs
                messages = kwargs["messages"]
                # Single user message with the static prefix concatenated
                assert all(m.get("role") != "system" for m in messages)
                assert "STATIC PREFIX" in messages[-1]["content"]
                assert "dyn" in messages[-1]["content"]
