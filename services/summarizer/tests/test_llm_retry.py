"""Tests for LLM retry utility."""

import asyncio
import pytest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from litellm.exceptions import (
    APIError as LitellmAPIError,
    RateLimitError,
    ServiceUnavailableError,
)

from src.services.llm_messages import text_block
from src.utils.llm_retry import call_llm_with_retry, truncate_prompt_if_needed


@pytest.fixture
def mock_llm():
    """Mock LLM service with call_llm method."""
    service = MagicMock()
    service.call_llm = AsyncMock()
    service.call_llm_fast = AsyncMock()
    service.model = "anthropic/claude-sonnet-4-6"
    service.fast_model = "anthropic/claude-haiku-4-5-20251001"
    return service


class TestCallLlmWithRetry:
    """Test call_llm_with_retry function."""

    @pytest.mark.asyncio
    async def test_returns_response_on_success(self, mock_llm):
        mock_llm.call_llm.return_value = '{"key": "value"}'

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            max_tokens=1000,
            timeout=10.0,
            max_retries=2,
            stage_name="test",
        )

        assert result == '{"key": "value"}'
        assert mock_llm.call_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self, mock_llm):
        mock_llm.call_llm.side_effect = [
            asyncio.TimeoutError(),
            '{"success": true}',
        ]

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            timeout=5.0,
            max_retries=1,
            stage_name="test",
        )

        assert result == '{"success": true}'
        assert mock_llm.call_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_exception(self, mock_llm):
        mock_llm.call_llm.side_effect = [
            LitellmAPIError(
                status_code=500, message="API error", llm_provider="anthropic", model="test-model"
            ),
            '{"recovered": true}',
        ]

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            timeout=10.0,
            max_retries=1,
            stage_name="test",
        )

        assert result == '{"recovered": true}'
        assert mock_llm.call_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_empty_response(self, mock_llm):
        mock_llm.call_llm.side_effect = [
            "",
            "   ",
            '{"data": true}',
        ]

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            timeout=10.0,
            max_retries=2,
            stage_name="test",
        )

        assert result == '{"data": true}'
        assert mock_llm.call_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_none_after_all_retries_exhausted(self, mock_llm):
        mock_llm.call_llm.side_effect = asyncio.TimeoutError()

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            timeout=5.0,
            max_retries=2,
            stage_name="test",
        )

        assert result is None
        assert mock_llm.call_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_no_retries_when_max_retries_zero(self, mock_llm):
        mock_llm.call_llm.side_effect = asyncio.TimeoutError()

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            timeout=5.0,
            max_retries=0,
            stage_name="test",
        )

        assert result is None
        assert mock_llm.call_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_passes_correct_params_to_llm(self, mock_llm):
        mock_llm.call_llm.return_value = '{"ok": true}'

        await call_llm_with_retry(
            mock_llm,
            "my prompt",
            max_tokens=8192,
            timeout=30.0,
            max_retries=0,
            stage_name="triage",
        )

        mock_llm.call_llm.assert_called_once_with(
            "my prompt",
            max_tokens=8192,
            timeout=30.0,
            json_mode=False,
            cache_static=None,
            span_name="triage",
            span_metadata={
                "attempt": 1,
                "maxAttempts": 1,
                "useFastModel": False,
                "modelOverride": None,
            },
            temperature=None,
            system_prompt=None,
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_all_empty_responses(self, mock_llm):
        mock_llm.call_llm.return_value = ""

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            timeout=10.0,
            max_retries=1,
            stage_name="test",
        )

        assert result is None
        assert mock_llm.call_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_no_retry(self, mock_llm):
        mock_llm.call_llm.return_value = "response"

        result = await call_llm_with_retry(
            mock_llm,
            "prompt",
            timeout=60.0,
            max_retries=5,
            stage_name="test",
        )

        assert result == "response"
        assert mock_llm.call_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_failure_types(self, mock_llm):
        mock_llm.call_llm.side_effect = [
            asyncio.TimeoutError(),
            LitellmAPIError(
                status_code=429,
                message="rate limited",
                llm_provider="anthropic",
                model="test-model",
            ),
            '{"finally": true}',
        ]

        result = await call_llm_with_retry(
            mock_llm,
            "prompt",
            timeout=5.0,
            max_retries=2,
            stage_name="test",
        )

        assert result == '{"finally": true}'
        assert mock_llm.call_llm.call_count == 3

    @pytest.mark.asyncio
    async def test_use_fast_model_routes_to_call_llm_fast(self, mock_llm):
        mock_llm.call_llm_fast.return_value = '{"fast": true}'

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            max_tokens=1000,
            timeout=10.0,
            max_retries=0,
            stage_name="test",
            use_fast_model=True,
        )

        assert result == '{"fast": true}'
        mock_llm.call_llm_fast.assert_called_once()
        mock_llm.call_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_use_fast_model_false_routes_to_call_llm(self, mock_llm):
        mock_llm.call_llm.return_value = '{"default": true}'

        result = await call_llm_with_retry(
            mock_llm,
            "test prompt",
            max_tokens=1000,
            timeout=10.0,
            max_retries=0,
            stage_name="test",
            use_fast_model=False,
        )

        assert result == '{"default": true}'
        mock_llm.call_llm.assert_called_once()
        mock_llm.call_llm_fast.assert_not_called()

    @pytest.mark.asyncio
    async def test_backoff_schedule_is_linear(self, mock_llm):
        """Backoff grows linearly (1s, 2s, ...) between attempts — the
        docstring must match this schedule (project-score-9 6.1)."""
        mock_llm.call_llm.return_value = ""

        with mock.patch("src.utils.llm_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await call_llm_with_retry(
                mock_llm,
                "test prompt",
                max_tokens=1000,
                timeout=10.0,
                max_retries=2,
                stage_name="test",
            )

        assert result is None
        assert [c.args[0] for c in mock_sleep.await_args_list] == [1.0, 2.0]


