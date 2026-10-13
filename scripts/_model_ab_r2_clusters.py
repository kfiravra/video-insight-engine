"""Extraction verdicts of the model A/B round 2 (pipeline-1min 4.0d, D30) from reviewer clusters.

``review/<vid>.classified.json`` (unblinded) puts every item id of the dump in
one cluster per field — the same step / item across outputs — with a verdict
(``supported`` / ``padding`` / ``unclear``). A cluster is PRESENT in an output
when one of its members is that output's item ("direct") or one of the
output's items in another cluster lists it in ``also_covers`` ("merged").
Several members of one output in one cluster are duplicates.

Per arm and run, against ``A+rule``'s two runs (their union): the supported
clusters the run lacks and the ones it adds; padding, unclear and duplicate
items. The keep-the-rule check compares Haiku 4.5 with and without the rule
by Jaccard distance over its supported + padding clusters.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any

SUPPORTED, PADDING, UNCLEAR = "supported", "padding", "unclear"
REFERENCE_ARM = "A+rule"
NO_RULE_ARM = "A"
Key = tuple[str, str]  # (field, cluster slug)

# A+rule.0's T1dQ visual answer: the only output with snippets / patterns (12
# snippets, 10 of them padding; ≈ 4.8k output tokens vs 24–358 elsewhere). The
# report shows the A+rule reference with and without it.
OUTLIER: dict[str, Any] = {
    "video": "T1dQhQAm8Tc",
    "output": "A+rule.0",
    "fields": ("tech.snippets", "tech.patterns"),
    "request_id": "cassette/T1dQhQAm8Tc/extraction_visual#0",
    "repeat": 0,
}

# Kfir's named check on jMq8 (corrected after the blind review: the transcript has
# no preheat, no 375 °F and no covered bake — those are invented; the real oven
# step is the uncovered 425 °F bake "just to crisp the top up" [14:40]).
NAMED_CHECK: dict[str, Any] = {
    "video": "jMq8lEu-of0",
    "field": "food.steps",
    "invented": (
        "preheat-oven-375",
        "bake-covered-foil-375",
        "bake-covered-foil-425",
        "preheat-oven-425",
    ),
    "real": "bake-uncovered-425-crisp-top",
}


@dataclass(frozen=True)
class Cluster:
    """One reviewer cluster: a field's same-thing items across outputs, and its verdict."""

    field: str
    slug: str
    verdict: str
    members: tuple[str, ...]
    also_covers: Mapping[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)


def load_clusters(path: Path) -> list[Cluster]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Cluster(
            field=name,
            slug=c["cluster"],
            verdict=c["verdict"],
            members=tuple(c["members"]),
            also_covers={k: tuple(v) for k, v in (c.get("also_covers") or {}).items()},
        )
        for name, clusters in data.items()
        for c in clusters
    ]


def output_of(item_id: str) -> str:
    """``A+rule.0.food.steps.3`` → ``A+rule.0`` (arm names hold no dots)."""
    return ".".join(item_id.split(".")[:2])


def verdicts(clusters: Iterable[Cluster]) -> dict[Key, str]:
    return {(c.field, c.slug): c.verdict for c in clusters}


def presence(clusters: Sequence[Cluster]) -> dict[str, dict[Key, str]]:
    """output → {(field, slug): "direct" | "merged"} for every cluster the output covers."""
    found: dict[str, dict[Key, str]] = {}
    for cluster in clusters:
        for member in cluster.members:
            found.setdefault(output_of(member), {})[(cluster.field, cluster.slug)] = "direct"
    for cluster in clusters:
        for member, slugs in cluster.also_covers.items():
            covered = found.setdefault(output_of(member), {})
            for slug in slugs:
                covered.setdefault((cluster.field, slug), "merged")
    return found


def item_counts(clusters: Sequence[Cluster], output: str) -> dict[str, int]:
    """One output's items per verdict and its duplicates (extra members in one cluster)."""
    counts = {"items": 0, SUPPORTED: 0, PADDING: 0, UNCLEAR: 0, "duplicates": 0}
    for cluster in clusters:
        mine = sum(output_of(m) == output for m in cluster.members)
        counts["items"] += mine
        counts[cluster.verdict] += mine
        counts["duplicates"] += max(0, mine - 1)
    return counts


def _supported(covered: Mapping[Key, str], verdict: Mapping[Key, str]) -> set[Key]:
    return {key for key in covered if verdict.get(key) == SUPPORTED}


