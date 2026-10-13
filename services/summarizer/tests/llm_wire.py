"""Offline provider wire for LLM plumbing tests: real LiteLLM, fake HTTP (pipeline-1min 4.0b).

``offline_http`` patches ``httpx.AsyncClient.send`` — the call LiteLLM's
Anthropic handler and the OpenAI SDK both end in — so a request runs through
LiteLLM's own request transformation (the wire body is captured) and its
response through LiteLLM's own parsing (Anthropic JSON / SSE, OpenAI JSON),
with zero network and zero spend. Callers set fake API keys (``set_fake_keys``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from llm_common.context import llm_refused_model_var

ANTHROPIC_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 40,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}
THINKING = {"type": "thinking", "thinking": "THOUGHT", "signature": "sig"}
REFUSAL_DETAILS = {"type": "refusal", "category": "cyber", "explanation": "declined"}

# What a fake provider answers for one request body.
Answer = Callable[[dict[str, Any]], httpx.Response]


def set_fake_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider keys LiteLLM requires before it sends — never real, never sent anywhere."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-offline-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-offline-test")


def text(value: str) -> dict[str, str]:
    return {"type": "text", "text": value}


def anthropic_message(
    blocks: list[dict[str, Any]], *, stop_reason: str = "end_turn", model: str = "claude"
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "msg_offline",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": ANTHROPIC_USAGE,
    }
    if stop_reason == "refusal":
        body["stop_details"] = REFUSAL_DETAILS
    return body


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _block_events(index: int, block: dict[str, Any]) -> list[dict[str, Any]]:
    if block["type"] == "thinking":
        start = {"type": "thinking", "thinking": "", "signature": ""}
        deltas = [
            {"type": "thinking_delta", "thinking": block["thinking"]},
            {"type": "signature_delta", "signature": block["signature"]},
        ]
    else:
        half = len(block["text"]) // 2
        start = {"type": "text", "text": ""}
        deltas = [
            {"type": "text_delta", "text": part}
            for part in (block["text"][:half], block["text"][half:])
        ]
    return [
        {"type": "content_block_start", "index": index, "content_block": start},
        *({"type": "content_block_delta", "index": index, "delta": d} for d in deltas),
        {"type": "content_block_stop", "index": index},
    ]


def anthropic_stream(
    blocks: list[dict[str, Any]], *, stop_reason: str = "end_turn", model: str = "claude"
) -> bytes:
    start = {**anthropic_message([], stop_reason=stop_reason, model=model), "stop_reason": None}
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_reason == "refusal":
        delta["stop_details"] = REFUSAL_DETAILS
    events: list[dict[str, Any]] = [{"type": "message_start", "message": start}]
    for index, block in enumerate(blocks):
        events += _block_events(index, block)
    events.append({"type": "message_delta", "delta": delta, "usage": {"output_tokens": 40}})
    events.append({"type": "message_stop"})
    return _sse(events)


def openai_message(
    content: str | None, *, refusal: str | None = None, model: str = "gpt"
) -> dict[str, Any]:
    message = {"role": "assistant", "content": content, "refusal": refusal}
    return {
        "id": "chatcmpl-offline",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240},
    }


def as_json(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def as_stream(raw: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)


@dataclass
class Wire:
    """Every request body sent, and the refused model the ledger saw during each."""

    bodies: list[dict[str, Any]] = field(default_factory=list)
    refused_model_tags: list[str | None] = field(default_factory=list)

    @property
    def models(self) -> list[str]:
        return [body["model"] for body in self.bodies]


@contextmanager
def offline_http(answer: Answer, *, delays: Mapping[str, float] | None = None) -> Iterator[Wire]:
    """Patch the HTTP send under LiteLLM; ``answer`` builds each response from the body.

    ``delays`` holds a bare model name's seconds before it answers (a slow provider).
    """
    wire = Wire()

    async def send(_client: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        wire.bodies.append(body)
        wire.refused_model_tags.append(llm_refused_model_var.get())
        await asyncio.sleep((delays or {}).get(body["model"], 0.0))
        response = answer(body)
        response.request = request
        return response

    with patch.object(httpx.AsyncClient, "send", send):
        yield wire
