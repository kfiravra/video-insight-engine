"""Token counts and the dry-run cost estimate of the model A/B (pipeline-1min 4.0d).

Counts are cl100k tokens of the request as each arm sends it (text blocks;
images at Claude's pixel rate) scaled by the arm model's tokenizer factor: the
5.5 models' tokenizer counts ≈ 30 % more (context.md "4.0c docs facts").
Prices are LiteLLM's model map entries — the ledger's ``completion_cost``
source — with Haiku 5.5's above-100K tier applied per request.

The estimate walks the run in the live order (:func:`run_order`) and models
Anthropic's prompt cache: each ``cache_control`` breakpoint closes a segment;
a segment's prefix is written once per model (5-minute or 1-hour rate) and
read after, when the prefix reaches the model's minimum cacheable length.
OpenAI's automatic caching is not credited (the estimate stays an upper bound).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import litellm
import tiktoken
from _model_ab_frames import data_uri_tokens
from _model_ab_types import (
    HAIKU_55,
    SONNET_55,
    AbRequest,
    Arm,
    arms_for,
    provider_of,
    request_for_arm,
)

SPEND_CEILING_USD = 1.50
TOKENIZER_FACTOR: dict[str, float] = {HAIKU_55: 1.3, SONNET_55: 1.3}
# 3.7's chunk ceiling (cl100k tokens of transcript) and Haiku 5.5's price tier.
LONGEST_CHUNK_CL100K = 50_000
HAIKU_55_PRICE_TIER = 100_000
_TIER_SUFFIX = "_above_100k_tokens"
_IMAGE_FALLBACK_TOKENS = 1_600
_ENCODING = tiktoken.get_encoding("cl100k_base")


# ─── Run order (shared by the estimate and the live run) ─────────────────────


@dataclass(frozen=True)
class PlannedCall:
    """One live call: a request, the arm that sends it and which repeat it is."""

    request: AbRequest
    arm: Arm
    repeat: int


def _groups(requests: Sequence[AbRequest]) -> list[list[AbRequest]]:
    by_key: dict[tuple[str, str], list[AbRequest]] = {}
    for request in requests:
        by_key.setdefault((request.source, request.video_id), []).append(request)
    return list(by_key.values())


def _video_calls(
    group: list[AbRequest], index: int, medium: frozenset[str]
) -> Iterator[PlannedCall]:
    arms = {r.request_id: arms_for(r.call_type, medium=medium) for r in group}
    names = sorted({arm.name for found in arms.values() for arm in found})
    order = names if index % 2 == 0 else names[::-1]
    for repeat in range(max(r.repeats for r in group)):
        for name in order:
            for request in (r for r in group if repeat < r.repeats):
                arm = next((a for a in arms[request.request_id] if a.name == name), None)
                if arm is not None:
                    yield PlannedCall(request, arm, repeat)


def run_order(
    requests: Sequence[AbRequest], *, medium: frozenset[str] = frozenset()
) -> Iterator[PlannedCall]:
    """Per video: every repeat, then arm by arm (order alternating per video), call order.

    One arm runs a video's requests back to back, as the pipeline does, so its
    cache reads (extraction groups after text1) happen as they would.
    """
    for index, group in enumerate(_groups(requests)):
        yield from _video_calls(group, index, medium)


# ─── Token counts ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Segment:
    """Prompt tokens up to a cache breakpoint (``ttl`` set) or after the last one."""

    tokens: int
    ttl: str | None
    prefix_key: str


def text_tokens(text: str) -> int:
    return len(_ENCODING.encode(text, disallowed_special=()))


def _block_tokens(block: Any) -> int:
    if isinstance(block, str):
        return text_tokens(block)
    if block.get("type") == "image_url":
        url = (block.get("image_url") or {}).get("url", "")
        return data_uri_tokens(url) if url.startswith("data:") else _IMAGE_FALLBACK_TOKENS
    return text_tokens(str(block.get("text", "")))


def _blocks(kwargs: dict[str, Any]) -> Iterator[Any]:
    for message in kwargs["messages"]:
        content = message.get("content")
        yield from content if isinstance(content, list) else [content or ""]


def request_segments(kwargs: dict[str, Any]) -> list[Segment]:
    """The request's prompt split at its cache breakpoints, in send order."""
    segments: list[Segment] = []
    digest = hashlib.sha256()
    tokens = 0
    for block in _blocks(kwargs):
        digest.update(json.dumps(block, sort_keys=True, ensure_ascii=False).encode())
        tokens += _block_tokens(block)
        control = block.get("cache_control") if isinstance(block, dict) else None
        if control:
            segments.append(Segment(tokens, str(control.get("ttl", "5m")), digest.hexdigest()))
            tokens = 0
    segments.append(Segment(tokens, None, digest.hexdigest()))
    return segments


def prompt_tokens(kwargs: dict[str, Any]) -> int:
    """cl100k prompt tokens of ``kwargs`` (images at Claude's rate)."""
    return sum(segment.tokens for segment in request_segments(kwargs))


def model_tokens(cl100k: int, model: str) -> int:
    """``cl100k`` tokens as ``model``'s tokenizer counts them (estimate)."""
    return round(cl100k * TOKENIZER_FACTOR.get(model, 1.0))


# ─── Prices ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Prices:
    """USD per token for one request (its tier picked by its prompt size)."""

    input: float
    output: float
    cache_read: float
    write_5m: float
    write_1h: float
    min_cacheable: int


