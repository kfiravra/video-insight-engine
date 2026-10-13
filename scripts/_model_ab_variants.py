"""Request variants of the model A/B round 2 (pipeline-1min 4.0d, Kfir 2026-10-13, D30).

A variant changes the captured request's prompt before an arm re-shapes it
for its model (``request_for_arm`` → ``request_for_model``), so every arm that
names the same variant sends the same prompt bytes.

``completeness`` adds ONE rule to the split extraction's static part — the
first content block, the rules + ``<schema>`` + example cached for an hour
(``extraction_group_prompt.prefix_block``; the group call has no system
message). Its rules section is ``<quality_rules>``: the rule goes in as that
section's last paragraph, after a blank line, right before ``</quality_rules>``.
Nothing else changes (no counts, floors or anti-padding text).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

COMPLETENESS = "completeness"
COMPLETENESS_RULE = (
    "Completeness: emit every distinct step, item or command the transcript supports "
    "— never merge two into one, never skip one."
)
# Inserted right before the closing tag, so it reads as the rules' last paragraph.
COMPLETENESS_INSERT = "\n\n" + COMPLETENESS_RULE
_RULES_CLOSE = "\n</quality_rules>"
_STATIC_TTL = "1h"


def _static_block(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The request's 1-hour cached block (the split extraction's rules + schemas + example)."""
    message = kwargs["messages"][0]
    content = message.get("content")
    if message.get("role") != "user" or not isinstance(content, list) or not content:
        raise ValueError("completeness: the request has no user content blocks")
    block = content[0]
    if (block.get("cache_control") or {}).get("ttl") != _STATIC_TTL:
        raise ValueError("completeness: the first block is not the 1-hour static block")
    return block


def add_completeness_rule(kwargs: dict[str, Any]) -> dict[str, Any]:
    """``kwargs`` with the completeness rule as ``<quality_rules>``'s last paragraph (a copy).

    Raises ``ValueError`` when the static block lacks exactly one
    ``</quality_rules>`` — the rule must land in the same place in every request.
    """
    changed = copy.deepcopy(kwargs)
    block = _static_block(changed)
    text = str(block["text"])
    if text.count(_RULES_CLOSE) != 1:
        raise ValueError("completeness: the static block has no single </quality_rules>")
    at = text.index(_RULES_CLOSE)
    block["text"] = text[:at] + COMPLETENESS_INSERT + text[at:]
    return changed


def insertion_point(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Where the rule lands in ``kwargs`` (for the report): block, offsets, bytes added."""
    text = str(_static_block(kwargs)["text"])
    at = text.index(_RULES_CLOSE)
    return {
        "block": "messages[0].content[0] (cache_control ttl=1h)",
        "after": text[max(0, at - 60) : at],
        "before": _RULES_CLOSE,
        "char_offset": at,
        "inserted": COMPLETENESS_INSERT,
        "inserted_utf8_bytes": len(COMPLETENESS_INSERT.encode("utf-8")),
    }


VARIANTS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    COMPLETENESS: add_completeness_rule,
}


def apply_variant(kwargs: dict[str, Any], variant: str | None) -> dict[str, Any]:
    """``kwargs`` with ``variant`` applied (unchanged — the same dict — for none)."""
    if variant is None:
        return kwargs
    return VARIANTS[variant](kwargs)
