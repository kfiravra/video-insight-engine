"""Per-group validation, key filter and merge of the split extraction (pipeline-1min 3.5).

A group call answers only its own keys (its emit-only line). ``validate_group``
reads each of the group's domains from the answer — under its key, or the flat
answer of a one-domain group — renames the keys the model spelled as a field
name or validation alias to the registry spelling (its dump key), keeps the
requested keys the model filled
(``schema_fields.keep_requested``, the one key rule), validates them item by
item with today's validator (bad items dropped, items keep their item-level
defaults) and cuts the validated dump back to those keys: a field nobody
requested, or one the model left out, is never materialized from the model
defaults (A5). Keys outside the request (other domains' included) are
discarded and logged. A domain whose schema does not parse is validated
unfiltered (the prompt could only name its keys).

``merge_groups`` unions the landed groups chunk-major, then text1..textN, then
the visual group (C12 — the group path only; ``extraction_merger`` keeps
today's chunked merge): objects merge key by key, lists concatenate, a scalar
keeps the FIRST non-empty value. A later contribution's list item whose
identity an earlier one holds is dropped — identity is the first of
``_IDENTITY_KEYS`` the item fills, in that fixed order. An ordering key is
renumbered only when it ran 1..n in every contributing list (shirt numbers
stay). ``requested_quality`` scores what the groups were asked for.
"""

from __future__ import annotations

import copy
import json
import logging
import types
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from pydantic import AliasChoices, BaseModel
from pydantic.fields import FieldInfo

from ...models.domain_types import DOMAIN_MODELS, MODIFIER_MODELS
from ...models.domain_validation import ValidationReport, merge_report, validate_block_items
from .extraction_quality import ExtractionQuality
from .field_groups import ExtractionGroup
from .field_requests import FieldRequest
from .schema_fields import keep_requested, load_domain_schema

if TYPE_CHECKING:
    from .extraction_groups import GroupOutcome

logger = logging.getLogger(__name__)

# Fixed order: a frozenset made the pick depend on the hash seed (C12).
_IDENTITY_KEYS = ("name", "label", "term", "title", "ingredient", "tool", "location")
_ORDER_KEYS = ("number", "step", "order", "index")
_TEXT_GROUP_PREFIX = "text"


def _empty_report() -> ValidationReport:
    return {"dropped": {}, "defaulted": 0}


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str | list | dict) and not value)


def _value_at(tree: Mapping[str, Any], path: str) -> Any:
    """The value at a dotted key path, ``None`` when the path breaks."""
    node: Any = tree
    for key in path.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


# ─── Validation ───


def _domains(group: ExtractionGroup) -> list[str]:
    return list(dict.fromkeys(f.domain for f in group.fields))


def _wrapper_keys(domain: str) -> tuple[str, str]:
    """Where a domain's object may sit; the output shape's own key is read last (it wins)."""
    return (f"{domain}Data", domain)


def _domain_block(raw: Mapping[str, Any], group: ExtractionGroup, domain: str) -> dict[str, Any]:
    """``domain``'s object in a group's answer: its wrapped object over its flat keys.

    Flat keys are the top-level keys no other domain of the group requests — a
    one-domain group's whole flat answer; a key two domains request cannot be
    attributed, so only their wrapped objects carry it.
    """
    wrappers = {key for d in _domains(group) for key in _wrapper_keys(d)}
    others = {f.field.split(".", 1)[0] for f in group.fields if f.domain != domain}
    block = {k: v for k, v in raw.items() if k not in wrappers and k not in others}
    for key in _wrapper_keys(domain):
        wrapped = raw.get(key)
        if isinstance(wrapped, dict):
            block.update(wrapped)
    return block


def _accepted_keys(name: str, info: FieldInfo) -> set[str]:
    """Every top-level spelling the model validates into this field (``populate_by_name``)."""
    keys = {name, info.alias or name}
    if isinstance(info.validation_alias, str):
        keys.add(info.validation_alias)
    elif isinstance(info.validation_alias, AliasChoices):
        keys |= {c for c in info.validation_alias.choices if isinstance(c, str)}
    return keys


def _spellings(model_cls: type[BaseModel]) -> dict[str, tuple[str, FieldInfo]]:
    """Accepted spelling → (the dump key = the registry spelling, the field)."""
    fields = model_cls.model_fields.items()
    variants = {k: (i.alias or n, i) for n, i in fields for k in _accepted_keys(n, i)}
    return {**variants, **{i.alias or n: (i.alias or n, i) for n, i in fields}}


