"""Round 2 section, part 1 (pipeline-1min 4.0d, D30): header, round-1 correction, extraction.

Every number comes from the block ``_model_ab_r2_report.build_round2`` builds:
the run facts (calls, failures, OPTIONAL calls sent, ceiling), the reviewer
clusters (verdict table, named check, keep-the-rule check, the A+rule visual
outlier) and the unblinded reviewer notes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from _model_ab_r2_tables import _cell

Block = Mapping[str, Any]
EXTRACTION_ARMS = ("A", "A+rule", "B+rule", "B-medium+rule", "C+rule")


def table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


def model_label(entry: Mapping[str, Any]) -> str:
    """The model, with its effort where the model takes one (the 5.5 models)."""
    bare = str(entry["model"]).split("/")[-1]
    return f"`{bare}` {entry['effort']}" if bare.endswith("-5-5") else f"`{bare}`"


# ─── Header ──────────────────────────────────────────────────────────────────


def _run_line(block: Block) -> str:
    run, spend = block["run"], block["spend"]
    note = f", {block['ceiling_note']}" if block.get("ceiling_note") else ""
    scope = (
        "REQUIRED scope only (no OPTIONAL call sent)"
        if not run["optional_sent"]
        else f"{run['optional_sent']} OPTIONAL calls sent"
    )
    return (
        f"Kfir's order D30 (`brief-4.0d-r2.md`). **Spend ${spend['total']:.4f}** over "
        f"{run['calls']} calls (ceiling ${block['ceiling_usd']:.2f}{note}; dry-run estimate "
        f"${block['estimate_usd']:.3f}); {scope}; {run['errors']} errors, {run['refusals']} "
        f"refusals, {run['truncated']} truncated answers."
    )


def _identity_line(block: Block) -> str:
    identity, match, eqa = block["identity"], block["token_match"], block["eqa_trace_check"]
    equal = sum(bool(r["equal"]) for r in identity)
    compared = sum(r["equal"] is not None for r in identity)
    changed = ", ".join(f"`{f}`" for f in block["round1_rerender"]) or "none"
    return (
        "Requests: round 1's, re-captured from the cassettes with round 1's TODAY models pinned — "
        f"{equal}/{compared} identical in cl100k tokens; {match['equal']}/{match['pairs']} "
        "(request, model) pairs both rounds answered got the same provider-counted prompt tokens. "
        "EqaMrzd9nZU's plan request was rebuilt by `run_plan` from its latest v9 dev run (Mongo + "
        "S3 + that run's Langfuse trace for the probe answer and the description); its video half "
        f"is byte-identical to the request the run sent (cache read {eqa['cache_read_traced']} = "
        "round 1's static block). The completeness rule — "
        f"`{block['variant']['insertion']['inserted'].strip()}` — sits at the end of "
        "`<quality_rules>` in the 1-hour block of every extraction request (all four arms, "
        "identical bytes). Repeats are numbered after round 1's, so both rounds' answers are "
        "scored together (round 1's arms shown as `r1`). Latency = true median; round 1's tables "
        f"above were re-scored with it — summary fields changed: {changed}."
    )


def header(block: Block) -> list[str]:
    review = block["review"]
    return [
        f"## Round 2 — {block['run_at'][:10]} (UTC)",
        "",
        _run_line(block),
        "",
        _identity_line(block),
        "",
        f"**Blind review.** {review['jobs']} reviewer agents, one per field group, classified all "
        f"{review['items']:,} extraction items of the 10 outputs per field (Haiku 4.5 without the "
        "rule ×2, the four `+rule` arms ×2) with the outputs shuffled and relabelled X1..X10 per "
        "field, then unblinded by key. Each item sits in one cluster (the same step / item across "
        "outputs) with a verdict — supported (quoted with its [m:ss]), padding, unclear. A cluster "
        "counts as present in an output when one of its items is there or an item of the output "
        'merges it (`also_covers`, flagged "merged"); items of one output beyond the first in one '
        'cluster — a duplicate, or one thing split into two items — count as "dup / split". '
        "Verdicts below use supported clusters only; unclear is reported apart.",
        "",
    ]


def correction(block: Block) -> list[str]:
    """Round 1's jMq8 claim, set right by the blind review (round 1 compared against A.0 only)."""
    named = block["extraction"]["named_check"]

    def invented(round_: int) -> str:
        return "; ".join(
            f"{r['output']}'s {', '.join(r['invented'])}"
            for r in named
            if r["invented"] and r["round"] == round_
        )

    partial = [
        f"{r['output']} ({r['real_425_crisp']})" for r in named if r["real_425_crisp"] != "direct"
    ]
    return [
        "**Round-1 correction (jMq8).** Round 1's extraction pick said Haiku 5.5 \"lost real "
        'recipe steps (jMq8: preheat, 375 °F covered bake)". It did not: the transcript has no '
        "preheat, no 375 °F and no foil / covered bake (0 hits); its oven steps are the sauce "
        'into a 225-degree oven, lid ajar [8:21], and the lasagna "in the oven at 425, just to '
        'crisp the top up" [14:40]. Round 1 compared Haiku 5.5 with A.0 only and counted '
        f"{invented(1) or 'nothing'} — Haiku 4.5's own inventions without the rule; round 2's "
        f"extra no-rule run also invented: {invented(2) or 'nothing'}. The real 425 °F crisp "
        "step is in every `+rule` run; outputs without it as an item of its own: "
        + (", ".join(partial) or "none")
        + ". Kfir's named check is evaluated that way below; the preheat / covered bake are not "
        "required.",
        "",
    ]


