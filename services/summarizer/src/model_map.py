"""Provider model tiers and the refusal fallback table (re-exported by ``src.config``)."""

from __future__ import annotations

# Model mapping for each provider
MODEL_MAP = {
    "anthropic": {
        # The primary model — the plan, at effort medium (4.0d A/B: p50 11.3 vs
        # 23.6 s, tab choice within noise); every other stage is pinned per stage.
        "default": "anthropic/claude-sonnet-5-5",
        "fast": "anthropic/claude-haiku-4-5-20251001",
    },
    "openai": {
        "default": "openai/gpt-4o",
        "fast": "openai/gpt-4o-mini",
    },
    "gemini": {
        "default": "gemini/gemini-2.5-flash",
        "fast": "gemini/gemini-2.5-flash-lite",
    },
}


def get_model(provider: str = "anthropic", tier: str = "default") -> str:
    """Get model name for provider and tier."""
    provider_models = MODEL_MAP.get(provider, MODEL_MAP["anthropic"])
    return provider_models.get(tier, provider_models["default"])


# The model a refusal falls back to (pipeline-1min 4.0b): an answer a model
# declines (stop_reason "refusal") is asked for once more, in the same call, on
# the model it replaced — same provider, never chained. A model missing here
# has no refusal fallback. Haiku 4.5 and Sonnet 4.6 stay configured for this.
PREVIOUS_MODEL: dict[str, str] = {
    "anthropic/claude-haiku-5-5": "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-5-5": "anthropic/claude-sonnet-4-6",
    "openai/gpt-6-luna": "openai/gpt-4o-mini",
}