def _object_model(annotation: Any) -> type[BaseModel] | None:
    """The model a field holds as one object (``X`` / ``X | None``); None for lists and scalars."""
    union = get_origin(annotation) in (Union, types.UnionType)
    candidates = get_args(annotation) if union else (annotation,)
    return next((c for c in candidates if isinstance(c, type) and issubclass(c, BaseModel)), None)


def _canonical(block: Mapping[str, Any], model_cls: type[BaseModel]) -> dict[str, Any]:
    """``block`` with every key the model accepts under another spelling (a field name,
    a validation alias) renamed to its dump key, nested model objects alike — the key
    rule matches registry spellings exactly. The dump spelling wins when both appear."""
    spellings = _spellings(model_cls)
    renamed: dict[str, Any] = {}
    exact_first = sorted(block, key=lambda key: key in spellings and spellings[key][0] != key)
    for key in exact_first:
        dumped, info = spellings.get(key, (key, None))
        if dumped in renamed:
            continue
        nested = _object_model(info.annotation) if info is not None else None
        value = block[key]
        renamed[dumped] = _canonical(value, nested) if nested and isinstance(value, dict) else value
    return renamed


def _filled(block: Mapping[str, Any], paths: Sequence[str]) -> list[str]:
    """The paths the answer filled. Omitted, ``null`` or ``{}`` stays absent (validating it
    would only materialize the model defaults); ``[]`` is the model's own answer."""
    return [path for path in paths if _value_at(block, path) not in (None, {})]


def _kept_paths(
    group_id: str, domain: str, block: Mapping[str, Any], fields: Sequence[str]
) -> list[str]:
    """The requested fields the answer filled (the rest is logged as discarded) — every
    filled key when the schema is broken."""
    if load_domain_schema(domain).broken:
        logger.error(
            "[group_merge] schema %r does not parse — its group output is validated unfiltered",
            domain,
        )
        return _filled(block, list(block))
    outside = sorted(set(block) - {field.split(".", 1)[0] for field in fields})
    if outside:
        logger.info(
            "[group_merge] %s %s: keys outside the request discarded: %s",
            group_id,
            domain,
            outside,
        )
    return _filled(block, fields)


def _model_for(
    domain: str, content_tags: Sequence[str], modifiers: Sequence[str]
) -> type[BaseModel] | None:
    if domain in content_tags:
        return DOMAIN_MODELS.get(domain)
    if domain in modifiers:
        return MODIFIER_MODELS.get(domain)
    return None


def _validate_domain(
    domain: str,
    model_cls: type[BaseModel],
    block: Mapping[str, Any],
    paths: Sequence[str],
    report: ValidationReport,
) -> dict[str, Any]:
    """The domain's kept paths, validated item by item, in the model's key order.

    Validation sees only the kept keys, so its drop counts are the group's own.
    A block still failing after pruning keeps nothing (today it became the
    model defaults); the reset is in the report.
    """
    if not paths:
        return {}
    own = keep_requested(block, paths)
    validated, dropped, defaulted = validate_block_items(domain, model_cls, own)
    merge_report(report, dropped, defaulted)
    if f"{domain}.*" in dropped:
        return {}
    kept = keep_requested(validated, paths)
    return {key: kept[key] for key in validated if key in kept}


def validate_group(
    raw: Mapping[str, Any],
    group: ExtractionGroup,
    content_tags: Sequence[str],
    modifiers: Sequence[str],
) -> tuple[dict[str, Any], ValidationReport]:
    """One group's answer → its validated keys, domain-keyed, and the validation report.

    A domain of the group that is not an active content tag or modifier with a
    model is discarded (logged) — never passed through unvalidated.
    """
    report = _empty_report()
    validated: dict[str, Any] = {}
    for domain in _domains(group):
        model_cls = _model_for(domain, content_tags, modifiers)
        if model_cls is None:
            logger.warning(
                "[group_merge] %s: %r is not an active domain with a model — keys discarded",
                group.group_id,
                domain,
            )
            continue
        fields = [f.field for f in group.fields if f.domain == domain]
        block = _canonical(_domain_block(raw, group, domain), model_cls)
        paths = _kept_paths(group.group_id, domain, block, fields)
        kept = _validate_domain(domain, model_cls, block, paths, report)
        if kept:
            validated[domain] = kept
    return validated, report


