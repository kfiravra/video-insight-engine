"""The assistant's LLM usage callback never stores chat prompt text (docs/PRIVACY.md)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import bootstrap


@pytest.mark.asyncio
async def test_register_usage_callback_should_disable_prompt_preview_storage():
    with (
        patch("llm_common.MongoDBUsageCallback") as callback_cls,
        patch("litellm.callbacks", []),
    ):
        callback_cls.return_value.start_async = AsyncMock()

        await bootstrap._register_usage_callback(MagicMock())  # noqa: SLF001

    callback_cls.assert_called_once()
    assert callback_cls.call_args.kwargs["store_prompt_preview"] is False


@pytest.mark.asyncio
async def test_register_usage_callback_should_tag_rows_as_assistant_in_async_mode():
    with (
        patch("llm_common.MongoDBUsageCallback") as callback_cls,
        patch("litellm.callbacks", []),
    ):
        callback_cls.return_value.start_async = AsyncMock()

        await bootstrap._register_usage_callback(MagicMock())  # noqa: SLF001

    assert callback_cls.call_args.kwargs["service"] == "assistant"
    assert callback_cls.call_args.kwargs["mode"] == "async"


@pytest.mark.asyncio
async def test_register_usage_callback_should_return_none_when_registration_fails():
    with patch("llm_common.MongoDBUsageCallback", side_effect=RuntimeError("boom")):
        result = await bootstrap._register_usage_callback(MagicMock())  # noqa: SLF001

    assert result is None


@pytest.mark.asyncio
async def test_register_usage_callback_should_record_an_empty_preview():
    from datetime import UTC, datetime

    with patch("litellm.callbacks", []):
        callback = await bootstrap._register_usage_callback(MagicMock())  # noqa: SLF001

    assert callback is not None
    kwargs = {"model": "anthropic/claude-sonnet-4-6", "messages": [{"role": "user", "content": "private question"}]}
    with patch("llm_common.callback.litellm") as mock_litellm:
        mock_litellm.completion_cost.return_value = 0.0
        mock_litellm.version = "1.105.0"
        record = callback._build_record(  # noqa: SLF001
            kwargs, MagicMock(), datetime.now(UTC), datetime.now(UTC)
        )

    await callback.shutdown_async()
    assert record["prompt_preview"] == ""
