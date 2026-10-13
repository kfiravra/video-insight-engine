"""Shared types of the model A/B (pipeline-1min 4.0d): call types, arms, requests, results.

A request is a real pipeline request, captured at the provider's
``acompletion`` seam for TODAY's model (whose request rules add nothing, so the
captured kwargs are the model-agnostic request). Each arm re-shapes it for its
own model through 4.0b's ``request_for_model`` — thinking off + effort,
the sampling params it accepts, cache breakpoints it honours — so the bytes
the arms send differ only by what their model implies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.services.llm_model_rules import Effort, request_for_model

HAIKU_45 = "anthropic/claude-haiku-4-5-20251001"
HAIKU_55 = "anthropic/claude-haiku-5-5"
SONNET_46 = "anthropic/claude-sonnet-4-6"
SONNET_55 = "anthropic/claude-sonnet-5-5"
GPT_4O_MINI = "openai/gpt-4o-mini"
GPT_6_LUNA = "openai/gpt-6-luna"

# Call types in report order. Synthesis and chapter_detect are not in the 4.0
# list (they keep today's model) and are never sent.
CALL_TYPES: tuple[str, ...] = (
    "tier_probe",
    "description",
    "plan",
    "memory",
    "extraction",
    "quiz",
    "vision",
    "translation_output",
    "translation_rag",
    "faithfulness",
)

# Arm A = today's model (dev ledger, tasks.md 4.0 "Checked against the code").
TODAY: dict[str, str] = {
    "tier_probe": HAIKU_45,
    "description": GPT_4O_MINI,
    "plan": SONNET_46,
    "memory": HAIKU_45,
    "extraction": HAIKU_45,
    "quiz": GPT_4O_MINI,
    "vision": SONNET_46,
    "translation_output": GPT_4O_MINI,
    "translation_rag": GPT_4O_MINI,
    "faithfulness": GPT_4O_MINI,
}

# Arm B = the 4.0 target (D29).
TARGET: dict[str, str] = {
    "tier_probe": HAIKU_55,
    "description": GPT_6_LUNA,
    "plan": SONNET_55,
    "memory": HAIKU_55,
    "extraction": HAIKU_55,
    "quiz": GPT_6_LUNA,
    "vision": HAIKU_55,
    "translation_output": GPT_6_LUNA,
    "translation_rag": GPT_6_LUNA,
    "faithfulness": GPT_6_LUNA,
}

DEFAULT_EFFORT: Effort = "low"


@dataclass(frozen=True)
class Arm:
    """One side of the A/B for a call type: a model at an effort."""

    name: str
    model: str
    effort: Effort = DEFAULT_EFFORT


def arms_for(call_type: str, *, medium: frozenset[str] = frozenset()) -> tuple[Arm, ...]:
    """A (today) and B (target); vision adds C = Sonnet 5.5; ``medium`` call types add B at medium.

    Kfir's spec allows effort medium only for the plan, on a quality loss; any
    other call type at medium is measured as data for a g4 question, not a pick.
    """
    arms = [Arm("A", TODAY[call_type]), Arm("B", TARGET[call_type])]
    if call_type == "vision":
        arms.append(Arm("C", SONNET_55))
    if call_type in medium:
        arms.append(Arm("B-medium", TARGET[call_type], "medium"))
    return tuple(arms)


@dataclass
class AbRequest:
    """One pipeline request and what its scorer needs.

    ``kwargs`` are the captured ``acompletion`` kwargs (today's model, no
    rules applied); ``expected_output_tokens`` is the recorded (or estimated)
    answer size the dry-run cost estimate uses; ``context`` holds the scorer
    inputs (JSON-safe: it is saved for ``--rescore``).
    """

    request_id: str
    call_type: str
    source: str
    video_id: str
    kwargs: dict[str, Any]
    expected_output_tokens: int
    context: dict[str, Any] = field(default_factory=dict)
    repeats: int = 1


@dataclass
class CallResult:
    """One (request, arm, repeat) call as sent live."""

    request_id: str
    call_type: str
    arm: str
    model: str
    effort: str
    repeat: int
    latency_ms: int
    ttft_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None
    refusal: bool = False
    refusal_category: str | None = None
    raw: str = ""
    error: str | None = None
    # sha256 of the kwargs the arm sent (``request_sha256``): a row whose bytes
    # differ from today's capture is re-sent / flagged stale.
    request_sha256: str | None = None


def provider_of(model: str) -> str:
    return model.split("/", 1)[0]


def request_for_arm(captured: dict[str, Any], arm: Arm) -> dict[str, Any]:
    """``captured`` as ``arm`` sends it: only the model and the params it implies change.

    The arm's model must be on the captured model's provider: a request
    captured for OpenAI has lost its Anthropic cache breakpoints.
    """
    if provider_of(arm.model) != provider_of(str(captured["model"])):
        raise ValueError(f"{arm.model} is not on the provider {captured['model']} was captured for")
    base = {key: value for key, value in captured.items() if key != "temperature"}
    base["model"] = arm.model
    return request_for_model(base, temperature=captured.get("temperature"), effort=arm.effort)


def reshaped_keys(captured: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    """What re-shaping changed besides the model: ``+key`` added or changed, ``-key`` dropped."""
    diff: dict[str, Any] = {}
    for key in sorted((set(captured) | set(sent)) - {"model", "messages"}):
        if key not in sent:
            diff[f"-{key}"] = captured[key]
        elif captured.get(key) != sent[key]:
            diff[f"+{key}"] = sent[key]
    if captured["messages"] != sent["messages"]:
        diff["messages"] = "changed"
    return diff
