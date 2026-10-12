"""Per-item validation of one extraction block (hotfix 2.1, phase-1 close).

A block that failed its Pydantic model used to be passed through raw, so one
bad item put unvalidated data in front of every reader. Each validation error
is now repaired or narrowed to the item it belongs to:

- a missing ``title``/``name`` is taken from its counterpart on the same item
  (the LLM writes one for the other);
- a number in a string field becomes its text (Pydantic does not coerce
  int → str; prod fitness exercises lost 12/12 items to ``duration: 30``);
- any other bad value — an explicit ``null`` included — on a field that has a
  default removes the key so the default applies;
- a failing required field (``null`` included) drops the deepest list item
  that contains it (outside any list the field is reset to its default).

The rest of the block is validated again and kept; a block that still fails
becomes the model defaults. ``{"tag.field": {"dropped": n, "total": m}}`` is
persisted as ``pipeline.extraction._dropped``, and a planned field that lost
more than half of its items as ``pipeline.extraction._misTargeted``.
"""

from __future__ import annotations

import copy
import logging
import re
import types
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Union, get_args, get_origin

from pydantic import AliasChoices, BaseModel, ValidationError
from pydantic.fields import FieldInfo

logger = logging.getLogger(__name__)

# Repairs and nested items can surface only once the previous round validates,
# so validation repeats; each round repairs or removes something, or stops.
MAX_PRUNE_ROUNDS = 6
DROPPED_KEY = "_dropped"
MIS_TARGETED_KEY = "_misTargeted"
_TITLE_ALIASES = {"title": "name", "name": "title"}

PathSegment = str | int
Path = tuple[PathSegment, ...]
DropCounts = dict[str, dict[str, int]]


# ─── loc → data ───


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


def _holder(data: dict, loc: tuple[PathSegment, ...]) -> dict | None:
    """The dict that holds (or should hold) the field ``loc`` ends with."""
    current: Any = data
    for segment in loc[:-1]:
        step = _step(current, segment)
        if step is None:
            return None
        current = step[1]
    return current if isinstance(current, dict) and isinstance(loc[-1], str) else None


# ─── loc → model field ───


def _unwrap_optional(annotation: Any) -> Any:
    if get_origin(annotation) in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return args[0] if len(args) == 1 else annotation
    return annotation


def _model_field(model: type[BaseModel], segment: str) -> FieldInfo | None:
    for name, info in model.model_fields.items():
        aliases = {name, info.alias}
        if isinstance(info.validation_alias, str):
            aliases.add(info.validation_alias)
        elif isinstance(info.validation_alias, AliasChoices):
            aliases |= {c for c in info.validation_alias.choices if isinstance(c, str)}
        if segment in aliases:
            return info
    return None


def _field_at(model_cls: type[BaseModel], loc: tuple[PathSegment, ...]) -> FieldInfo | None:
    """The model field ``loc`` names, walking nested models and list items."""
    current: Any = model_cls
    info: FieldInfo | None = None
    for segment in loc:
        current = _unwrap_optional(current)
        if isinstance(segment, int):
            args = get_args(current) if get_origin(current) is list else ()
            current, info = (args[0] if args else None), None
            continue
        if not (isinstance(current, type) and issubclass(current, BaseModel)):
            return None
        info = _model_field(current, segment)
        if info is None:
            return None
        current = info.annotation
    return info


# ─── Repairs ───


def _alias_repair(holder: dict, key: str) -> bool:
    """A missing ``title``/``name`` taken from its counterpart on the same item."""
    other = _TITLE_ALIASES.get(key)
    value = holder.get(other) if other else None
    if isinstance(value, str) and value.strip():
        holder[key] = value
        return True
    return False


def _repair(
    data: dict,
    model_cls: type[BaseModel],
    error: Mapping[str, Any],
    tag: str,
    dropped: Counter[str],
) -> bool:
    """Fix one error in place; True when the data changed.

    - missing ``title``/``name`` → its counterpart (no data lost);
    - a number in a string field → the number as text (Pydantic does not
      coerce int → str; no data lost);
    - any other bad value (``null`` included) on a field with a default → the
      field is removed so the default applies (counted under the field's label);
    - a failing required field is not repaired — its item is dropped.
    """
    loc = tuple(error["loc"])
    holder = _holder(data, loc) if loc else None
    if holder is None:
        return False
    key = str(loc[-1])
    if error["type"] == "missing":
        return _alias_repair(holder, key)
    step = _step(holder, key)
    if step is None:
        return False
    actual_key, value = step
    if (
        error["type"] == "string_type"
        and isinstance(value, int | float)
        and not isinstance(value, bool)
    ):
        holder[actual_key] = str(value)
        return True
    info = _field_at(model_cls, loc)
    if info is None or info.is_required():
        return False
    del holder[actual_key]
    if value is not None:
        dropped[_label(tag, _fields(loc))] += 1
    return True