class TestPropagateRateLimit:
    """Terminal RateLimitError / ServiceUnavailableError must re-raise when
    ``propagate_rate_limit=True`` so the parallel batch extractor can route
    the batch into its sequential fallback."""

    @pytest.mark.asyncio
    async def test_re_raises_terminal_rate_limit_error(self, mock_llm):
        rate_err = RateLimitError(
            message="429",
            model="anthropic/claude-sonnet-4-6",
            llm_provider="anthropic",
        )
        mock_llm.call_llm.side_effect = rate_err

        with pytest.raises(RateLimitError):
            await call_llm_with_retry(
                mock_llm,
                "prompt",
                max_tokens=100,
                timeout=1.0,
                max_retries=1,
                stage_name="test",
                propagate_rate_limit=True,
            )
        # 1 initial + 1 retry = 2 attempts before re-raising.
        assert mock_llm.call_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_re_raises_terminal_service_unavailable(self, mock_llm):
        svc_err = ServiceUnavailableError(
            message="529",
            model="anthropic/claude-sonnet-4-6",
            llm_provider="anthropic",
        )
        mock_llm.call_llm.side_effect = svc_err

        with pytest.raises(ServiceUnavailableError):
            await call_llm_with_retry(
                mock_llm,
                "prompt",
                max_tokens=100,
                timeout=1.0,
                max_retries=0,
                stage_name="test",
                propagate_rate_limit=True,
            )
        assert mock_llm.call_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_default_returns_none_on_terminal_rate_limit(self, mock_llm):
        """Default contract preserved: callers without the flag still get None."""
        rate_err = RateLimitError(
            message="429",
            model="anthropic/claude-sonnet-4-6",
            llm_provider="anthropic",
        )
        mock_llm.call_llm.side_effect = rate_err

        result = await call_llm_with_retry(
            mock_llm,
            "prompt",
            max_tokens=100,
            timeout=1.0,
            max_retries=0,
            stage_name="test",
        )

        assert result is None
        assert mock_llm.call_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_propagate_flag_recovers_if_retry_succeeds(self, mock_llm):
        """A rate-limit on attempt 1 + success on attempt 2 should NOT raise."""
        rate_err = RateLimitError(
            message="429",
            model="anthropic/claude-sonnet-4-6",
            llm_provider="anthropic",
        )
        mock_llm.call_llm.side_effect = [rate_err, '{"recovered": true}']

        result = await call_llm_with_retry(
            mock_llm,
            "prompt",
            max_tokens=100,
            timeout=1.0,
            max_retries=1,
            stage_name="test",
            propagate_rate_limit=True,
        )

        assert result == '{"recovered": true}'
        assert mock_llm.call_llm.call_count == 2


class TestTruncatePromptIfNeeded:
    def test_short_prompt_unchanged(self):
        prompt = "Short prompt"
        result = truncate_prompt_if_needed(prompt, "anthropic/claude-sonnet-4-6")
        assert result == prompt

    def test_long_prompt_truncated(self):
        prompt = "x" * 700_000
        result = truncate_prompt_if_needed(prompt, "anthropic/claude-sonnet-4-6")
        assert len(result) < 700_000
        assert result.endswith("[TRANSCRIPT TRUNCATED DUE TO LENGTH]")

    def test_unknown_model_uses_default_limit(self):
        prompt = "x" * 400_000
        result = truncate_prompt_if_needed(prompt, "unknown/model")
        assert len(result) < 400_000
        assert result.endswith("[TRANSCRIPT TRUNCATED DUE TO LENGTH]")

    def test_gemini_has_large_limit(self):
        prompt = "x" * 500_000
        result = truncate_prompt_if_needed(prompt, "gemini/gemini-2.5-flash")
        assert result == prompt  # 500K < 3M limit

    def test_short_block_prompt_unchanged(self):
        blocks = [text_block("transcript", cache=True), text_block("job")]
        result = truncate_prompt_if_needed(blocks, "anthropic/claude-sonnet-4-6")
        assert result is blocks

    def test_block_prompt_should_fit_limit_when_longest_block_truncated(self):
        blocks = [text_block("t" * 700_000, cache=True), text_block("job")]
        result = truncate_prompt_if_needed(blocks, "anthropic/claude-sonnet-4-6")
        marker = len("\n\n[TRANSCRIPT TRUNCATED DUE TO LENGTH]")
        assert sum(len(block["text"]) for block in result) == 600_000 + marker

    def test_block_prompt_should_keep_breakpoint_and_tail_when_truncated(self):
        blocks = [text_block("t" * 700_000, cache=True), text_block("job")]
        result = truncate_prompt_if_needed(blocks, "anthropic/claude-sonnet-4-6")
        assert (result[0].get("cache_control"), result[1]) == (
            {"type": "ephemeral"},
            text_block("job"),
        )
