"""Field-aware view of the domain schemas — key trees for group extraction (pipeline-1min 3.1).

Parses the schema files the extraction prompt already sends (same loader, same
text — the ``<schema>`` block of block 1), never edits them. A schema is prose
around one JSON skeleton; dropping every line that starts with ``>`` (the
``> UI``, ``> CONNECTIONS``, ``> GROUPING`` directives, some of which sit inside
the object) leaves the first ``{`` line parseable as JSON.

A group call reads the full schema in its cached block and is told in its tail
which keys to emit: ``slice_skeleton`` gives a group's key tree (dotted paths
such as ``setup.commands`` keep their parents), ``render_emit_only`` turns the
group's slices into the "Emit ONLY these keys" line plus a compact output
shape, ``keep_requested`` applies the same key rule to a group's output (the
key filter), and ``scalar_fields`` names a domain's top-level keys no registry
dataSource covers (``food.meta``, ``learning.summary``) so the domain's first
group can carry them.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ...shared_config.domain_config import data_sources
from .extraction_prompt import load_schema_text

logger = logging.getLogger(__name__)

# A schema's JSON skeleton is the first object that opens at the start of a line.
_OBJECT_START_RE = re.compile(r"^\{", re.MULTILINE)
_DIRECTIVE_PREFIX = ">"

_EMIT_ONLY_LEAD = "Emit ONLY these keys (anything else is discarded): "
_SHAPE_LEAD = "Output shape: "
_LIST_SHAPE = "[…]"
_OBJECT_SHAPE = "{…}"
_SCALAR_SHAPE = '"…"'


class SchemaParseError(ValueError):
    """A domain's schema text exists but holds no parseable JSON skeleton.

    The caller cannot cut a key tree from it: send that domain unfiltered.
    """


@dataclass(frozen=True)
class DomainSchema:
    """One domain or modifier schema's JSON skeleton.

    ``broken``: the schema text exists but its skeleton does not parse (logged
    at error level); ``skeleton`` is then ``{}``. An unsafe or unknown name is
    not broken — it has no schema, so its skeleton is simply empty.
    """

    name: str
    skeleton: dict[str, Any]
    broken: bool = False


# ─── Loading ───


def _skeleton_json(text: str) -> object:
    """The first JSON object of a schema text once its ``>`` directive lines are gone."""
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(_DIRECTIVE_PREFIX)
    )
    start = _OBJECT_START_RE.search(body)
    if start is None:
        raise SchemaParseError("no line opens a JSON object")
    try:
        skeleton, _ = json.JSONDecoder().raw_decode(body, start.start())
    except json.JSONDecodeError as exc:
        raise SchemaParseError(str(exc)) from exc
    return skeleton


@lru_cache(maxsize=64)
def _parse(name: str, text: str) -> DomainSchema:
    """Keyed by the text: a registry promotion's new text is parsed afresh, an
    unchanged one is not re-parsed. Never hand this object out (callers could
    mutate the cached skeleton)."""
    if not text:
        return DomainSchema(name, {})
    try:
        skeleton = _skeleton_json(text)
    except SchemaParseError as exc:
        logger.error("Schema %r has no parseable JSON skeleton: %s", name, exc)
        return DomainSchema(name, {}, broken=True)
    if not isinstance(skeleton, dict) or not skeleton:
        logger.error("Schema %r skeleton is not a non-empty JSON object", name)
        return DomainSchema(name, {}, broken=True)
    return DomainSchema(name, skeleton)


def _schema(name: str) -> DomainSchema:
    """The current text on every use (the registry can serve a newer one), parsed once per text."""
    return _parse(name, load_schema_text(name))


def load_domain_schema(name: str) -> DomainSchema:
    """The schema's skeleton; an unsafe or unknown name gives an empty one.

    Deep-copied like ``data_sources()``: parses are cached for the process, so a
    caller editing its skeleton must not change the next caller's.
    """
    schema = _schema(name)
    return DomainSchema(schema.name, copy.deepcopy(schema.skeleton), schema.broken)


def scalar_fields(domain: str) -> tuple[str, ...]:
    """Top-level non-list keys no registry dataSource of ``domain`` reads, in schema order.

    A registry path's parent is not a scalar (``tech.setup`` holds
    ``tech.setup.commands``); a non-registry list (``music.genre``) is not one
    either — nothing requests it. A broken schema has none.
    """
    served = {
        spec["field"].split(".", 1)[0]
        for spec in data_sources().values()
        if spec["domain"] == domain
    }
    skeleton = _schema(domain).skeleton
    return tuple(
        key for key, value in skeleton.items() if not isinstance(value, list) and key not in served
    )


# ─── Key trees ───


def _subtree(tree: Mapping[str, Any], field: str) -> Any:
    """The value at a dotted key path; ``KeyError`` when the path breaks."""
    node: Any = tree
    for key in field.split("."):
        if not isinstance(node, Mapping) or key not in node:
            raise KeyError(field)
        node = node[key]
    return node


def _top_level_fields(fields: Sequence[str]) -> list[str]:
    """Requested paths in order, minus duplicates and paths under another requested path."""
    requested = list(dict.fromkeys(fields))
    return [
        field
        for field in requested
        if not any(field.startswith(f"{other}.") for other in requested)
    ]


def _graft(tree: dict[str, Any], keys: Sequence[str], value: Any) -> None:
    """Place ``value`` at ``keys`` in ``tree``, creating the parent objects on the way."""
    node = tree
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def keep_requested(data: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    """``data`` (one domain's object) cut down to ``fields`` — the one key rule
    for both the prompt's key tree and the group output's key filter.

    Request order is kept; a dotted path keeps only its parents
    (``setup.commands`` → ``{"setup": {"commands": …}}``); a requested object is
    kept whole and wins over its requested children; a requested field the
    data lacks stays absent. The result is the caller's own copy.
    """
    kept: dict[str, Any] = {}
    for field in _top_level_fields(fields):
        try:
            value = _subtree(data, field)
        except KeyError:
            continue
        _graft(kept, field.split("."), copy.deepcopy(value))
    return kept


def slice_skeleton(domain: str, fields: Sequence[str]) -> dict[str, Any]:
    """The domain skeleton cut down to ``fields`` (key paths inside the domain object).

    ``keep_requested`` over the skeleton; a field the schema lacks is left out
    (logged). Raises ``SchemaParseError`` when the schema is broken — an empty
    slice would tell the model to emit nothing and filter every field away.
    """
    schema = _schema(domain)
    if schema.broken:
        raise SchemaParseError(f"Schema {domain!r} has no parseable JSON skeleton")
    sliced = keep_requested(schema.skeleton, fields)
    for field in fields:
        try:
            _subtree(sliced, field)
        except KeyError:
            logger.warning("Schema %r has no field %r — left out of the slice", domain, field)
    return sliced


# ─── Rendering ───


def _names_below(path: str, caps: Mapping[str, int | None]) -> bool:
    prefix = f"{path}."
    return any(name.startswith(prefix) for name in caps)


def _key_label(path: str, leaf: Any, cap: int | None) -> str:
    """``food.ingredients (cap 30)`` — a cap counts list items; an object is one item."""
    return f"{path} (cap {cap})" if cap is not None and isinstance(leaf, list) else path


def _leaf_shape(leaf: Any) -> str:
    if isinstance(leaf, list):
        return _LIST_SHAPE
    return _OBJECT_SHAPE if isinstance(leaf, Mapping) else _SCALAR_SHAPE


def _object_shape(
    tree: Mapping[str, Any], prefix: str, caps: Mapping[str, int | None], labels: list[str]
) -> str:
    """``{"key":shape,…}`` for one object of a slice; appends each requested key's label."""
    members: list[str] = []
    for key, value in tree.items():
        path = f"{prefix}.{key}"
        if path in caps:
            labels.append(_key_label(path, value, caps[path]))
            shape = _leaf_shape(value)
        elif isinstance(value, Mapping) and _names_below(path, caps):
            shape = _object_shape(value, path, caps, labels)
        else:
            raise ValueError(
                f"Slice key {path!r} is not named in caps nor a parent of a named path"
            )
        members.append(f"{json.dumps(key)}:{shape}")
    return "{" + ",".join(members) + "}"


def render_emit_only(
    slices: Mapping[str, Mapping[str, Any]], caps: Mapping[str, int | None]
) -> str:
    """The group tail's key instruction: the emit-only line, then the output shape.

    ``slices`` maps each domain (or modifier) to its ``slice_skeleton``; ``caps``
    MUST name every requested ``domain.field`` path (``None`` = no cap) — it is
    where a requested leaf ends, so a slice key that is neither named nor the
    parent of a named path raises ``ValueError``. ``""`` when no slice has a key.
    """
    labels: list[str] = []
    members = [
        f"{json.dumps(domain)}:{_object_shape(tree, domain, caps, labels)}"
        for domain, tree in slices.items()
        if tree
    ]
    if not labels:
        return ""
    return f"{_EMIT_ONLY_LEAD}{', '.join(labels)}\n{_SHAPE_LEAD}{{{','.join(members)}}}"
