"""Extraction field groups — how the requested fields are cut into calls (phase 3.3).

``build_groups`` buckets the text fields by domain (a modifier is its own
domain), splits a bucket by field once Σ weight × expect exceeds
``groupOutputBudget`` (a slice under ``minGroupOutput`` rides with the one
before it — a small first slice with the one after it), keeps at most
``maxTextGroups`` text groups and orders them by the first tab they serve, the
first planned tab's group first (text1 = the warm-up call). Every other field —
the ``waitsForVisual`` ones — forms ONE visual group; ``visual_group`` re-cuts
it at frames-done from what no fired group asked for. Pure; the fields come from ``field_requests``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from ...shared_config.domain_config import GroupingConfig
from .field_requests import FieldRequest


@dataclass(frozen=True)
class ExtractionGroup:
    """One extraction call's share of the requested fields."""

    group_id: str  # "text1".."textN" | "visual"; text1 = the warm-up call
    fields: tuple[FieldRequest, ...]
    visual: bool
    est_output_tokens: int
    tab_ids: tuple[str, ...]


@dataclass
class _Piece:
    """A text group being built: one domain's slice, or a merge of slices."""

    domain: str | None  # None once two domains were merged
    fields: list[FieldRequest]

    @property
    def cost(self) -> int:
        return sum(f.est_output_tokens for f in self.fields)


def _split_by_budget(fields: Sequence[FieldRequest], budget: int) -> list[list[FieldRequest]]:
    """Greedy in plan order: a field joins the open slice while it stays within budget.

    A field is never split; one over budget on its own stands alone.
    """
    slices: list[list[FieldRequest]] = []
    load = 0
    for req in fields:
        if slices and load + req.est_output_tokens <= budget:
            slices[-1].append(req)
            load += req.est_output_tokens
        else:
            slices.append([req])
            load = req.est_output_tokens
    return slices


def _slice_cost(piece: Sequence[FieldRequest]) -> int:
    return sum(f.est_output_tokens for f in piece)


def _absorb_small(slices: list[list[FieldRequest]], minimum: int) -> list[list[FieldRequest]]:
    """A slice estimated under ``minimum`` tokens joins the previous one (L16): its own
    call would re-read the cached transcript for a few hundred tokens of answer. A
    small FIRST slice has no previous one, so it leads the next (plan order kept) —
    the call count must not depend on which field the plan names first."""
    kept: list[list[FieldRequest]] = []
    for piece in slices:
        if kept and _slice_cost(piece) < minimum:
            kept[-1].extend(piece)
        else:
            kept.append(piece)
    if len(kept) > 1 and _slice_cost(kept[0]) < minimum:
        first = kept.pop(0)
        kept[0] = [*first, *kept[0]]
    return kept


def _domain_pieces(
    domain: str, fields: Sequence[FieldRequest], config: GroupingConfig
) -> list[_Piece]:
    """One domain's text fields split by budget, a small slice absorbed by the one
    before it; its scalars join the first slice."""
    text = [f for f in fields if f.domain == domain and not f.visual]
    split = _split_by_budget([f for f in text if f.kind != "scalar"], config["groupOutputBudget"])
    slices = _absorb_small(split, config.get("minGroupOutput", 0)) or [[]]
    slices[0].extend(f for f in text if f.kind == "scalar")
    return [_Piece(domain, s) for s in slices if s]


def _merge_pair(pieces: Sequence[_Piece]) -> tuple[int, int]:
    """The two pieces to merge: the smallest adjacent same-domain pair, else the two smallest."""
    same_domain = [
        (pieces[i].cost + pieces[i + 1].cost, i)
        for i in range(len(pieces) - 1)
        if pieces[i].domain is not None and pieces[i].domain == pieces[i + 1].domain
    ]
    if same_domain:
        _, first = min(same_domain)
        return first, first + 1
    first, second = sorted(range(len(pieces)), key=lambda i: (pieces[i].cost, i))[:2]
    return min(first, second), max(first, second)