# ─── Merge ───


def _text_number(group: ExtractionGroup) -> int:
    number = group.group_id.removeprefix(_TEXT_GROUP_PREFIX)
    return int(number) if number.isdigit() else 0


def merge_order(outcomes: Sequence[GroupOutcome]) -> list[GroupOutcome]:
    """Chunk-major, then text1..textN, then the visual group."""
    return sorted(outcomes, key=lambda o: (o.chunk, o.group.visual, _text_number(o.group)))


def _identity(item: Any) -> str:
    """An item's dedup identity: its first filled identity key, else its whole value."""
    if isinstance(item, dict):
        for key in _IDENTITY_KEYS:
            value = item.get(key)
            if value is not None and str(value).strip():
                return f"{key}={str(value).strip().lower()}"
    if isinstance(item, str):
        return f"text={item.strip().lower()}"
    return json.dumps(item, sort_keys=True, default=str)


def _runs_one_to_n(items: Sequence[Any], key: str) -> bool:
    return all(
        isinstance(item, dict)
        and isinstance(item.get(key), int)
        and not isinstance(item.get(key), bool)
        and item[key] == n
        for n, item in enumerate(items, start=1)
    )


def _renumber(merged: list[Any], lists: Sequence[Sequence[Any]]) -> None:
    """Renumber the first ordering key that ran 1..n in every contributing list."""
    key = next((k for k in _ORDER_KEYS if all(_runs_one_to_n(lst, k) for lst in lists)), None)
    if key is None:
        return
    for n, item in enumerate(merged, start=1):
        item[key] = n


def _union_lists(lists: Sequence[list[Any]]) -> list[Any]:
    """Concatenated in order; a later contribution's item an earlier one holds is dropped
    (a contribution's own repeats stay — the model listed them)."""
    merged = list(lists[0])
    if len(lists) == 1:
        return merged
    seen = {_identity(item) for item in merged}
    for incoming in lists[1:]:
        fresh = [item for item in incoming if _identity(item) not in seen]
        seen.update(_identity(item) for item in fresh)
        merged.extend(fresh)
    _renumber(merged, lists)
    return merged


def _union(values: Sequence[Any]) -> Any:
    """One key's contributions, in merge order: objects merge, lists concatenate, a scalar
    (or a shape conflict) keeps the first non-empty value; all empty → the first."""
    filled = [value for value in values if not _is_empty(value)]
    if not filled:
        return values[0]
    if all(isinstance(value, dict) for value in filled):
        return _union_objects(filled)
    if all(isinstance(value, list) for value in filled):
        return _union_lists(filled)
    return filled[0]


def _union_objects(objects: Sequence[dict[str, Any]]) -> dict[str, Any]:
    keys = dict.fromkeys(key for obj in objects for key in obj)
    return {key: _union([obj[key] for obj in objects if key in obj]) for key in keys}


def merge_groups(outcomes: Sequence[GroupOutcome]) -> tuple[dict[str, Any], ValidationReport]:
    """The landed groups' data as ONE domain-keyed extraction + their summed report.

    Failed groups contribute nothing. The result is a fresh copy — merging never
    touches an outcome's data, so merging the outcomes landed so far is safe.
    """
    landed = [o for o in merge_order(outcomes) if o.status == "ok" and o.data is not None]
    report = _empty_report()
    for outcome in landed:
        validation = outcome.validation
        merge_report(report, validation.get("dropped") or {}, validation.get("defaulted", 0))
    contributions = [copy.deepcopy(o.data) for o in landed if o.data]
    return _union_objects(contributions), report


# ─── Quality ───


def requested_quality(
    fields: Sequence[FieldRequest], extraction: Mapping[str, Any]
) -> ExtractionQuality:
    """The share of the requested fields the extraction filled (unrequested keys don't count)."""
    paths = list(dict.fromkeys(f.path for f in fields))
    empty = [path for path in paths if _is_empty(_value_at(extraction, path))]
    populated = len(paths) - len(empty)
    return ExtractionQuality(
        score=populated / len(paths) if paths else 1.0,
        populated=populated,
        total=len(paths),
        empty_fields=empty,
    )
