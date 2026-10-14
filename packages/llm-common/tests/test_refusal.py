"""Tests for llm_common.refusal and the ledger's refusal tags (summarizer 4.0b)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from llm_common.callback import MongoDBUsageCallback
from llm_common.context import llm_refused_model_var
from llm_common.refusal import Refusal, refusal_of


def _response(finish_reason="stop", content="ok", refusal=None, fields=None):
    """A LiteLLM-shaped chat response (``choices[0].message``)."""
    message = SimpleNamespace(content=content, refusal=refusal, provider_specific_fields=fields)
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=message)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
    )


class TestRefusalOf:
    def test_should_find_a_refusal_when_anthropic_stops_for_one(self):
        # LiteLLM maps stop_reason "refusal" to finish_reason "content_filter".
        assert refusal_of(_response(finish_reason="content_filter", content="partial")) == Refusal(
            category=None
        )

    def test_should_find_a_refusal_when_openai_puts_it_in_the_provider_fields(self):
        response = _response(content=None, fields={"refusal": "I can't help with that."})

        assert refusal_of(response) == Refusal(category=None)

    def test_should_find_a_refusal_when_the_message_carries_one(self):
        assert refusal_of(_response(content=None, refusal="No.")) == Refusal(category=None)

    def test_should_read_the_category_when_stop_details_reach_the_response(self):
        fields = {"stop_details": {"type": "refusal", "category": "cyber"}}
        response = _response(finish_reason="content_filter", fields=fields)

        assert refusal_of(response) == Refusal(category="cyber")

    def test_should_find_no_refusal_when_the_model_answered(self):
        assert refusal_of(_response(fields={"refusal": None})) is None

    def test_should_find_no_refusal_when_the_response_has_no_choice(self):
        assert refusal_of(SimpleNamespace(choices=[])) is None

    def test_should_find_no_refusal_when_the_response_is_a_test_double(self):
        response = MagicMock()
        response.choices[0].finish_reason = "stop"

        assert refusal_of(response) is None


def _record(response, refused_model=None):
    callback = MongoDBUsageCallback(MagicMock(), service="summarizer", mode="sync")
    token = llm_refused_model_var.set(refused_model)
    try:
        return callback._build_record(
            {"model": "anthropic/claude-haiku-5-5", "messages": []},
            response,
            datetime.now(UTC),
            datetime.now(UTC),
        )
    finally:
        llm_refused_model_var.reset(token)


class TestLedgerRefusalTags:
    def test_should_tag_the_row_when_the_model_refused(self):
        record = _record(_response(finish_reason="content_filter"))

        assert (record["refusal"], record["refusal_category"]) == (True, None)

    def test_should_tag_the_fallback_row_with_the_refused_model(self):
        record = _record(_response(), refused_model="anthropic/claude-haiku-5-5")

        assert (record["refusal"], record["fallback_from"]) == (False, "anthropic/claude-haiku-5-5")

    def test_should_leave_the_tags_unset_when_an_ordinary_call_answered(self):
        record = _record(_response())

        assert (record["refusal"], record["refusal_category"], record["fallback_from"]) == (
            False,
            None,
            None,
        )