def _label(keys: Iterable[Key]) -> list[str]:
    return sorted(f"{name}:{slug}" for name, slug in keys)


@dataclass
class Cassette:
    """One cassette's clusters and the outputs the dump holds (an empty output counts).

    ``rounds``: output → the round that answered it. ``exclude``: output → fields
    whose clusters it is not credited with (a known outlier left out).
    """

    video_id: str
    clusters: list[Cluster]
    outputs: list[str]
    rounds: Mapping[str, int] = dataclasses.field(default_factory=dict)
    exclude: Mapping[str, frozenset[str]] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        self.verdict = verdicts(self.clusters)
        self.covered = presence(self.clusters)

    def covered_by(self, output: str) -> dict[Key, str]:
        dropped = self.exclude.get(output, frozenset())
        return {k: how for k, how in self.covered.get(output, {}).items() if k[0] not in dropped}

    def graded_of(self, output: str, grades: tuple[str, ...] = (SUPPORTED, PADDING)) -> set[Key]:
        return {k for k in self.covered_by(output) if self.verdict.get(k) in grades}

    def supported_of(self, output: str) -> set[Key]:
        return self.graded_of(output, (SUPPORTED,))

    def union(self, outputs: Iterable[str]) -> set[Key]:
        return set().union(*(self.supported_of(o) for o in outputs))

    def runs(self, arm: str) -> list[str]:
        return sorted(o for o in self.outputs if o.rsplit(".", 1)[0] == arm)

    def without(self, output: str, fields: Iterable[str]) -> Cassette:
        """The same cassette with ``output`` not credited for ``fields``."""
        return dataclasses.replace(self, exclude={**self.exclude, output: frozenset(fields)})


def run_rows(cassette: Cassette) -> list[dict[str, Any]]:
    """Per output: supported clusters it lacks / adds vs A+rule's union, vs every output's."""
    reference = cassette.union(cassette.runs(REFERENCE_ARM))
    everything = cassette.union(cassette.outputs)
    rows = []
    for output in cassette.outputs:
        mine = cassette.supported_of(output)
        merged = [k for k, how in cassette.covered_by(output).items() if how == "merged"]
        rows.append(
            {
                "video_id": cassette.video_id,
                "output": output,
                "arm": output.rsplit(".", 1)[0],
                "round": cassette.rounds.get(output),
                "supported_clusters": len(mine),
                "lacks_vs_ref": _label(reference - mine),
                "adds_vs_ref": _label(mine - reference),
                "lacks_vs_union": len(everything - mine),
                "union_size": len(everything),
                "merged_only": len([k for k in merged if k in mine]),
                **item_counts(cassette.clusters, output),
            }
        )
    return rows


def _distance(left: set[Key], right: set[Key]) -> float:
    union = left | right
    return round(1 - len(left & right) / len(union), 3) if union else 0.0


def _distances(cassette: Cassette, grades: tuple[str, ...]) -> dict[str, Any]:
    """Mean A-vs-A+rule distance over every pair, and each arm's run-to-run (two runs only)."""
    plain, ruled = cassette.runs(NO_RULE_ARM), cassette.runs(REFERENCE_ARM)
    graded = {o: cassette.graded_of(o, grades) for o in [*plain, *ruled]}
    across = [_distance(graded[a], graded[r]) for a, r in product(plain, ruled)]
    noise = {
        arm: _distance(graded[runs[0]], graded[runs[1]]) if len(runs) == 2 else None
        for arm, runs in ((NO_RULE_ARM, plain), (REFERENCE_ARM, ruled))
    }
    known = [v for v in noise.values() if v is not None]
    rule = round(mean(across), 3) if across else None
    within = rule is not None and bool(known) and rule <= max(known)
    return {"rule_distance": rule, "run_to_run": noise, "within_noise": within}


def _difference(cassette: Cassette) -> dict[str, int]:
    """Clusters differing between A and A+rule (summed over every pair); how many are padding."""
    plain, ruled = cassette.runs(NO_RULE_ARM), cassette.runs(REFERENCE_ARM)
    differing = [cassette.graded_of(a) ^ cassette.graded_of(r) for a, r in product(plain, ruled)]
    padding = sum(cassette.verdict.get(k) == PADDING for diff in differing for k in diff)
    return {"clusters": sum(map(len, differing)), "padding": padding}