# ─── Extraction table ────────────────────────────────────────────────────────


def _per_video(arm: str, runs: Sequence[Mapping[str, Any]], value: Any) -> str:
    per_video: dict[str, list[str]] = {}
    for row in runs:
        if row["arm"] == arm:
            per_video.setdefault(row["video_id"][:4], []).append(str(value(row)))
    return " · ".join(f"{v} {'/'.join(n)}" for v, n in per_video.items())


def _arm_row(arm: str, block: Block) -> list[Any]:
    extraction = block["extraction"]
    entry, verdict, runs = extraction["calls"][arm], extraction["arms"][arm], extraction["runs"]
    sizes = {r["video_id"][:4]: r["union_size"] for r in runs}
    return [
        arm,
        model_label(entry),
        entry["calls"],
        f"{entry['latency_p50_ms'] / 1000:.1f} / {entry['latency_p95_ms'] / 1000:.1f}",
        (entry["ttft_p50_ms"] or 0) / 1000,
        f"{entry['input_tokens_mean']:,.0f} / {entry['output_tokens_mean']:,.0f}",
        round(entry["cost_usd"] / entry["calls"], 5),
        verdict["filled_share"],
        verdict["defaulted"],
        _per_video(arm, runs, lambda r: len(r["lacks_vs_ref"])),
        _per_video(arm, runs, lambda r: r["lacks_vs_union"])
        + f" (of {'/'.join(map(str, sizes.values()))})",
        verdict["padding"],
        verdict["unclear"],
        verdict["duplicates"],
        "reference" if arm == "A+rule" else "yes" if verdict["pass"] else "no",
    ]


def extraction_table(block: Block) -> list[str]:
    head = [
        "arm", "model", "calls", "median / p95 s", "TTFT median s", "in / out tok", "$ / call",
        "filled_share", "defaulted", "lacks vs A+rule (T1dQ · jMq8 · uC45, run 0/1)",
        "lacks vs all outputs", "padding", "unclear", "dup / split", "PASS",
    ]  # fmt: skip
    return table(head, [_arm_row(arm, block) for arm in EXTRACTION_ARMS])


def outlier_lines(block: Block) -> list[str]:
    """A+rule.0's T1dQ visual answer: what it adds to the reference, and the figures without it."""
    out = block["extraction"]["outlier"]
    fields = out["fields"]
    video = out["video"][:4]
    shown = "; ".join(
        f"`{f}` " + ", ".join(f"{n} {v}" for v, n in sorted(out["verdicts"][f].items()))
        for f in fields
    )

    def in_fields(row: Mapping[str, Any]) -> int:
        return sum(item.split(":", 1)[0] in fields for item in row["lacks_vs_ref"])

    runs = [r for r in block["extraction"]["runs"] if r["video_id"] == out["video"]]
    without = [r for r in out["runs_without"] if r["video_id"] == out["video"]]
    return [
        f"**A+rule's reference holds one outlier.** {out['output']}'s {video} visual answer "
        f"({', '.join(fields)}) is the only output with snippets / patterns — {shown} — and "
        f"{out['output_tokens']:,} output tokens vs {out['others_output_tokens'][0]}–"
        f"{out['others_output_tokens'][1]} for the other outputs of that request. A+rule's "
        f"filled_share is {out['filled_share']} with it, {out['filled_share_without']} with that "
        f"answer left out, {out['filled_share_as_empty']} with it counted as empty. Of each run's "
        f"{video} lacks vs A+rule, these many are that answer's clusters: "
        + ", ".join(f"{r['output']} {in_fields(r)}" for r in runs if r["arm"] != "A+rule")
        + f". {video} lacks vs A+rule without it (run 0/1): "
        + ", ".join(
            f"{arm} {_per_video(arm, without, lambda r: len(r['lacks_vs_ref'])).split(' ', 1)[1]}"
            for arm in EXTRACTION_ARMS
        )
        + ".",
        "",
    ]


# ─── Named check, keep-the-rule, lacks, section ──────────────────────────────


def named_lines(block: Block) -> list[str]:
    rows = [
        [r["output"], r["round"], ", ".join(r["invented"]) or "–", r["real_425_crisp"]]
        for r in block["extraction"]["named_check"]
    ]
    head = ["output", "round", "invented oven steps (verdict)", "real 425 °F crisp bake"]
    return [
        "**Kfir's named check, corrected.** Which outputs INVENT the preheat / covered bake "
        "(padding — the transcript has neither) and which carry the real 425 °F crisp step "
        '(`bake-uncovered-425-crisp-top`; "merged" = only inside another item):',
        "",
        *table(head, rows),
    ]


