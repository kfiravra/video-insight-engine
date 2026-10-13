"""The "Round 2" section of ``gates/g4-model-ab.md`` (pipeline-1min 4.0d, D30).

Renders the computed round-2 block (``model_ab_r2_offline.py report``): the
header (spend, identity, the blind review), the round-1 correction, then one
part per question of Kfir's order — extraction with the completeness rule,
the plan at effort high (+ where duplicate sources go), Haiku 4.5's memory
noise, and the per-video cost of the two candidate stacks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from _model_ab_r2_render_extraction import correction, extraction_section, header
from _model_ab_r2_render_extraction import model_label as _model
from _model_ab_r2_render_extraction import table as _table
from _model_ab_r2_tables import _cell

Block = Mapping[str, Any]
_PLAN_ARMS = ("A r1", "B r1", "B-medium r1", "B-high")


# ─── 2. Plan ─────────────────────────────────────────────────────────────────


def _plan_row(arm: str, entry: Mapping[str, Any]) -> list[Any]:
    metrics, noise = entry["metrics"], entry["noise"]
    return [
        arm,
        _model(entry),
        entry["calls"],
        f"{entry['latency_p50_ms'] / 1000:.1f} / {entry['latency_p95_ms'] / 1000:.1f}",
        entry["output_tokens_mean"],
        round(entry["cost_usd"] / entry["calls"], 4),
        metrics.get("tabs"),
        metrics.get("distinct_sources"),
        metrics.get("duplicate_sources"),
        metrics.get("required_present"),
        metrics.get("vsA_evidence_agree"),
        metrics.get("vsMedium_evidence_agree"),
        metrics.get("vsA_tabs_jaccard"),
        f"{_cell(noise.get('run_evidence_agree'))} / {_cell(noise.get('run_tabs_jaccard'))}",
    ]


def _flips(entry: Mapping[str, Any], metric: str) -> str:
    counts = (entry.get("lists") or {}).get(metric) or {}
    return ", ".join(f"{k}×{n}" for k, n in sorted(counts.items())) or "none"


def plan_section(block: Block) -> list[str]:
    arms = block["plan"]["arms"]
    head = [
        "arm", "model", "calls", "median / p95 s", "out tok", "$ / call", "tabs", "distinct",
        "duplicate", "required present", "evidence vs A", "evidence vs medium", "tabs J vs A",
        "run-to-run evidence / tabs J",
    ]  # fmt: skip
    flips = [
        f"- {arm}: vs A {_flips(arms[arm], 'vsA_evidence_flips')}; "
        f"vs medium {_flips(arms[arm], 'vsMedium_evidence_flips')}"
        for arm in _PLAN_ARMS
    ]
    return [
        "### 2. Plan — Sonnet 5.5 at effort high (thinking off) vs medium",
        "",
        "Four inputs ×2 (the cassettes + EqaMrzd9nZU). A = Sonnet 4.6 (round 1); B = Sonnet 5.5 "
        'at low and B-medium at medium (round 1, cassettes only). "vs medium" = against '
        "B-medium's run 0 on the same input (Eqa has none: the OPTIONAL medium runs were not "
        "sent).",
        "",
        *_table(head, [_plan_row(arm, arms[arm]) for arm in _PLAN_ARMS]),
        "Evidence flips (keys answered differently):",
        "",
        *flips,
        "",
        *downstream_lines(block),
    ]


def downstream_lines(block: Block) -> list[str]:
    items = block["plan"]["downstream"]
    rows = [
        [
            item["video_id"][:4],
            item["answer"],
            "; ".join(f"{s}: {o}" for s, o in item["outcomes"].items()) or "no duplicate",
            item["group_calls"],
            item["group_calls_deduped"],
            "yes" if item["same_fields_as_deduped"] else "NO",
        ]
        for item in items
    ]
    extra = sum(item["extra_group_calls"] for item in items)
    head = ["video", "answer", "duplicate source → after plan-done", "group calls", "deduped"]
    return [
        "**Downstream — what a duplicate dataSource costs.** Every plan answer (A, B, B-medium, "
        "B-high) ran offline through the code that turns a plan into tabs and extraction groups: "
        f"{block['plan']['code_path']}. Group calls of the final plan vs the same plan with each "
        f"source's later tabs removed: **{extra} extra group calls over all "
        f"{len(items)} answers**. A second tab on a source with the same component is dropped at "
        "plan-done; one with another component stays as a second view of the same data, whose "
        "field is requested once. The plan-done reconcile reads the cassette's Haiku 4.5 memory "
        "answer (round 2's run 1); "
        + (
            ", ".join(sorted({i["video_id"] for i in items if i["memory"] == "none"}))
            + ": no memory input (the memory second opinion is skipped)."
            if any(i["memory"] == "none" for i in items)
            else "every input had one."
        ),
        "",
        *_table([*head, "same fields"], rows),
    ]


# ─── 3. Memory + 4. Cost ─────────────────────────────────────────────────────


def _floor(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The noise floor: the pairs whose two runs come from different rounds."""
    across = [p for p in pairs if p["runs"][0].split("(")[1] != p["runs"][1].split("(")[1]]
    agree = [p["evidence_agree"] for p in across if p["evidence_agree"] is not None]
    deltas = sorted({p["request_id"].split("/")[1][:4] for p in across if p["outline_delta"]})
    return {
        "mean": round(sum(agree) / len(agree), 3) if agree else None,
        "min": min(agree, default=None),
        "delta": max((p["outline_delta"] for p in across), default=0),
        "videos": deltas,
    }


