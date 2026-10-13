"""Group calls answered from the recorded single extraction (pipeline-1min 3.4, design §5).

The cassettes were recorded with ONE extraction call (entry E). A split run
asks per field group instead; each group call is answered from E — never a
``CassetteMissError``:

* content = E's JSON cut to the group's keys (a flat single-domain answer is
  read as that domain's object), re-wrapped under the domain; a list or object
  key E lacks is answered ``[]`` / ``{}`` — assembly sees the recorded data;
* output tokens = E's output tokens × cl100k(slice) / cl100k(all of E);
* wall = TTFT + out / R with R = O / (L/1000 − 1.0) (E's output over its wall
  minus a 1 s prefill), TTFT 1.0 s for text1 (it writes the cache) and 0.6 s
  for the rest; input usage: text1 writes blocks 1 + 2 (chars / 3.6), the
  others read them.

The group a call belongs to is published on ``group_var`` by a patch around
``extraction_groups.run_group_call`` (the span trick of ``fake_llm``).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import tiktoken

from src.services.pipeline import extraction_groups
from src.services.pipeline.extraction_groups import GroupCall
from tests.replay.cassette import LLMEntry, LLMKey

EXTRACTION_KEY = LLMKey(feature="summarize:extraction", span="extraction", ordinal=0)
GROUP_FEATURE_PREFIX = "summarize:extraction:"
WARM_UP_GROUP = "text1"
WARM_UP_TTFT_S = 1.0
GROUP_TTFT_S = 0.6
PREFILL_S = 1.0
CHARS_PER_TOKEN = 3.6

_ENCODING = tiktoken.get_encoding("cl100k_base")


@dataclass(frozen=True)
class GroupSpec:
    """The group a call asks for: (domain, field, kind) per key."""

    group_id: str
    keys: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class SynthCall:
    """One synthesized group answer, its timing (seconds at speed 1) and usage."""

    output: str
    output_tokens: int
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    ttft_s: float
    wall_s: float


group_var: ContextVar[GroupSpec | None] = ContextVar("replay_group_spec", default=None)


def is_group_feature(feature: str) -> bool:
    return feature.startswith(GROUP_FEATURE_PREFIX)


# ─── Content ───


def recorded_answer(entry: LLMEntry) -> dict[str, Any]:
    """E's JSON (the recordings are fenced)."""
    body = entry.output.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(body)
    return parsed if isinstance(parsed, dict) else {}


def _domain_object(answer: Mapping[str, Any], domain: str) -> Mapping[str, Any]:
    """The domain's object in E: under its key / ``<d>Data``, else the flat answer."""
    for key in (domain, f"{domain}Data"):
        value = answer.get(key)
        if isinstance(value, dict):
            return value
    return answer


def _lookup(tree: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    node: Any = tree
    for key in field.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return False, None
        node = node[key]
    return True, node


def _put(tree: dict[str, Any], field: str, value: Any) -> None:
    keys = field.split(".")
    node = tree
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


_EMPTY = {"list": list, "object": dict}


def slice_answer(answer: Mapping[str, Any], keys: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    """E cut to ``keys``, wrapped per domain; a missing list/object key → ``[]`` / ``{}``."""
    sliced: dict[str, dict[str, Any]] = {}
    for domain, field, kind in keys:
        found, value = _lookup(_domain_object(answer, domain), field)
        if not found and kind not in _EMPTY:
            continue
        _put(sliced.setdefault(domain, {}), field, value if found else _EMPTY[kind]())
    return sliced


def _tokens(value: object) -> int:
    return len(_ENCODING.encode(json.dumps(value, ensure_ascii=False)))


# ─── Timing + usage ───


def _cached_chars(messages: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """(chars of the cached blocks, chars of the whole request)."""
    cached = total = 0
    for message in messages:
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"text": str(content or "")}]
        for block in blocks:
            size = len(str(block.get("text", "")))
            total += size
            if "cache_control" in block:
                cached += size
    return cached, total


def synthesize(
    entry: LLMEntry, spec: GroupSpec, messages: Sequence[Mapping[str, Any]]
) -> SynthCall:
    """The group's answer and timing from E (see the module docstring)."""
    answer = recorded_answer(entry)
    sliced = slice_answer(answer, spec.keys)
    share = _tokens(sliced) / max(_tokens(answer), 1)
    output_tokens = max(1, round(entry.output_tokens * share))
    rate = entry.output_tokens / max(entry.latency_ms / 1000 - PREFILL_S, 1e-3)
    ttft = WARM_UP_TTFT_S if spec.group_id == WARM_UP_GROUP else GROUP_TTFT_S
    cached_chars, total_chars = _cached_chars(messages)
    cached = round(cached_chars / CHARS_PER_TOKEN)
    warm_up = spec.group_id == WARM_UP_GROUP
    return SynthCall(
        output=json.dumps(sliced, ensure_ascii=False),
        output_tokens=output_tokens,
        input_tokens=round(total_chars / CHARS_PER_TOKEN),
        cache_read_tokens=0 if warm_up else cached,
        cache_write_tokens=cached if warm_up else 0,
        ttft_s=ttft,
        wall_s=ttft + output_tokens / rate,
    )


# ─── Publishing the group ───


def _spec(call: GroupCall) -> GroupSpec:
    keys = tuple((f.domain, f.field, f.kind) for f in call.group.fields)
    return GroupSpec(group_id=call.group.group_id, keys=keys)


def _tag_group(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def wrapper(call: GroupCall, *args: Any, **kwargs: Any) -> Any:
        token = group_var.set(_spec(call))
        try:
            return await original(call, *args, **kwargs)
        finally:
            group_var.reset(token)

    return wrapper


@contextmanager
def group_tagging_patch() -> Iterator[None]:
    """Publish each group call's keys on ``group_var`` while it runs."""
    tagged = _tag_group(extraction_groups.run_group_call)
    with patch.object(extraction_groups, "run_group_call", tagged):
        yield
