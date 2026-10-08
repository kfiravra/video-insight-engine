"""Per-item validation of one extraction block (hotfix 2.1).

A block that failed its Pydantic model used to be passed through raw, so one
bad item (a ``cheatSheet`` entry with ``code: null``) put unvalidated data in
front of every reader. Now every validation error is traced to the deepest
list item that contains it and that item is dropped; an error outside any
list removes the field so its model default applies. The rest of the block
is validated again and kept. The dropped counts are persisted under
``pipeline.extraction._dropped``.
"""

from __future__ import annotations

import copy
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# A nested item can fail only once its parent validates, so pruning repeats;
# each round removes at least one target or stops.
MAX_PRUNE_ROUNDS = 4
DROPPED_KEY = "_dropped"

PathSegment = str | int
Path = tuple[PathSegment, ...]


def _key_variants(segment: str) -> tuple[str, ...]:
    """``loc`` reports aliases (``cheatSheet``); the input may use field names."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", segment).lower()
    camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), segment)
    return (segment, snake, camel)


def _step(container: Any, segment: PathSegment) -> tuple[PathSegment, Any] | None:
    """The (actual key, value) one ``loc`` segment reaches, or None."""
    if isinstance(container, list) and isinstance(segment, int):
        return (segment, container[segment]) if 0 <= segment < len(container) else None
    if isinstance(container, dict) and isinstance(segment, str):
        for key in _key_variants(segment):
            if key in container:
                return key, container[key]
    return None


def _target(data: dict, loc: Iterable[PathSegment]) -> Path | None:
    """What one error removes: the deepest list item on ``loc``, else the deepest field.

    The walk stops at the first segment the data does not contain (a missing
    required field, or a union-branch label Pydantic adds to ``loc``).
    """
    current: Any = data
    path: list[PathSegment] = []
    item_path: Path | None = None
    for segment in loc:
        step = _step(current, segment)
        if step is None:
            break
        key, current = step
        path.append(key)
        if isinstance(key, int):
            item_path = tuple(path)
    if item_path is not None:
        return item_path
    return tuple(path) if path else None


def _sort_key(path: Path) -> list[tuple[int, Any]]:
    return [(0, s) if isinstance(s, int) else (1, s) for s in path]


def _outermost(targets: set[Path]) -> list[Path]:
    """Targets without an ancestor in the set, deepest list index first.

    Removing in reverse order keeps the remaining indices of a list valid.
    """
    kept = [t for t in targets if not any(t[: len(o)] == o for o in targets if len(o) < len(t))]
    return sorted(kept, key=_sort_key, reverse=True)


def _label(tag: str, path: Path) -> str:
    fields = ".".join(s for s in path if isinstance(s, str))
    return f"{tag}.{fields}" if fields else tag


def _remove(data: dict, targets: set[Path], tag: str, dropped: Counter[str]) -> None:
    for path in _outermost(targets):
        parent: Any = data
        for segment in path[:-1]:
            parent = parent[segment]
        del parent[path[-1]]
        dropped[_label(tag, path)] += 1


def _defaults(model_cls: type[BaseModel]) -> dict:
    try:
        return model_cls.model_validate({}).model_dump(by_alias=True)
    except ValidationError:
        return {}


def validate_block_items(
    tag: str, model_cls: type[BaseModel], block: dict
) -> tuple[dict, Counter[str]]:
    """Validate ``block``; drop the items/fields that fail and keep the rest.

    Returns the validated dict and ``{"tag.field.path": count}`` of what was
    removed. Never returns unvalidated data: a block that still fails after
    pruning becomes the model defaults.
    """
    data = copy.deepcopy(block)
    dropped: Counter[str] = Counter()
    for _ in range(MAX_PRUNE_ROUNDS):
        try:
            validated = model_cls.model_validate(data).model_dump(by_alias=True)
        except ValidationError as exc:
            targets = {t for err in exc.errors() if (t := _target(data, err["loc"])) is not None}
            if not targets or not isinstance(data, dict):
                break
            _remove(data, targets, tag, dropped)
            continue
        if dropped:
            logger.warning("Validation for tag %s dropped invalid items: %s", tag, dict(dropped))
        return validated, dropped
    logger.error("Validation for tag %s still failing after pruning — block reset to defaults", tag)
    dropped[f"{tag}.*"] += 1
    return _defaults(model_cls), dropped


def extraction_with_drops(
    extraction: dict[str, Any] | None, dropped: Mapping[str, int]
) -> dict[str, Any] | None:
    """``pipeline.extraction``: the validated data, plus ``_dropped`` when items were removed."""
    if not dropped or not isinstance(extraction, dict):
        return extraction
    return {**extraction, DROPPED_KEY: dict(dropped)}
