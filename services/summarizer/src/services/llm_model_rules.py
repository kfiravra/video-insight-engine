"""Model-keyed request rules: what each model is sent beyond the prompt (pipeline-1min 4.0b).

Every pipeline request passes :func:`request_for_model` on its way to LiteLLM,
keyed by the model it goes to, never by call site:

- **thinking off + effort.** A model that thinks unless told not to gets its
  own "off" shape and ``output_config.effort`` (default ``low``): Claude Haiku
  5.5 ``{"type": "disabled"}``, Claude Sonnet 5.5 ``{"type": "between_tools"}``
  (the only off shape it accepts; every other model 400s on it). LiteLLM
  1.105.0 passes both through verbatim — it does not know ``between_tools`` and
  drops ``disabled`` for Sonnet 5.5 (``thinking_always_on``), hence the table.
  An OpenAI reasoning model whose map entry supports it gets
  ``reasoning_effort="none"`` (thinking off; ``effort`` is Anthropic's knob).
- **sampling.** ``temperature`` / ``top_p`` / ``top_k`` are dropped (with a
  warning) for a model that rejects them, by LiteLLM's own predicates and on
  the effort the request actually carries: an Anthropic model whose map says
  ``supports_sampling_params: false``; a gpt-5/gpt-6 model unless its effort
  resolves to ``none`` (temperature=1 always passes, as in LiteLLM).
- **no assistant prefill.** A request whose last message is the assistant's is
  refused before LiteLLM (:class:`AssistantPrefillError`): the 5.x models
  reject prefill, and a reply format belongs in ``response_format``.

Today's models (Haiku 4.5, Sonnet 4.6, gpt-4o-mini, gpt-4o) match none of
these rules: their requests stay byte-identical.

The module also gives LiteLLM a Haiku 5.5 map entry when the loaded map lacks
one (1.105.0's bundled map does): LiteLLM keys the thinking / effort /
structured-output mapping, the sampling gate and ``completion_cost`` on it.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import litellm
from litellm.llms.anthropic.common_utils import AnthropicModelInfo
from litellm.llms.openai.chat.gpt_5_transformation import OpenAIGPT5Config

from src.services.llm_messages import is_anthropic_model, prepare_for_model

logger = logging.getLogger(__name__)

# The effort levels every thinking-off shape accepts (Haiku 5.5 400s on
# ``disabled`` at xhigh/max; Sonnet 5.5's ``between_tools`` needs ≤ high).
Effort = Literal["low", "medium", "high"]
DEFAULT_EFFORT: Effort = "low"

_SAMPLING_PARAMS = ("temperature", "top_p", "top_k")


class AssistantPrefillError(ValueError):
    """A request ended with an assistant turn (prefill) — a programming error."""


class LLMRefusalError(Exception):
    """The model declined to answer (stop_reason "refusal") and no fallback answered.

    Not transient: the call is never re-sent to ``model`` (``call_llm_with_retry``
    stops on it and returns None; frame vision counts the batch as failed).
    """

    def __init__(self, model: str, category: str | None) -> None:
        self.model = model
        self.category = category
        super().__init__(f"{model} refused to answer (category={category or 'unknown'})")


# ─── LiteLLM model map ───────────────────────────────────────────────────────

_HAIKU_5_5 = "claude-haiku-5-5"
# Docs prices (context.md "4.0c docs facts"): $0.10 / $0.50 per MTok up to 100K
# prompt tokens, $0.50 / $2.50 above; cache read 0.1x, 5m write 1.25x, 1h write
# 2x. Capability flags as the map's claude-sonnet-5-5, corrected for Haiku 5.5:
# ``disabled`` thinking and forced tool use are accepted.
_HAIKU_5_5_ENTRY: dict[str, Any] = {
    "litellm_provider": "anthropic",
    "mode": "chat",
    "max_input_tokens": 1_000_000,
    "max_output_tokens": 128_000,
    "max_tokens": 128_000,
    "input_cost_per_token": 1e-07,
    "output_cost_per_token": 5e-07,
    "input_cost_per_token_above_100k_tokens": 5e-07,
    "output_cost_per_token_above_100k_tokens": 2.5e-06,
    "cache_read_input_token_cost": 1e-08,
    "cache_read_input_token_cost_above_100k_tokens": 5e-08,
    "cache_creation_input_token_cost": 1.25e-07,
    "cache_creation_input_token_cost_above_100k_tokens": 6.25e-07,
    "cache_creation_input_token_cost_above_1hr": 2e-07,
    "cache_creation_input_token_cost_above_1hr_above_100k_tokens": 1e-06,
    "prompt_cache_min_tokens": 512,
    "supports_prompt_caching": True,
    "supports_adaptive_thinking": True,
    "thinking_always_on": False,
    "supports_reasoning": True,
    "supports_output_config": True,
    "supports_max_reasoning_effort": True,
    "supports_xhigh_reasoning_effort": True,
    "supports_native_structured_output": True,
    "supports_response_schema": True,
    "supports_sampling_params": False,
    "supports_assistant_prefill": False,
    "supports_forced_tool_use": True,
    "supports_function_calling": True,
    "supports_tool_choice": True,
    "supports_vision": True,
    "supports_pdf_input": True,
    "supports_mid_conversation_system": True,
}


def register_missing_model_entries() -> bool:
    """Register the Haiku 5.5 entry when LiteLLM's loaded map lacks it; True when it did.

    A map that has the model (the remote map, a later LiteLLM) wins: nothing is
    registered, and a registration is not replayed over a reloaded map.
    """
    if _HAIKU_5_5 in litellm.model_cost:
        return False
    litellm.register_model({_HAIKU_5_5: dict(_HAIKU_5_5_ENTRY)}, persist_across_reloads=False)
    logger.info("Registered %s in LiteLLM's model map (the loaded map lacks it)", _HAIKU_5_5)
    return True


register_missing_model_entries()


# ─── Thinking off + effort ───────────────────────────────────────────────────

# The thinking-off shape of each Claude model that thinks by default (bare names).
_THINKING_OFF: dict[str, dict[str, str]] = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
}


def _bare(model: str) -> str:
    return model.split("/", 1)[-1]


def _turns_thinking_off_by_effort(model: str) -> bool:
    """An OpenAI gpt-5/gpt-6 reasoning model whose map entry supports effort "none"."""
    return OpenAIGPT5Config.is_model_gpt_5_model(model) and (
        OpenAIGPT5Config._supports_reasoning_effort_level(model, "none")
    )


def _reasoning_params(model: str, effort: Effort) -> dict[str, Any]:
    """Thinking off (and the effort) for ``model``; empty for a model that never thinks unasked."""
    off = _THINKING_OFF.get(_bare(model)) if is_anthropic_model(model) else None
    if off is not None:
        return {"thinking": dict(off), "output_config": {"effort": effort}}
    if _turns_thinking_off_by_effort(model):
        return {"reasoning_effort": "none"}
    return {}


# ─── Sampling ────────────────────────────────────────────────────────────────


def _sampling_rejected(model: str, param: str, value: object, effort: str | None) -> bool:
    """LiteLLM's rule for this model and the effort the request carries (``effort``)."""
    if param == "temperature" and value == 1:
        return False
    if is_anthropic_model(model):
        return not AnthropicModelInfo._supports_sampling_params(_bare(model))
    if not OpenAIGPT5Config.is_model_gpt_5_model(model):
        return False
    supports_none = OpenAIGPT5Config._supports_reasoning_effort_level(model, "none")
    return not (supports_none and OpenAIGPT5Config.effort_resolves_to_none(model, effort))


