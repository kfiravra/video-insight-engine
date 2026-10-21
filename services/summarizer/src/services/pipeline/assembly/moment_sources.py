"""A label-only Moments source yields to a described sibling (output-quality 1.2, R7).

A Moments tab planned on a list of titles (``learning.timestamps`` stored before
phase 1: S01 shipped 46 chapter-like labels) ships its registry sibling instead
when the same run extracted that sibling with what each beat established
(S01's ``narrative.keyMoments``: 20 of 20 described, planned nowhere). A sibling
another planned tab ships from is never borrowed (R5). Read before the moment
fallback chain, which only fills a track that has nothing at all. And past the
assembler, a track that fails the moment gate yields to a described fallback
(``described_or_fallback``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from typing import Any

from src.shared_config.domain_config import data_source

from .density import moment_gate_passes
from .moment_normalizers import normalize_moment_items
from .sampling import is_described_track

logger = logging.getLogger(__name__)


def _read_path(path: str, extraction: dict | None) -> Any:
    """The value at a dotted extraction path; None when it breaks."""
    node: Any = extraction
    for key in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _described(value: Any) -> bool:
    return is_described_track(normalize_moment_items(value))


def described_moment_source(
    planned: str, data: Any, extraction: dict | None, claimed: Collection[str] = ()
) -> Any:
    """``data`` when it is a described track; else the first described registry
    sibling of ``planned`` no other tab ships from; else ``data`` unchanged."""
    if data is None or _described(data):
        return data
    spec = data_source(planned)
    for sibling in spec["siblings"] if spec else []:
        if sibling in claimed:
            continue
        value = _read_path(sibling, extraction)
        if _described(value):
            logger.info(
                "Moments: %s is label-only → described sibling %s (%d items)",
                planned,
                sibling,
                len(value) if isinstance(value, list) else 0,
            )
            return value
    return data


def described_or_fallback(props: dict, fallback: Callable[[], dict | None]) -> dict:
    """R7: a track that fails the moment gate (``density.moment_gate_passes``) yields
    to the fallback chain's track when that one is described — the memory outline
    with what each section establishes (1.9); with none it ships as it is (a
    Moments tab is never dropped for lack of a better source)."""
    if moment_gate_passes(props):
        return props
    alternative = fallback()
    if alternative is not None and moment_gate_passes(alternative):
        logger.info("Moments: label-only track → the described fallback (R7)")
        return alternative
    return props
