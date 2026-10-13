"""Requested extraction fields — what the split extraction asks for (phase 3.3).

``requested_fields`` is the union over the reconciled tabs of: each tab's
planned dataSource, the registry ``siblings`` assembly swaps in when it is
empty ("fallbacks", active domains only), the side reads of the component that
renders the tab ("upgrades"), the overview hero's reads of the primary domain
(tips + level — the overview is in every response, planned or not), and the
scalar keys of every requested domain.
Nothing outside that union is requested — an active modifier no tab reads
costs no output — and nothing of an inactive domain: ``active_domain_model``
is the one rule both this request and ``group_merge.validate_group`` apply, so
no group asks for keys its validation would discard (today's single call
emits only the active domains too). A moment_track tab the plan-parse guard
kept on an untagged domain therefore requests nothing; assembly's moment
fallback chain fills it. ``field_groups`` cuts the result into extraction calls.

Pure: registry values (cap, outputWeight, waitsForVisual, siblings) come from
domains.json through ``domain_config``; ``scalars_of`` is injected
(``schema_fields.scalar_fields`` in production).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel

from ...models.domain_types import DOMAIN_MODELS, MODIFIER_MODELS
from ...models.pipeline_types import TabBrief
from ...shared_config.domain_config import (
    NON_EXTRACTION_DATASOURCES,
    DataSourceSpec,
    data_sources,
    valid_modifiers,
)
from .assembly.normalizers import _TIP_FIELDS

logger = logging.getLogger(__name__)

FieldKind = Literal["list", "object", "scalar"]
FieldReason = Literal["planned", "fallback", "upgrade", "scalar"]
ScalarsOf = Callable[[str], Sequence[str]]

# Estimated output tokens of ONE domain's scalar keys together (title, meta, …):
# scalars have no registry entry, so the design fixes their weight per domain.
SCALAR_DOMAIN_WEIGHT = 60

# A modifier key outside the registry (finance has no entries — plan.py's
# ``_bypasses_registry`` keeps tabs on ``finance`` / ``finance.costs``) has no
# cap or outputWeight; it is estimated like one domain's scalars.
UNREGISTERED_FIELD_WEIGHT = SCALAR_DOMAIN_WEIGHT

# Tabs that read no extraction field: assemble_overview builds from synthesis +
# meta and ignores its dataSource; ``enrichment.*`` comes from the quiz call;
# ``frames`` (NON_EXTRACTION_DATASOURCES) from the frames phase.
_SKIPPED_COMPONENTS = frozenset({"overview"})
_SKIPPED_DOMAINS = frozenset({"enrichment"})

# A field requested for several reasons keeps the strongest (earliest listed) one.
_REASON_RANK: dict[str, int] = {reason: i for i, reason in enumerate(get_args(FieldReason))}


@dataclass(frozen=True)
class _SideRead:
    """Fields an assembler reads from each active domain in ``domains`` (only the
    primary's when ``primary_only``)."""

    domains: tuple[str, ...]
    fields: tuple[str, ...]
    primary_only: bool = False


_REVIEW_COMPARISON = _SideRead(
    ("review",), ("pros", "cons", "comparisons", "verdict", "product", "rating")
)

# "Upgrades": what the component rendering a tab reads from the extraction
# besides the tab's own dataSource, keyed by that component. Assembly reads them
# by domain, not through the tab's path. A name that is not a registry field is
# a scalar key.
_SIDE_READS: dict[str, _SideRead] = {
    # assembly/core.py `assemble_response` passes the primary tag to
    # promotion.py `_promote_flash_deck`, whose `_resolve_concept_source` reads
    # extraction[<primary>]["concepts"] (concept_canvas promotion, learning/science).
    "flash_deck": _SideRead(("learning", "science"), ("concepts",), primary_only=True),
    # assembly/assemblers_learning.py `assemble_lyrics_player`: extraction["music"]
    # structure + lyrics, whatever the tab's own source.
    "lyrics_karaoke": _SideRead(("music",), ("structure", "lyrics")),
    # assembly/core.py `assemble_response` keys this read by tab id (exercises /
    # timer): when the planned list is empty it hands extraction["fitness"] to
    # `assemble_exercise_tracker` (exercises, warmup, cooldown); timer is the
    # registry sibling.
    "workout_room": _SideRead(("fitness",), ("exercises", "warmup", "cooldown", "timer")),
    # assembly/core.py `assemble_response` pros_cons tab-id fallback (pros, cons,
    # comparisons) + assemblers_primary.py `assemble_comparison`: leftLabel from
    # review.product, the verdict header from review.verdict + review.rating.
    "comparison": _REVIEW_COMPARISON,
    # assemblers_overhaul.py `assemble_comparison_radar` hands off to assemble_comparison.
    "comparison_radar": _REVIEW_COMPARISON,
    # assembly/core.py `assemble_response`: an empty moment_track tab reads
    # extraction["learning"]["timestamps"].
    "moment_track": _SideRead(("learning",), ("timestamps",)),
}


# The overview hero — assembly adds it to every response, planned or not — reads the
# PRIMARY domain directly: assemblers_learning.py `_collect_tips` (its Tips section)
# over normalizers.py `_TIP_FIELDS`, which attachments.py `_tip_callout_attachment`
# also reads for the tip_callout attachment, and `_extract_level` (meta.difficulty /
# meta.level / difficulty / level → the Level pill; the keys below hold them). Only
# registry tip fields: travel's transportationTips / accommodationTips back core.py
# tab fallbacks, finance.savingTips is a modifier key (never the primary).
_OVERVIEW_LEVEL_KEYS = ("meta", "difficulty", "level")


@dataclass(frozen=True)
class FieldRequest:
    """One key of one domain object that the split extraction asks for."""

    domain: str
    field: str  # key path inside the domain object ("setup.commands", "meta")
    kind: FieldKind
    reason: FieldReason
    tab_ids: tuple[str, ...]  # tabs that wait for this key (none for a plain scalar)
    cap: int | None  # max items asked for; None for scalars and unregistered keys
    expect: int  # items expected (an object or scalar counts as one)
    weight: int  # estimated output tokens per item
    visual: bool  # waits for frame annotations → the visual group

    @property
    def path(self) -> str:
        return f"{self.domain}.{self.field}"

    @property
    def est_output_tokens(self) -> int:
        return self.weight * self.expect


@dataclass
class _Draft:
    """A FieldRequest accumulating tabs and reasons across the plan."""

    domain: str
    field: str
    kind: FieldKind
    reason: FieldReason
    cap: int | None
    weight: int
    visual: bool
    tab_ids: list[str]
    expects: list[int]  # the planned briefs' item counts (none given = empty)

    def absorb(self, other: _Draft) -> None:
        if _REASON_RANK[other.reason] < _REASON_RANK[self.reason]:
            self.reason = other.reason
        for tab_id in other.tab_ids:
            if tab_id not in self.tab_ids:
                self.tab_ids.append(tab_id)
        self.expects.extend(other.expects)


def _from_spec(spec: DataSourceSpec, reason: FieldReason, tab_id: str, expect: int = 0) -> _Draft:
    tab_ids, expects = ([tab_id] if tab_id else []), ([expect] if expect else [])
    domain, field, kind = spec["domain"], spec["field"], spec["kind"]
    weight, visual = spec["outputWeight"], spec["waitsForVisual"]
    return _Draft(domain, field, kind, reason, spec["cap"], weight, visual, tab_ids, expects)


def _unregistered(
    domain: str, name: str, kind: FieldKind, reason: FieldReason, tab_id: str
) -> _Draft:
    """A key with no registry entry: a scalar (weight and visual settled per domain in
    ``_freeze``) or a modifier key such as ``finance.costs``."""
    weight = 0 if kind == "scalar" else UNREGISTERED_FIELD_WEIGHT
    tab_ids = [tab_id] if tab_id else []
    return _Draft(domain, name, kind, reason, None, weight, False, tab_ids, [])


def _modifier_key(domain: str, name: str, scalars_of: ScalarsOf, tab_id: str) -> _Draft:
    kind: FieldKind = "scalar" if name in scalars_of(domain) else "list"
    return _unregistered(domain, name, kind, "planned", tab_id)


def _whole_domain(
    domain: str, registry: Mapping[str, DataSourceSpec], scalars_of: ScalarsOf, tab_id: str
) -> list[_Draft]:
    """A bare ``d`` / ``d.*`` source: d's registry fields, else (finance, which has no
    registry entries) its modifier model's keys."""
    registered = [
        _from_spec(s, "planned", tab_id) for s in registry.values() if s["domain"] == domain
    ]
    model = MODIFIER_MODELS.get(domain)
    if registered or model is None:
        return registered
    return [
        _modifier_key(domain, info.alias or name, scalars_of, tab_id)
        for name, info in model.model_fields.items()
    ]


# ─── Active domains ───


def active_domain_model(
    domain: str, content_tags: Sequence[str], modifiers: Sequence[str]
) -> type[BaseModel] | None:
    """The model ``domain``'s extraction validates with; None when it is not an active
    content tag or modifier with a model (its keys are neither requested nor kept).

    A modifier the planner also wrote into contentTags (nothing filters it) still
    resolves to its modifier model, as the single call's modifier loop validates it.
    """
    model = DOMAIN_MODELS.get(domain) if domain in content_tags else None
    if model is None and domain in modifiers:
        model = MODIFIER_MODELS.get(domain)
    return model


def _active_domains(content_tags: Sequence[str], modifiers: Sequence[str]) -> list[str]:
    """The run's active domains, content tags first, each once."""
    tagged = dict.fromkeys([*content_tags, *modifiers])
    return [d for d in tagged if active_domain_model(d, content_tags, modifiers) is not None]


def _tab_id(tab: Mapping[str, object]) -> str:
    return str(tab.get("id") or "")


def _reads_extraction(tab: Mapping[str, object]) -> bool:
    return tab.get("component") not in _SKIPPED_COMPONENTS and _tab_id(tab) != "overview"


def reads_extraction(tab: Mapping[str, object]) -> bool:
    """Whether the tab renders an extraction field (not the overview, frames or the quiz)."""
    source = str(tab.get("dataSource") or "")
    domain = source.partition(".")[0]
    return (
        _reads_extraction(tab)
        and bool(source)
        and source not in NON_EXTRACTION_DATASOURCES
        and domain not in _SKIPPED_DOMAINS
    )


def reads_active_extraction(
    tab: Mapping[str, object], content_tags: Sequence[str], modifiers: Sequence[str]
) -> bool:
    """Whether the tab renders an extraction field of an active domain — a moment_track
    tab kept on an untagged domain reads none (the moment fallback chain fills it)."""
    domain = str(tab.get("dataSource") or "").partition(".")[0]
    active = active_domain_model(domain, content_tags, modifiers) is not None
    return reads_extraction(tab) and active


def _planned(
    tab: Mapping[str, object], registry: Mapping[str, DataSourceSpec], scalars_of: ScalarsOf
) -> list[_Draft]:
    """The fields a tab's own dataSource names."""
    source = str(tab.get("dataSource") or "")
    domain, _, rest = source.partition(".")
    if not reads_extraction(tab):
        return []
    if rest in ("", "*"):
        return _whole_domain(domain, registry, scalars_of, _tab_id(tab))
    # The brief's count describes the tab's own field, so a bare domain ignores it.
    expect = TabBrief.from_raw(tab.get("brief")).expect
    spec = registry.get(source)
    if spec is not None:
        return [_from_spec(spec, "planned", _tab_id(tab), expect)]
    if domain in valid_modifiers():
        draft = _modifier_key(domain, rest, scalars_of, _tab_id(tab))
        draft.expects = [expect] if expect else []
        return [draft]
    logger.warning("[field_requests] tab %r: dataSource %r is not registered", _tab_id(tab), source)
    return []


def _fallbacks(
    planned: Sequence[_Draft],
    registry: Mapping[str, DataSourceSpec],
    active: Sequence[str],
    tab_id: str,
) -> list[_Draft]:
    """Registry siblings of the planned fields whose domain is active."""
    drafts: list[_Draft] = []
    for draft in planned:
        spec = registry.get(f"{draft.domain}.{draft.field}")
        siblings = (registry.get(path) for path in (spec["siblings"] if spec else []))
        drafts.extend(
            _from_spec(sibling, "fallback", tab_id)
            for sibling in siblings
            if sibling is not None and sibling["domain"] in active
        )
    return drafts


def _side_reads(
    tab: Mapping[str, object],
    registry: Mapping[str, DataSourceSpec],
    active: Sequence[str],
    primary_tag: str | None,
) -> list[_Draft]:
    """The rendering component's side reads in the domains it reads them from."""
    rule = _SIDE_READS.get(str(tab.get("component") or ""))
    if rule is None:
        return []
    primary = [primary_tag] if primary_tag else []
    drafts: list[_Draft] = []
    for domain in (d for d in (primary if rule.primary_only else active) if d in rule.domains):
        for name in rule.fields:
            spec = registry.get(f"{domain}.{name}")
            drafts.append(
                _from_spec(spec, "upgrade", _tab_id(tab))
                if spec
                else _unregistered(domain, name, "scalar", "upgrade", _tab_id(tab))
            )
    return drafts


def _overview_reads(
    primary_tag: str | None,
    content_tags: Sequence[str],
    registry: Mapping[str, DataSourceSpec],
    scalars_of: ScalarsOf,
) -> list[_Draft]:
    """The primary domain's tip fields and level key, once, with no tab waiting for them."""
    if primary_tag is None or primary_tag not in content_tags:
        return []
    tips = (registry.get(f"{primary_tag}.{name}") for name in _TIP_FIELDS)
    drafts = [_from_spec(spec, "upgrade", "") for spec in tips if spec is not None]
    scalars = scalars_of(primary_tag)
    levels = [key for key in _OVERVIEW_LEVEL_KEYS if key in scalars]
    return drafts + [_unregistered(primary_tag, key, "scalar", "upgrade", "") for key in levels]


def _put(drafts: dict[tuple[str, str], _Draft], draft: _Draft, active: Sequence[str]) -> None:
    """Request ``draft`` (merged into an earlier request of its key) — never for an
    inactive domain, whatever the reason: its group's validation would discard it."""
    if draft.domain not in active:
        return
    existing = drafts.get((draft.domain, draft.field))
    if existing is None:
        drafts[(draft.domain, draft.field)] = draft
    else:
        existing.absorb(draft)


def _scalar_weights(drafts: Sequence[_Draft]) -> dict[tuple[str, str], int]:
    """Each domain's SCALAR_DOMAIN_WEIGHT spread over its scalar keys."""
    names: dict[str, list[str]] = {}
    for draft in drafts:
        if draft.kind == "scalar":
            names.setdefault(draft.domain, []).append(draft.field)
    weights: dict[tuple[str, str], int] = {}
    for domain, keys in names.items():
        base, extra = divmod(SCALAR_DOMAIN_WEIGHT, len(keys))
        for i, key in enumerate(keys):
            weights[(domain, key)] = base + (1 if i < extra else 0)
    return weights


def _visual_only_domains(drafts: Sequence[_Draft]) -> frozenset[str]:
    """Domains whose every non-scalar field waits for visual: their first group is the visual one."""
    fields = [d for d in drafts if d.kind != "scalar"]
    text = {d.domain for d in fields if not d.visual}
    return frozenset(d.domain for d in fields if d.visual and d.domain not in text)


def _expect(draft: _Draft) -> int:
    """The brief's count (clamped to the cap), else half the cap; scalars and objects are one."""
    if draft.kind == "scalar":
        return 1
    given = max(draft.expects, default=0)
    if draft.cap is None:
        return max(given, 1)
    return max(min(given, draft.cap) if given else math.ceil(draft.cap / 2), 1)


def _freeze(drafts: Sequence[_Draft]) -> list[FieldRequest]:
    shares, visual_only = _scalar_weights(drafts), _visual_only_domains(drafts)
    frozen: list[FieldRequest] = []
    for d in drafts:
        scalar = d.kind == "scalar"
        weight = shares[(d.domain, d.field)] if scalar else d.weight
        visual = d.domain in visual_only if scalar else d.visual
        head = (d.domain, d.field, d.kind, d.reason, tuple(d.tab_ids), d.cap)
        frozen.append(FieldRequest(*head, _expect(d), weight, visual))
    return frozen


def requested_fields(
    tabs: Sequence[Mapping[str, object]],
    content_tags: Sequence[str],
    modifiers: Sequence[str],
    scalars_of: ScalarsOf,
    *,
    primary_tag: str | None = None,
) -> list[FieldRequest]:
    """Every field the reconciled tabs read, in plan order (scalars last).

    Per tab: its planned field(s), then the planned fields' active siblings,
    then its component's side reads (``primary_tag`` scopes the primary-only
    ones); then the overview's reads of the primary domain (``primary_tag``);
    finally the scalar keys of every domain that has a requested field.
    A field several tabs read is requested once, with all their tab ids and its
    strongest reason. Only active domains' fields (``active_domain_model``).
    """
    registry = data_sources()
    active = _active_domains(content_tags, modifiers)
    drafts: dict[tuple[str, str], _Draft] = {}
    for tab in (t for t in tabs if _reads_extraction(t)):
        planned = _planned(tab, registry, scalars_of)
        fallbacks = _fallbacks(planned, registry, active, _tab_id(tab))
        upgrades = _side_reads(tab, registry, active, primary_tag)
        for draft in [*planned, *fallbacks, *upgrades]:
            _put(drafts, draft, active)
    for draft in _overview_reads(primary_tag, content_tags, registry, scalars_of):
        _put(drafts, draft, active)
    for domain in dict.fromkeys([d.domain for d in drafts.values()]):
        for name in scalars_of(domain):
            _put(drafts, _unregistered(domain, name, "scalar", "scalar", ""), active)
    return _freeze(list(drafts.values()))