def _drop_rejected_sampling(kwargs: dict[str, Any]) -> None:
    model = kwargs["model"]
    effort = kwargs.get("reasoning_effort")
    rejected = [
        param
        for param in _SAMPLING_PARAMS
        if param in kwargs and _sampling_rejected(model, param, kwargs[param], effort)
    ]
    for param in rejected:
        logger.warning("Dropping %s=%s: %s rejects it", param, kwargs[param], model)
        del kwargs[param]


# ─── Request ─────────────────────────────────────────────────────────────────


def refuse_prefill(messages: list[dict[str, Any]]) -> None:
    """Raise :class:`AssistantPrefillError` when the last message is the assistant's."""
    if messages and messages[-1].get("role") == "assistant":
        raise AssistantPrefillError(
            "LLM request ends with an assistant turn (prefill): the 5.x models reject it — "
            "ask for the reply format with response_format instead"
        )


def request_for_model(
    request: dict[str, Any], *, temperature: float | None, effort: Effort
) -> dict[str, Any]:
    """``request`` (``model`` set) as sent to its model: a new dict, the caller's is untouched.

    Messages lose the cache breakpoints the model does not honour
    (``prepare_for_model``); the model's thinking-off params are added;
    ``temperature`` is added when set, then every sampling param the model
    rejects is dropped.
    """
    model = request["model"]
    kwargs = {**request, "messages": prepare_for_model(request["messages"], model)}
    refuse_prefill(kwargs["messages"])
    kwargs.update(_reasoning_params(model, effort))
    if temperature is not None:
        kwargs["temperature"] = temperature
    _drop_rejected_sampling(kwargs)
    return kwargs
