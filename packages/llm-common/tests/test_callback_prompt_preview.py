"""store_prompt_preview: chat services keep prompt text out of llm_usage (docs/PRIVACY.md)."""

import hashlib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from llm_common.callback import MongoDBUsageCallback

PROMPT = "What did the speaker say about my medical results?"


def _record(callback: MongoDBUsageCallback, prompt: str = PROMPT) -> dict:
    kwargs = {
        "model": "anthropic/claude-sonnet-4-6",
        "messages": [{"role": "user", "content": prompt}],
    }
    with patch("llm_common.callback.litellm") as mock_litellm:
        mock_litellm.completion_cost.return_value = 0.01
        mock_litellm.version = "1.105.0"
        return callback._build_record(kwargs, MagicMock(), datetime.now(UTC), datetime.now(UTC))


class TestStorePromptPreview:
    def test_should_store_the_preview_by_default(self) -> None:
        cb = MongoDBUsageCallback(MagicMock(), service="summarizer", mode="sync")

        assert _record(cb)["prompt_preview"] == PROMPT

    def test_should_store_an_empty_preview_when_disabled(self) -> None:
        cb = MongoDBUsageCallback(
            MagicMock(), service="assistant", mode="async", store_prompt_preview=False
        )

        assert _record(cb)["prompt_preview"] == ""

    def test_should_keep_the_prompt_hash_when_disabled(self) -> None:
        cb = MongoDBUsageCallback(
            MagicMock(), service="assistant", mode="async", store_prompt_preview=False
        )

        expected = hashlib.sha256(PROMPT.encode()).hexdigest()[:16]
        assert _record(cb)["prompt_hash"] == expected

    def test_should_not_change_the_hash_whether_or_not_the_preview_is_stored(self) -> None:
        stored = MongoDBUsageCallback(MagicMock(), service="summarizer", mode="sync")
        hidden = MongoDBUsageCallback(
            MagicMock(), service="assistant", mode="async", store_prompt_preview=False
        )

        assert _record(stored)["prompt_hash"] == _record(hidden)["prompt_hash"]

    def test_should_not_leak_the_prompt_into_any_other_field_when_disabled(self) -> None:
        cb = MongoDBUsageCallback(
            MagicMock(), service="assistant", mode="async", store_prompt_preview=False
        )

        record = _record(cb)

        assert not any(isinstance(value, str) and PROMPT[:20] in value for value in record.values())