def _keep_row(check: Mapping[str, Any]) -> list[Any]:
    noise = check["run_to_run"]
    return [
        check["video_id"],
        check["rule_distance"],
        noise.get("A"),
        noise.get("A+rule"),
        "yes" if check["within_noise"] else "NO",
        f"{check['supported_only']['rule_distance']} vs "
        f"{_cell(check['supported_only']['run_to_run'].get('A'))} / "
        f"{_cell(check['supported_only']['run_to_run'].get('A+rule'))}",
        f"{len(check['supported_lost'])} / {len(check['supported_gained'])}",
        f"{check['padding_items']['A']} → {check['padding_items']['A+rule']}",
    ]


def _rounds(rounds: Sequence[int | None]) -> str:
    if len(set(rounds)) == 1:
        return f"both runs in round {rounds[0]}, back to back"
    return "runs from rounds " + " and ".join(str(r) for r in rounds)


def _keep_caveats(block: Block) -> str:
    checks = block["extraction"]["keep_rule"]
    failing = [c for c in checks if not c["within_noise"]]
    parts = []
    for c in failing:
        noise, diff = c["run_to_run"], c["difference"]
        rounds = c["noise_rounds"]
        parts.append(
            f'The "no" rests on {c["video_id"][:4]} alone ({c["rule_distance"]} vs '
            f"{max(v for v in noise.values() if v is not None)}), one noise pair per arm — A's "
            f"pair: {_rounds(rounds['A'])}; A+rule's: {_rounds(rounds['A+rule'])}. Of the "
            f"{diff['clusters']} clusters that differ across the four A / A+rule pairs, "
            f"{diff['padding']} are padding (the invented oven steps); on supported clusters "
            f"only the distance is {c['supported_only']['rule_distance']} vs "
            f"{max(v for v in c['supported_only']['run_to_run'].values() if v is not None)}."
        )
    clean = block["extraction"]["outlier"]["keep_rule_without"]
    kept = next(c for c in checks if c["video_id"] == clean["video_id"])
    parts.append(
        f"On {clean['video_id'][:4]} the check passes ({kept['rule_distance']} ≤ "
        f"{kept['run_to_run']['A+rule']}) because A+rule's own run-to-run is inflated by the "
        f"A+rule.0 visual outlier: without it A+rule's run-to-run is "
        f"{clean['run_to_run']['A+rule']} and the with/without distance {clean['rule_distance']} "
        f"— within noise: {'yes' if clean['within_noise'] else 'no'}."
    )
    return " ".join(parts)


def keep_lines(block: Block) -> list[str]:
    checks = block["extraction"]["keep_rule"]
    verdict = "yes" if all(c["within_noise"] for c in checks) else "no"
    head = [
        "cassette", "A vs A+rule (mean of 4 pairs)", "A run-to-run", "A+rule run-to-run",
        "within noise", "supported only: rule vs A / A+rule noise", "supported lost / gained",
        "padding items A → A+rule",
    ]  # fmt: skip
    return [
        "**Keep-the-rule check** (Haiku 4.5 with vs without the rule; Jaccard distance over the "
        "supported + padding clusters an output covers; within noise = the with/without distance "
        f"≤ the larger run-to-run distance on every cassette): **{verdict}**.",
        "",
        *table(head, [_keep_row(c) for c in checks]),
        _keep_caveats(block),
        "",
    ]


def lack_lines(block: Block) -> list[str]:
    lines = ["Supported clusters each run lacks against A+rule's two runs (field:cluster):", ""]
    for row in block["extraction"]["runs"]:
        if row["arm"] in ("B+rule", "B-medium+rule", "C+rule") and row["lacks_vs_ref"]:
            lines.append(
                f"- {row['video_id'][:4]} {row['output']}: " + ", ".join(row["lacks_vs_ref"])
            )
    return [*lines, ""]


def _own_lacks(block: Block) -> str:
    counts = [len(r["lacks_vs_ref"]) for r in block["extraction"]["runs"] if r["arm"] == "A+rule"]
    return f"{min(counts)}–{max(counts)}"


def extraction_section(block: Block, notes: str) -> list[str]:
    return [
        "### 1. Extraction — four arms, completeness rule",
        "",
        "PASS = in both runs on every cassette the arm lacks no supported cluster A+rule's two "
        "runs have, filled_share ≥ A+rule's, defaulted ≤ A+rule's. A single run of A+rule itself "
        f"lacks {_own_lacks(block)} clusters of its own two-run union, so no single run of any "
        'arm can meet the first condition; the counts are the comparison. "Lacks vs all '
        'outputs" = supported clusters of the union of all 10 outputs a run misses. A = Haiku '
        "4.5 without the rule (T1dQ: round 1 ×2; jMq8, uC45: round 1 + round 2).",
        "",
        *extraction_table(block),
        *outlier_lines(block),
        *named_lines(block),
        *keep_lines(block),
        *lack_lines(block),
        "**Item-level errors** (reviewer notes, unblinded — what the cluster verdicts do not "
        "capture):",
        "",
        notes.strip(),
        "",
    ]