def keep_rule(cassette: Cassette) -> dict[str, Any]:
    """Haiku 4.5 with vs without the rule against each one's run-to-run distance.

    Sets = the supported + padding clusters an output covers (also reported on
    supported clusters only). Within noise: the mean with/without distance is no
    larger than the larger run-to-run distance.
    """
    plain, ruled = cassette.runs(NO_RULE_ARM), cassette.runs(REFERENCE_ARM)
    return {
        "video_id": cassette.video_id,
        "runs": {NO_RULE_ARM: plain, REFERENCE_ARM: ruled},
        "noise_rounds": {
            arm: [cassette.rounds.get(o) for o in runs]
            for arm, runs in ((NO_RULE_ARM, plain), (REFERENCE_ARM, ruled))
        },
        **_distances(cassette, (SUPPORTED, PADDING)),
        "supported_only": _distances(cassette, (SUPPORTED,)),
        "difference": _difference(cassette),
        "supported_lost": _label(cassette.union(plain) - cassette.union(ruled)),
        "supported_gained": _label(cassette.union(ruled) - cassette.union(plain)),
        "padding_items": {
            arm: [item_counts(cassette.clusters, o)[PADDING] for o in runs]
            for arm, runs in ((NO_RULE_ARM, plain), (REFERENCE_ARM, ruled))
        },
    }


def named_check(cassette: Cassette) -> list[dict[str, Any]]:
    """Per output: the invented oven steps it carries, and whether it has the real 425 bake."""
    rows = []
    for output in cassette.outputs:
        covered = cassette.covered_by(output)
        invented = [
            f"{slug} ({cassette.verdict.get((NAMED_CHECK['field'], slug))})"
            for slug in NAMED_CHECK["invented"]
            if covered.get((NAMED_CHECK["field"], slug)) == "direct"
        ]
        real = covered.get((NAMED_CHECK["field"], NAMED_CHECK["real"]))
        rows.append(
            {
                "output": output,
                "round": cassette.rounds.get(output),
                "invented": invented,
                "real_425_crisp": real or "absent",
            }
        )
    return rows


def dump_rounds(dump_path: Path) -> dict[str, int]:
    """output id → its round, for every output the reviewer dump holds (empty ones too)."""
    dump = json.loads(dump_path.read_text(encoding="utf-8"))
    return {o["output"]: int(o["round"]) for f in dump["fields"].values() for o in f["outputs"]}


def arm_verdicts(
    rows: Sequence[Mapping[str, Any]], metrics: Mapping[str, Mapping[str, float]]
) -> dict[str, dict[str, Any]]:
    """Per arm, every cassette and run: PASS = no run lacks a supported A+rule cluster,
    filled_share ≥ A+rule's and defaulted ≤ A+rule's (``metrics``: arm → means)."""
    reference = metrics.get(REFERENCE_ARM, {})
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = found.setdefault(row["arm"], {"lacks": {}, "padding": 0, "unclear": 0})
        entry["lacks"][f"{row['video_id']} {row['output']}"] = len(row["lacks_vs_ref"])
        entry["padding"] += row[PADDING]
        entry["unclear"] += row[UNCLEAR]
        entry["duplicates"] = entry.get("duplicates", 0) + row["duplicates"]
    for arm, entry in found.items():
        mine = metrics.get(arm, {})
        filled = mine.get("filled_share", 0.0) >= reference.get("filled_share", 0.0)
        defaulted = mine.get("defaulted", 0.0) <= reference.get("defaulted", 0.0)
        entry["lacks_none"] = not any(entry["lacks"].values())
        entry["filled_share"], entry["defaulted"] = mine.get("filled_share"), mine.get("defaulted")
        entry["pass"] = entry["lacks_none"] and filled and defaulted
    return found


def field_verdicts(
    cassette: Cassette, output: str, names: Iterable[str]
) -> dict[str, dict[str, int]]:
    """``output``'s items in each field of ``names``, counted per verdict."""
    wanted = set(names)
    found: dict[str, dict[str, int]] = {name: {} for name in wanted}
    for cluster in cassette.clusters:
        if cluster.field in wanted:
            mine = sum(output_of(m) == output for m in cluster.members)
            if mine:
                counts = found[cluster.field]
                counts[cluster.verdict] = counts.get(cluster.verdict, 0) + mine
    return found
