"""Refusal detection on a LiteLLM chat response — shared by the usage ledger and the services.

A refusal is an HTTP 200 whose answer the model declined to give. How LiteLLM
(1.105.0, verified 2026-10-13) surfaces one:

- Anthropic ``stop_reason: "refusal"`` → ``finish_reason="content_filter"``,
  streamed and non-streamed. The chat-completions path drops ``stop_details``,
  so the category is unknown there; it is read from
  ``provider_specific_fields["stop_details"]`` should a later LiteLLM pass it on.
- OpenAI ``message.refusal`` → ``message.provider_specific_fields["refusal"]``
  (non-streamed). A streamed refusal's deltas are dropped by LiteLLM: the stream
  rebuilds to an empty answer and cannot be told apart from one.
- Any provider's ``content_filter`` finish: the answer was withheld.

Every read is type-checked, so a test double (``MagicMock``) is never a refusal.
"""

from dataclasses import dataclass

_WITHHELD = "content_filter"


@dataclass(frozen=True)
class Refusal:
    """A declined answer; ``category`` is ``stop_details.category`` when it reaches us."""

    category: str | None


def _specific_field(message: object, key: str) -> object:
    fields = getattr(message, "provider_specific_fields", None)
    return fields.get(key) if isinstance(fields, dict) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _category(message: object) -> str | None:
    details = _specific_field(message, "stop_details")
    category = details.get("category") if isinstance(details, dict) else None
    return category if isinstance(category, str) else None


def refusal_of(response: object) -> Refusal | None:
    """The refusal in ``response``'s first choice, or None for an answer (or no choice)."""
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    choice = choices[0]
    message = getattr(choice, "message", None)
    declined = _text(getattr(message, "refusal", None)) or _text(
        _specific_field(message, "refusal")
    )
    if getattr(choice, "finish_reason", None) != _WITHHELD and declined is None:
        return None
    return Refusal(category=_category(message))
