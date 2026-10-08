"""Message shaping for LLM requests — where prompt-cache breakpoints go, per provider.

Anthropic caches the prompt prefix up to each ``cache_control`` breakpoint. The
pipeline sets it on one user content block (``text_block(..., cache=True)``);
the system prompt never carries one. Other providers must never see the
marker: OpenAI rejects unknown content keys (LiteLLM strips them, but by mutating
the caller's dicts) and LiteLLM turns it into a billable context-cache resource
for Gemini/Vertex. So every non-Anthropic request leaves here without it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel


class TextBlock(TypedDict):
    """One text part of a message's content list (Anthropic / OpenAI shape)."""

    type: Literal["text"]
    text: str
    cache_control: NotRequired[dict[str, str]]


# A user message is either plain text or a list of content blocks; one block may
# carry the cache breakpoint (see ``text_block``).
UserContent = str | list[TextBlock]


class Message(BaseModel):
    """Chat message for LLM conversation."""

    role: str  # "system", "user", "assistant"
    content: str


def text_block(text: str, *, cache: bool = False) -> TextBlock:
    """A text content block; ``cache=True`` ends an Anthropic cached prefix after it."""
    block: TextBlock = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def is_anthropic_model(model: str) -> bool:
    """Only Anthropic models honour ``cache_control`` breakpoints."""
    return model.startswith("anthropic/")


def build_prompt_messages(
    prompt: UserContent, *, system_prompt: str | None
) -> list[dict[str, Any]]:
    """Messages for ``LLMProvider.complete``: the optional system prompt, then the user content.

    A block-list ``prompt`` is the user content as given, so a breakpoint the
    caller put on one block is kept (Anthropic) or stripped by
    :func:`prepare_for_model` (everyone else).
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def to_message_dicts(messages: Sequence[dict[str, Any] | Message]) -> list[dict[str, Any]]:
    """Plain dicts for LiteLLM, whether the caller passed dicts or ``Message`` objects."""
    return [m.model_dump() if isinstance(m, Message) else m for m in messages]


def _without_cache_control(item: Any) -> Any:
    """Shallow copy of a message or content block minus its ``cache_control`` key."""
    if not isinstance(item, dict):
        return item
    stripped = {key: value for key, value in item.items() if key != "cache_control"}
    content = stripped.get("content")
    if isinstance(content, list):
        stripped["content"] = [_without_cache_control(block) for block in content]
    return stripped


def prepare_for_model(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """The messages to send to ``model``: breakpoints only where they are honoured.

    Anthropic messages pass through untouched. For any other provider every
    ``cache_control`` key (message-level or on a content block) is dropped from
    copies — the caller's messages are never mutated, and messages without a
    breakpoint serialize exactly as before.
    """
    if is_anthropic_model(model):
        return messages
    return [_without_cache_control(message) for message in messages]