def memory_section(block: Block) -> list[str]:
    memory = block["memory"]
    rows = [
        [
            p["request_id"].split("/")[1],
            " vs ".join(p["runs"]),
            p["evidence_agree"],
            p["outline_delta"],
            p["outline_sections"],
        ]
        for p in memory["pairs"]
    ]
    repeats = memory["repeats"]
    same = all(r["identical"] for r in repeats)
    tokens = ", ".join("/".join(map(str, r["output_tokens"])) for r in repeats)
    floor = _floor(memory["pairs"])
    return [
        "### 3. Memory — Haiku 4.5's run-to-run noise",
        "",
        f"Round 2's two Haiku 4.5 runs per cassette (temperature {memory['temperature']}, sent "
        "back to back) came back "
        + ("byte-identical" if same else "different")
        + f" on {'every' if same else 'some'} cassette (output tokens {tokens}), so the noise "
        "floor is round 1's run against round 2's: evidence agreement mean "
        f"{_cell(floor['mean'])}, min {_cell(floor['min'])}; outline delta up to {floor['delta']} "
        f"section(s) on {', '.join(floor['videos']) or 'no cassette'}. Round 1's Haiku 5.5 vs "
        f"Haiku 4.5 agreement was {_cell(memory['haiku55_vs_a'])}.",
        "",
        *_table(["cassette", "runs", "evidence agree", "outline delta", "outline sections"], rows),
    ]


def _cost_intro(cost: Mapping[str, Any]) -> str:
    return (
        "From the prod benchmark ledger (`g3-prod-benchmark.json`, per call), re-priced with "
        "LiteLLM's map. Stack 1 = Sonnet 5.5 plan + vision, Haiku 5.5 extraction at effort low "
        '(B+rule) + memory ("stack 1 (medium)": the extraction at medium, B-medium+rule); '
        "stack 2 = Sonnet 5.5 plan + vision + extraction, Haiku 5.5 memory; both with the "
        "committed picks (description + translations gpt-6-luna); tier probe, quiz, synthesis "
        "and faithfulness as on prod. A moved call's prompt + cache tokens × the measured "
        "provider-counted prompt ratio of its call type, its output × the measured output ratio "
        "of the arm (plan: B-medium vs Sonnet 4.6; vision: Sonnet 5.5 vs 4.6; memory: Haiku 5.5 "
        "vs 4.5; extraction: the `+rule` arm vs Haiku 4.5 without the rule, same requests). The "
        "cache split is kept as recorded: on the 5.5 models the 512-token minimum would also "
        "cache the extraction's 1-hour block, which today's Haiku 4.5 (4,096 minimum) folds into "
        "the 5-minute write. Today re-priced vs the ledger's `usd`: max difference "
        f"${cost['max_reprice_diff']:.6f} per video."
    )


def cost_section(block: Block) -> list[str]:
    cost = block["stack_cost"]
    stacks = list(cost["ratios"])
    lines = ["### 4. Cost per video — today's v9 vs stack 1 vs stack 2", "", _cost_intro(cost), ""]
    lines += _table(
        ["video", "today $", *(f"{s} $" for s in stacks)],
        [[v, t["today"], *(t[s] for s in stacks)] for v, t in cost["totals"].items()],
    )
    for video, table in cost["videos"].items():
        lines += [f"{video} per call type:", ""]
        lines += _table(
            ["call type", "today $", *(f"{s} $" for s in stacks)],
            [
                [k, round(r["today"], 5), *(round(r[s], 5) for s in stacks)]
                for k, r in table.items()
            ],
        )
    kinds = list(cost["ratios"][stacks[0]])
    ratio_rows = [
        [
            k,
            *(
                f"{cost['ratios'][s][k]['prompt']} / {cost['ratios'][s][k]['output']}"
                for s in stacks
            ),
        ]
        for k in kinds
    ]
    return [
        *lines,
        "Measured ratios (prompt / output, new vs old):",
        "",
        *_table(["call type", *stacks], ratio_rows),
    ]


def render_round2(block: Block, notes: str) -> str:
    """The whole Round 2 section (markdown)."""
    lines = [
        *header(block),
        *correction(block),
        *extraction_section(block, notes),
        *plan_section(block),
        *memory_section(block),
        *cost_section(block),
        "Decisions recorded (D30): the tier probe stays Haiku 4.5 (final); `PIPELINE_VERSION` "
        "bumps to v10 in the picks commit, not before. No picks applied in round 2.",
        "",
    ]
    return "\n".join(lines)
