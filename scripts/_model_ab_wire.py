"""Offline wire check of the model A/B (pipeline-1min 4.0d): the live send path, zero network.

Each (call type, arm) sends one request through ``_model_ab_runner.send`` —
4.0b's request rules, LiteLLM's own request transformation, the stream path
for a streamed request, ``completion_cost`` and refusal detection on the
answer — with the HTTP layer answered by ``tests/llm_wire.offline_http`` (fake
keys, canned ``{}`` answers). The captured wire body is what the provider
would receive; :func:`wire_findings` flags a param its model rejects (4.0c):
sampling or ``budget_tokens`` on a 5.5 model, a forced ``tool_choice`` on
Sonnet 5.5, an assistant prefill.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import httpx
from _model_ab_cost import PlannedCall
from _model_ab_runner import send
from _model_ab_types import HAIKU_55, SONNET_55, provider_of
from tests.llm_wire import (
    anthropic_message,
    anthropic_stream,
    as_json,
    as_stream,
    offline_http,
    openai_message,
    text,
)

# The wire body keys the check reports (the prompt itself is left out).
WIRE_KEYS: tuple[str, ...] = (
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "top_k",
    "thinking",
    "output_config",
    "reasoning_effort",
    "response_format",
    "tool_choice",
    "stream",
)
_NEW_CLAUDE = frozenset({HAIKU_55.split("/")[-1], SONNET_55.split("/")[-1]})
_FORCED_TOOL = frozenset({"any", "tool"})
_FAKE_KEYS = {"ANTHROPIC_API_KEY": "sk-ant-offline-ab", "OPENAI_API_KEY": "sk-offline-ab"}


@contextmanager
def _fake_keys() -> Iterator[None]:
    """Fake provider keys for the offline send (the HTTP layer is patched; nothing leaves)."""
    saved = {key: os.environ.get(key) for key in _FAKE_KEYS}
    os.environ.update(_FAKE_KEYS)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _answer(body: dict[str, Any]) -> httpx.Response:
    model = str(body.get("model", ""))
    if "claude" not in model:
        return as_json(openai_message("{}", model=model))
    if body.get("stream"):
        return as_stream(anthropic_stream([text("{}")], model=model))
    return as_json(anthropic_message([text("{}")], model=model))


def wire_findings(model: str, body: dict[str, Any]) -> list[str]:
    """What in ``body`` the arm's model would reject (empty = clean)."""
    bare = model.split("/")[-1]
    problems: list[str] = []
    if bare in _NEW_CLAUDE:
        problems += [f"sends {k}" for k in ("top_p", "top_k") if k in body]
        if "temperature" in body and body["temperature"] != 1:
            problems.append(f"sends temperature={body['temperature']}")
        if (body.get("thinking") or {}).get("budget_tokens"):
            problems.append("thinking budget_tokens")
    if (
        bare == SONNET_55.split("/")[-1]
        and (body.get("tool_choice") or {}).get("type") in _FORCED_TOOL
    ):
        problems.append("forced tool_choice")
    messages = body.get("messages") or []
    if messages and messages[-1].get("role") == "assistant":
        problems.append("assistant prefill")
    return problems


async def _wire_one(planned: PlannedCall) -> dict[str, Any]:
    with offline_http(_answer) as wire:
        result = await send(planned)
    body = wire.bodies[-1] if wire.bodies else {}
    model = planned.arm.model
    return {
        "call_type": planned.request.call_type,
        "arm": planned.arm.name,
        "model": model,
        "provider": provider_of(model),
        "wire": {key: body[key] for key in WIRE_KEYS if key in body},
        "problems": wire_findings(model, body) if body else ["no request sent"],
        "error": result.error,
    }


async def _wire_all(planned: Sequence[PlannedCall]) -> list[dict[str, Any]]:
    return [await _wire_one(call) for call in planned]


def wire_check(planned: Sequence[PlannedCall]) -> list[dict[str, Any]]:
    """The wire body summary of each planned call, sent offline in turn (one event loop)."""
    with _fake_keys():
        return asyncio.run(_wire_all(planned))
