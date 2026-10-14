"""Cache rewrites: split-extraction calls that wrote a prefix another call writes.

Per chunk, the text1 group call is the warm-up: it writes the shared cached
prefix (block 1, 1h — identical for every chunk — and block 2, 5m), and its
first streamed token releases the chunk's other groups, which then read it.
Two writes are races, paid at the cache-write price (``pipeline.timing``
``counts.cacheRewrites`` / ``cacheRewriteTokens``):

* a group released before text1's first token (``GROUP_RELEASE_FALLBACK_S``)
  that wrote the prefix — jMq8lEu-of0 r1 on prod, +$0.065 (hotfix 4.1 (3));
* a later chunk's text1 writing block 1, which chunk 0's text1 writes at the
  same moment (only its 1h tokens count).

Not counted: a refusal re-send (the previous model's own cold cache), a group in
a chunk without a text1 call (it is that chunk's only writer), a group that wrote
after text1's first token (a lapsed 5-minute TTL, not a race), and refused calls
(they sit in ``llmFailures``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# extraction_groups.GroupCall.stage_name: chunk 0 has no suffix, chunk k >= 1 ``_c{k}``.
_GROUP_SPAN_RE = re.compile(r"^extraction_(?P<group>text\d+|visual)(?:_c(?P<chunk>\d+))?$")
_WARM_UP = "text1"


def _group_and_chunk(call: dict[str, Any]) -> tuple[str, int] | None:
    match = _GROUP_SPAN_RE.match(str(call.get("span") or ""))
    if match is None or "refusedModel" in call or call.get("fallbackUsed"):
        return None
    return match.group("group"), int(match.group("chunk") or 0)


def _first_tokens(calls: Iterable[dict[str, Any]]) -> dict[int, int]:
    """Chunk → when its text1 warm-up streamed its first token (run ms)."""
    first: dict[int, int] = {}
    for call in calls:
        parsed = _group_and_chunk(call)
        ttft = call.get("ttftMs")
        if parsed is None or parsed[0] != _WARM_UP or ttft is None:
            continue
        at = int(call.get("startMs") or 0) + int(ttft)
        first[parsed[1]] = min(at, first.get(parsed[1], at))
    return first


def _rewrite_tokens(call: dict[str, Any], group: str, chunk: int, first: dict[int, int]) -> int:
    """The prefix tokens ``call`` wrote that another call writes; 0 when it isn't a race."""
    if group == _WARM_UP:
        return int(call.get("cacheWrite1hTokens") or 0) if chunk >= 1 else 0
    released = first.get(chunk)
    if released is None or int(call.get("startMs") or 0) >= released:
        return 0
    return int(call.get("cacheWriteTokens") or 0)


def rewrite_counts(llm_calls: list[dict[str, Any]]) -> dict[str, int]:
    """``cacheRewrites`` (calls) and ``cacheRewriteTokens`` (the tokens they wrote)."""
    first = _first_tokens(llm_calls)
    rewrites = 0
    tokens = 0
    for call in llm_calls:
        parsed = _group_and_chunk(call)
        written = _rewrite_tokens(call, *parsed, first) if parsed is not None else 0
        if written:
            rewrites += 1
            tokens += written
    return {"cacheRewrites": rewrites, "cacheRewriteTokens": tokens}