# ─── Drops ───


def _sort_key(path: Path) -> list[tuple[int, Any]]:
    return [(0, s) if isinstance(s, int) else (1, s) for s in path]


def _outermost(targets: set[Path]) -> list[Path]:
    """Targets without an ancestor in the set, deepest list index first.

    Removing in reverse order keeps the remaining indices of a list valid.
    """
    kept = [t for t in targets if not any(t[: len(o)] == o for o in targets if len(o) < len(t))]
    return sorted(kept, key=_sort_key, reverse=True)


def _fields(path: Path) -> list[str]:
    return [s for s in path if isinstance(s, str)]


def _label(tag: str, fields: list[str]) -> str:
    return f"{tag}.{'.'.join(fields)}" if fields else tag


def _remove(data: dict, targets: set[Path], tag: str, dropped: Counter[str]) -> None:
    for path in _outermost(targets):
        parent: Any = data
        for segment in path[:-1]:
            parent = parent[segment]
        del parent[path[-1]]
        dropped[_label(tag, _fields(path))] += 1


def _count_items(node: Any, fields: list[str]) -> int:
    """How many items ``fields`` reaches in the original block (a non-list field counts 1)."""
    if not fields:
        return len(node) if isinstance(node, list) else int(node is not None)
    if isinstance(node, list):
        return sum(_count_items(item, fields) for item in node)
    if isinstance(node, dict):
        for key in _key_variants(fields[0]):
            if key in node:
                return _count_items(node[key], fields[1:])
    return 0


def _with_totals(tag: str, block: Any, dropped: Counter[str]) -> DropCounts:
    counts: DropCounts = {}
    for label, n in dropped.items():
        fields = label[len(tag) + 1 :].split(".") if label != tag and label != f"{tag}.*" else []
        total = _count_items(block, fields) if fields else 1
        counts[label] = {"dropped": n, "total": max(total, n)}
    return counts


def _defaults(model_cls: type[BaseModel]) -> dict:
    try:
        return model_cls.model_validate({}).model_dump(by_alias=True)
    except ValidationError:
        return {}


def validate_block_items(
    tag: str, model_cls: type[BaseModel], block: dict
) -> tuple[dict, DropCounts]:
    """Validate ``block``: repair what can be repaired, drop the items that fail.

    Returns the validated dict and ``{"tag.field.path": {"dropped": n, "total": m}}``.
    Never returns unvalidated data: a block that still fails after pruning
    becomes the model defaults.
    """
    data = copy.deepcopy(block)
    dropped: Counter[str] = Counter()
    for _ in range(MAX_PRUNE_ROUNDS):
        try:
            validated = model_cls.model_validate(data).model_dump(by_alias=True)
        except ValidationError as exc:
            if not isinstance(data, dict):
                break
            errors = exc.errors()
            if sum(_repair(data, model_cls, err, tag, dropped) for err in errors):
                continue
            targets = {t for err in errors if (t := _target(data, err["loc"])) is not None}
            if not targets:
                break
            _remove(data, targets, tag, dropped)
            continue
        if dropped:
            logger.warning("Validation for tag %s dropped invalid items: %s", tag, dict(dropped))
        return validated, _with_totals(tag, block, dropped)
    logger.error("Validation for tag %s still failing after pruning — block reset to defaults", tag)
    dropped[f"{tag}.*"] += 1
    return _defaults(model_cls), _with_totals(tag, block, dropped)


def merge_drop_counts(into: DropCounts, more: Mapping[str, Mapping[str, int]]) -> None:
    for label, counts in more.items():
        current = into.setdefault(label, {"dropped": 0, "total": 0})
        current["dropped"] += counts["dropped"]
        current["total"] += counts["total"]


def mis_targeted_fields(
    dropped: Mapping[str, Mapping[str, int]], planned: Iterable[str]
) -> list[str]:
    """Planned dataSources that lost more than half of their items to validation."""
    planned_paths = set(planned)
    return sorted(
        label
        for label, counts in dropped.items()
        if label in planned_paths
        and counts.get("total")
        and counts["dropped"] * 2 > counts["total"]
    )


def extraction_with_drops(
    extraction: dict[str, Any] | None,
    dropped: Mapping[str, Any],
    mis_targeted: list[str] | None = None,
) -> dict[str, Any] | None:
    """``pipeline.extraction``: the validated data, plus ``_dropped`` / ``_misTargeted`` when set."""
    if not isinstance(extraction, dict) or not (dropped or mis_targeted):
        return extraction
    record: dict[str, Any] = {**extraction}
    if dropped:
        record[DROPPED_KEY] = dict(dropped)
    if mis_targeted:
        record[MIS_TARGETED_KEY] = list(mis_targeted)
    return record