def _map_entry(model: str) -> dict[str, Any]:
    bare = model.split("/", 1)[-1]
    entry = litellm.model_cost.get(bare) or litellm.model_cost.get(model)
    if not entry:
        raise KeyError(f"{model} has no price in LiteLLM's model map")
    return entry


def prices(model: str, prompt: int) -> Prices:
    """``model``'s prices for a ``prompt``-token request (above-100K tier when it has one)."""
    entry = _map_entry(model)
    tier = _TIER_SUFFIX if prompt > HAIKU_55_PRICE_TIER else ""

    def rate(key: str, default: float) -> float:
        value = entry.get(f"{key}{tier}", entry.get(key))
        return float(value) if value is not None else default

    base = rate("input_cost_per_token", 0.0)
    write_5m = rate("cache_creation_input_token_cost", base)
    return Prices(
        input=base,
        output=rate("output_cost_per_token", 0.0),
        cache_read=rate("cache_read_input_token_cost", base),
        write_5m=write_5m,
        write_1h=rate("cache_creation_input_token_cost_above_1hr", 2 * base),
        min_cacheable=int(entry.get("prompt_cache_min_tokens") or 1024),
    )


# ─── Estimate ────────────────────────────────────────────────────────────────


@dataclass
class CallEstimate:
    """One planned call's estimated tokens and cost."""

    request_id: str
    call_type: str
    arm: str
    model: str
    prompt_tokens: int
    output_tokens: int
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cost_usd: float = 0.0


@dataclass
class CacheLedger:
    """The prefixes each model has written so far in the run."""

    written: set[tuple[str, str]] = field(default_factory=set)


def _cache_split(
    segments: list[Segment], model: str, min_cacheable: int, ledger: CacheLedger
) -> tuple[int, int, int, int]:
    """(uncached, read, 5m write, 1h write) model tokens of one request.

    A breakpoint whose prefix is below the model's minimum caches nothing; the
    next breakpoint that reaches it caches the whole prefix up to itself
    (Haiku 4.5's 1-hour block of ≈ 3.4k tokens rides the 5-minute one).
    """
    split = [0, 0, 0, 0]
    cumulative = pending = 0
    for segment in segments:
        tokens = model_tokens(segment.tokens, model)
        cumulative += tokens
        pending += tokens
        if segment.ttl is None or provider_of(model) != "anthropic" or cumulative < min_cacheable:
            continue
        if (model, segment.prefix_key) in ledger.written:
            split[1] += pending
        else:
            ledger.written.add((model, segment.prefix_key))
            split[3 if segment.ttl == "1h" else 2] += pending
        pending = 0
    return split[0] + pending, split[1], split[2], split[3]


def estimate_call(planned: PlannedCall, ledger: CacheLedger) -> CallEstimate:
    """The cost of one planned call as its arm sends the request."""
    request, arm = planned.request, planned.arm
    sent = request_for_arm(request.kwargs, arm)
    segments = request_segments(sent)
    prompt = model_tokens(sum(s.tokens for s in segments), arm.model)
    price = prices(arm.model, prompt)
    uncached, read, write_5m, write_1h = _cache_split(
        segments, arm.model, price.min_cacheable, ledger
    )
    output = model_tokens(request.expected_output_tokens, arm.model)
    cost = (
        uncached * price.input
        + read * price.cache_read
        + write_5m * price.write_5m
        + write_1h * price.write_1h
        + output * price.output
    )
    return CallEstimate(
        request_id=request.request_id,
        call_type=request.call_type,
        arm=arm.name,
        model=arm.model,
        prompt_tokens=prompt,
        output_tokens=output,
        cache_read=read,
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
        cost_usd=cost,
    )


def estimate_calls(planned: Iterable[PlannedCall]) -> list[CallEstimate]:
    """Each planned call's estimate, in order (a cold cache at the start)."""
    ledger = CacheLedger()
    return [estimate_call(call, ledger) for call in planned]


def totals_by_arm(estimates: Sequence[CallEstimate]) -> dict[tuple[str, str], dict[str, Any]]:
    """(call type, arm) → calls, model, max prompt tokens and cost."""
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for item in estimates:
        row = totals.setdefault(
            (item.call_type, item.arm),
            {"model": item.model, "calls": 0, "max_prompt_tokens": 0, "cost_usd": 0.0},
        )
        row["calls"] += 1
        row["max_prompt_tokens"] = max(row["max_prompt_tokens"], item.prompt_tokens)
        row["cost_usd"] += item.cost_usd
    return totals


def over_ceiling(estimates: Sequence[CallEstimate], ceiling: float = SPEND_CEILING_USD) -> bool:
    return sum(item.cost_usd for item in estimates) > ceiling


def longest_chunk_projection(requests: Sequence[AbRequest]) -> dict[str, int]:
    """Haiku 5.5 prompt tokens of an extraction call on 3.7's longest chunk.

    The extraction prompt is [static block (1h), video + transcript block (5m),
    the group's job]; the projection keeps the largest static and job blocks
    seen and swaps the transcript block for a ``LONGEST_CHUNK_CL100K`` one.
    """
    static = job = 0
    for request in requests:
        if request.call_type != "extraction":
            continue
        segments = request_segments(request.kwargs)
        static = max(static, segments[0].tokens)
        job = max(job, segments[-1].tokens)
    cl100k = static + LONGEST_CHUNK_CL100K + job
    return {
        "static_cl100k": static,
        "job_cl100k": job,
        "chunk_cl100k": LONGEST_CHUNK_CL100K,
        "total_cl100k": cl100k,
        "haiku55_tokens": model_tokens(cl100k, HAIKU_55),
        "price_tier_tokens": HAIKU_55_PRICE_TIER,
    }