def _merge_to_limit(
    pieces: Sequence[_Piece], limit: int, rank: Mapping[FieldRequest, int]
) -> list[_Piece]:
    merged = list(pieces)
    while len(merged) > max(limit, 1):
        first, second = _merge_pair(merged)
        a, b = merged[first], merged.pop(second)
        merged[first] = _Piece(
            a.domain if a.domain == b.domain else None,
            sorted([*a.fields, *b.fields], key=lambda f: rank[f]),
        )
    return merged


def _tab_id(tab: Mapping[str, object]) -> str:
    return str(tab.get("id") or "")


def _first_served(piece: _Piece, position: Mapping[str, int]) -> int:
    unknown = len(position)
    return min((position.get(t, unknown) for f in piece.fields for t in f.tab_ids), default=unknown)


def _holds_planned(piece: _Piece, tab_id: str) -> bool:
    return any(f.reason == "planned" and tab_id in f.tab_ids for f in piece.fields)


def _lead_index(pieces: Sequence[_Piece], tabs: Sequence[Mapping[str, object]]) -> int | None:
    """The piece holding the first planned tab's text field — a visual first tab yields to the next."""
    for tab_id in (_tab_id(t) for t in tabs):
        lead = next((i for i, p in enumerate(pieces) if _holds_planned(p, tab_id)), None)
        if lead is not None:
            return lead
    return None


def _order_pieces(pieces: Sequence[_Piece], tabs: Sequence[Mapping[str, object]]) -> list[_Piece]:
    """By first served tab, with the first planned tab's group moved to the front (text1)."""
    position: dict[str, int] = {}
    for i, tab in enumerate(tabs):
        position.setdefault(_tab_id(tab), i)
    ordered = sorted(pieces, key=lambda p: _first_served(p, position))
    lead = _lead_index(ordered, tabs)
    if lead:
        ordered.insert(0, ordered.pop(lead))
    return ordered


def _group(group_id: str, fields: Sequence[FieldRequest], *, visual: bool) -> ExtractionGroup:
    return ExtractionGroup(
        group_id=group_id,
        fields=tuple(fields),
        visual=visual,
        est_output_tokens=sum(f.est_output_tokens for f in fields),
        tab_ids=tuple(dict.fromkeys(t for f in fields for t in f.tab_ids)),
    )


def visual_group(
    fields: Sequence[FieldRequest], fired: Collection[str] = ()
) -> ExtractionGroup | None:
    """Every requested field whose path no fired group asked for, as ONE group.

    ``build_groups`` passes its text groups' paths, which leaves the
    ``waitsForVisual`` fields. At frames-done the caller re-requests the
    reconciled tabs and passes what already fired, so a field a frames-done
    demotion moved a tab onto (``tech.topics``) still gets extracted. None when
    nothing is left.
    """
    left = [f for f in fields if f.path not in fired]
    return _group("visual", left, visual=True) if left else None


def build_groups(
    fields: Sequence[FieldRequest],
    tabs: Sequence[Mapping[str, object]],
    config: GroupingConfig,
) -> list[ExtractionGroup]:
    """Text groups text1..textN in firing order, then the visual group (if any).

    Above ``maxTextGroups`` the smallest adjacent same-domain slices merge
    first, then the two smallest of any domain.
    """
    rank = {f: i for i, f in enumerate(fields)}
    domains = dict.fromkeys(f.domain for f in fields if not f.visual)
    pieces = [piece for domain in domains for piece in _domain_pieces(domain, fields, config)]
    pieces = _merge_to_limit(pieces, config["maxTextGroups"], rank)
    ordered = _order_pieces(pieces, tabs)
    groups = [_group(f"text{i}", p.fields, visual=False) for i, p in enumerate(ordered, start=1)]
    visual = visual_group(fields, {f.path for g in groups for f in g.fields})
    return [*groups, visual] if visual else groups
