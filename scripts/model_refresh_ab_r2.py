"""Model A/B round 2 (pipeline-1min 4.0d, Kfir 2026-10-13, D30): build, check, estimate, run.

Round 2 re-asks three call types (``_model_ab_round2``): extraction with the
completeness rule on four arms, the plan at effort high on four inputs, and
Haiku 4.5's memory twice for its noise floor. Requests are round 1's, captured
again from the cassettes with round 1's TODAY models pinned, plus
EqaMrzd9nZU's plan request rebuilt from its latest v9 dev run (``_model_ab_eqa``).

Before any call the script checks, and stops on a failure:

* identity — every cassette request's cl100k prompt tokens equal round 1's
  stored value (exit 4);
* Eqa — the rebuilt plan request's video half is byte-identical to the one the
  dev run sent (its Langfuse trace; exit 5);
* the wire — every arm's request through the live send path with the HTTP
  layer patched (fake keys, zero network): no param its model rejects (exit 3);
* spend — the estimate (REQUIRED, + OPTIONAL with ``--with-optional``) fits
  what is left of ``--ceiling`` after every row in ``answers.jsonl`` (exit 2).

``--dry-run`` stops there (``dry-run.json`` in the cache dir). The live run
sends the calls one at a time, appends each to ``answers.jsonl`` (a rerun
sends only what is missing) and prints the raw per-arm tables
(``live-summary.json``). Scoring against round 1 and the gate report are a
later step.

Usage (from the repo root)::

    services/summarizer/.venv/bin/python scripts/model_refresh_ab_r2.py --dry-run
    services/summarizer/.venv/bin/python scripts/model_refresh_ab_r2.py [--with-optional]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SUMMARIZER_DIR = _REPO_ROOT / "services" / "summarizer"
for _path in (_SUMMARIZER_DIR, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from _model_ab_capture import capture_cassettes  # noqa: E402
from _model_ab_cost import CallEstimate, PlannedCall, estimate_calls  # noqa: E402
from _model_ab_eqa import eqa_plan_request, gather_eqa, trace_check  # noqa: E402
from _model_ab_report import _call_type_table, score_rows, summarize  # noqa: E402
from _model_ab_round2 import (  # noqa: E402
    ROUND2_CALL_TYPES,
    ROUND2_CEILING_USD,
    ROUND2_RUNS,
    Measured,
    ScheduledCall,
    calibrated_cost,
    first_calls,
    identity_rows,
    measured_sizes,
    request_info,
    schedule,
    split_totals,
    variant_check,
)
from _model_ab_runner import (  # noqa: E402
    answered,
    arm_request,
    is_answered,
    load_answers,
    run_calls,
    spent_usd,
)
from _model_ab_types import AbRequest, reshaped_keys  # noqa: E402
from _model_ab_wire import wire_check  # noqa: E402
from model_refresh_ab import (  # noqa: E402
    _AWS_KEYS,
    _PROVIDER_KEYS,
    _STREAM_KEYS,
    ENV_PATH,
    GATES_DIR,
    load_env_keys,
)

DEFAULT_CACHE_DIR = _REPO_ROOT / "dev" / "active" / "pipeline-1min" / "tools" / "out" / "ab-r2"
ROUND1_JSON = GATES_DIR / "g4-model-ab.json"
# What the estimate adds when the rule lengthens the +rule arms' answers by a fifth.
_OUTPUT_SENSITIVITY = 0.2


# ─── Requests + checks ───────────────────────────────────────────────────────


def collect(cache_dir: Path) -> tuple[list[AbRequest], dict[str, Any]]:
    """The cassette requests round 2 sends (TODAY pinned) + Eqa's plan request."""
    captured = [r for r in capture_cassettes() if r.call_type in ROUND2_CALL_TYPES]
    eqa_data = gather_eqa(cache_dir, ENV_PATH)
    return [*captured, eqa_plan_request(eqa_data)], eqa_data


def _identity_lines(rows: Sequence[dict[str, Any]]) -> tuple[list[str], int]:
    lines = ["identity (cl100k prompt tokens, now vs round 1):"]
    for row in rows:
        verdict = {True: "OK", False: "MISMATCH", None: "new"}[row["equal"]]
        lines.append(
            f"  {row['request_id']:<44} {row['cl100k_now']:>7,} "
            f"{row['cl100k_round1'] or '–':>7} {verdict}"
        )
    return lines, sum(row["equal"] is False for row in rows)


def _trace_lines(check: dict[str, Any], round1_rows: Sequence[dict[str, Any]]) -> list[str]:
    lines = [
        f"Eqa plan vs the dev run's trace (blocks {check['blocks']}, {check['model_traced']}):"
    ]
    for row in check["rows"]:
        if row["identical"]:
            text = "identical"
        elif row["matches"]:
            text = (
                f"matches the trace's {row['truncated_at'] or row['chars'][1]:,} kept chars "
                f"(Langfuse truncated the rest; redacted spans: {row['redacted']})"
            )
        else:
            text = "DIFFERS"
        lines.append(
            f"  block {row['block']}: {text}; cache_control "
            f"{'same' if row['cache_control_equal'] else 'DIFFERS'}; "
            f"chars ours/trace {row['chars'][0]:,}/{row['chars'][1]:,}"
        )
    reads = sorted(
        {
            r["cache_read_tokens"]
            for r in round1_rows
            if r["call_type"] == "plan" and r["arm"] == "A"
        }
    )
    lines.append(
        f"  static block: the dev run's plan read {check['cache_read_traced']} cached tokens; "
        f"round 1's Sonnet 4.6 plan calls (cassettes) read {reads}"
    )
    return lines


def _wire_lines(samples: Sequence[PlannedCall]) -> tuple[list[str], list[dict[str, Any]]]:
    rows = wire_check(samples)
    lines: list[str] = []
    for call, row in zip(samples, rows, strict=True):
        captured = {k: v for k, v in call.request.kwargs.items() if k not in _STREAM_KEYS}
        variant = f" variant={call.arm.variant}" if call.arm.variant else ""
        lines.append(
            f"{row['call_type']:<11} {row['arm']:<14} {row['model'].split('/')[-1]} "
            f"effort={call.arm.effort}{variant} [{call.request.request_id}]"
        )
        lines.append(f"    re-shaped {json.dumps(reshaped_keys(captured, arm_request(call)))}")
        lines.append(f"    wire {json.dumps(row['wire'])}")
        if row["problems"] or row["error"]:
            lines.append(f"    PROBLEM {row['problems']} {row['error'] or ''}")
    return lines, rows


def _wire_samples(calls: Sequence[ScheduledCall], eqa: AbRequest) -> list[PlannedCall]:
    """Each arm's first call, plus the Eqa plan on each plan arm (a new input)."""
    samples = first_calls(calls)
    plan_arms = [s.arm for s in samples if s.request.call_type == "plan"]
    extra = [PlannedCall(eqa, arm, 0) for arm in plan_arms]
    sampled = {(s.request.request_id, s.arm.name) for s in samples}
    return samples + [c for c in extra if (c.request.request_id, c.arm.name) not in sampled]


# ─── Estimate ────────────────────────────────────────────────────────────────


def _table_lines(totals: dict[tuple[bool, str, str], dict[str, Any]], optional: bool) -> list[str]:
    lines = [
        "call type   arm            model                      effort  calls  max prompt"
        "   cost $   out $"
    ]
    for (is_optional, call_type, arm), row in totals.items():
        if is_optional == optional:
            lines.append(
                f"{call_type:<11} {arm:<14} {row['model'].split('/')[-1]:<26} "
                f"{row['effort']:<7} {row['calls']:>5}  {row['max_prompt_tokens']:>10,}  "
                f"{row['cost_usd']:>7.4f} {row['output_usd']:>7.4f}"
            )
    return lines


def _calibrated_line(
    calls: Sequence[ScheduledCall], estimates: Sequence[CallEstimate], measured: Measured
) -> str:
    """The estimate re-sized by round 1's measured prompts and answers (``calibrated_cost``)."""
    totals = {False: 0.0, True: 0.0}
    kinds: Counter[str] = Counter()
    for item, estimate in zip(calls, estimates, strict=True):
        cost, how = calibrated_cost(item, estimate, measured)
        totals[item.optional] += cost
        kinds[how] += 1
    return (
        f"calibrated on round 1's measured sizes: REQUIRED ${totals[False]:.4f}, "
        f"OPTIONAL ${totals[True]:.4f} (answers: {kinds['exact']} exact, {kinds['proxy']} "
        f"same model at another effort, {kinds['none']} not measured — "
        "Sonnet 5.5 never ran extraction)"
    )


def _estimate_lines(
    calls: Sequence[ScheduledCall], estimates: Sequence[CallEstimate], left: float, ceiling: float
) -> list[str]:
    totals = split_totals(calls, estimates)
    required = sum(r["cost_usd"] for (o, _, _), r in totals.items() if not o)
    optional = sum(r["cost_usd"] for (o, _, _), r in totals.items() if o)
    rule_output = sum(r["output_usd"] for (o, _, _), r in totals.items() if not o and r["variant"])
    return [
        "REQUIRED",
        *_table_lines(totals, optional=False),
        f"REQUIRED total ${required:.4f}",
        "OPTIONAL",
        *_table_lines(totals, optional=True),
        f"OPTIONAL total ${optional:.4f}",
        f"REQUIRED + OPTIONAL ${required + optional:.4f} — ceiling ${ceiling:.2f}, "
        f"${left:.4f} left: REQUIRED {'fits' if required <= left else 'OVER'}, "
        f"+OPTIONAL {'fits' if required + optional <= left else 'OVER'}",
        f"sensitivity: +{_OUTPUT_SENSITIVITY:.0%} output on the +rule arms → "
        f"+${rule_output * _OUTPUT_SENSITIVITY:.4f}",
    ]


# ─── Run ─────────────────────────────────────────────────────────────────────


def _live(args: argparse.Namespace, calls: Sequence[PlannedCall], info: dict[str, Any]) -> int:
    """Send the calls (sequentially), then print the raw per-arm tables."""
    load_env_keys(_PROVIDER_KEYS)
    answers = args.cache_dir / "answers.jsonl"
    asyncio.run(run_calls(calls, answers, ceiling_usd=args.ceiling))
    rows = score_rows(info, load_answers(answers))
    summary = summarize(rows)
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spend_usd": round(spent_usd(answers), 4),
        "ceiling_usd": args.ceiling,
        "summary": summary,
        "rows": rows,
    }
    out = args.cache_dir / "live-summary.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"spend ${report['spend_usd']:.4f} over {len(rows)} rows")
    for call_type in ROUND2_CALL_TYPES:
        if call_type in summary:
            print("\n".join(_call_type_table(call_type, summary[call_type])))
    return 0


@dataclass
class Checked:
    """The requests and what the checks found (``code`` non-zero = STOP)."""

    code: int
    requests: list[AbRequest]
    eqa: AbRequest
    record: dict[str, Any]
    measured: Measured


def _checks(args: argparse.Namespace) -> Checked:
    """Requests + identity, Eqa and variant checks."""
    requests, eqa_data = collect(args.cache_dir)
    eqa = next(r for r in requests if r.source == "dev")
    round1 = json.loads(args.round1_json.read_text(encoding="utf-8"))
    identity = identity_rows(requests, round1["requests"])
    lines, mismatches = _identity_lines(identity)
    trace = trace_check(eqa, eqa_data)
    lines += _trace_lines(trace, round1["rows"])
    variant = variant_check(requests)
    lines.append(f"completeness variant: {json.dumps(variant, ensure_ascii=False)}")
    print("\n".join(lines))
    record = {"identity": identity, "eqa_trace_check": trace, "variant": variant}
    code = 4 if mismatches else 5 if not trace["faithful"] else 0
    return Checked(code, requests, eqa, record, measured_sizes(round1["rows"]))


def _pending(calls: Sequence[ScheduledCall], answers: Path) -> list[ScheduledCall]:
    """The scheduled calls not answered yet with today's bytes (a rerun sends only these)."""
    done = answered(answers)
    return [c for c in calls if not is_answered(done, c.call)]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("src.services.llm_model_rules").setLevel(logging.ERROR)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    load_env_keys(_AWS_KEYS)
    checked = _checks(args)
    if checked.code:
        print(f"STOP: {'identity mismatch' if checked.code == 4 else 'Eqa input not faithful'}")
        return checked.code
    lines, wire = _wire_lines(_wire_samples(schedule(checked.requests), checked.eqa))
    print("\n".join(lines))
    if any(r["problems"] or r["error"] for r in wire):
        print("ABORT: an arm request carries a param its model rejects (above)")
        return 3
    answers = args.cache_dir / "answers.jsonl"
    calls = _pending(schedule(checked.requests), answers)
    estimates = estimate_calls(c.call for c in calls)
    left = args.ceiling - spent_usd(answers)
    print("\n".join(_estimate_lines(calls, estimates, left, args.ceiling)))
    print(_calibrated_line(calls, estimates, checked.measured))
    info = request_info(checked.requests)
    _write_dry_run(args, {**checked.record, "wire": wire, "requests": info}, calls, estimates)
    sent = [args.with_optional or not c.optional for c in calls]
    cost = sum(e.cost_usd for e, send in zip(estimates, sent, strict=True) if send)
    if cost > left:
        print(f"ABORT: estimate ${cost:.4f} is above the ${left:.4f} left")
        return 2
    chosen = [c.call for c, send in zip(calls, sent, strict=True) if send]
    return 0 if args.dry_run else _live(args, chosen, info)


def _write_dry_run(
    args: argparse.Namespace,
    record: dict[str, Any],
    calls: Sequence[ScheduledCall],
    estimates: Sequence[CallEstimate],
) -> None:
    payload = {
        **record,
        "runs": [asdict(run) for run in ROUND2_RUNS],
        "estimates": [
            {**asdict(e), "repeat": c.call.repeat, "optional": c.optional}
            for c, e in zip(calls, estimates, strict=True)
        ],
    }
    out = args.cache_dir / "dry-run.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--round1-json", type=Path, default=ROUND1_JSON)
    parser.add_argument("--ceiling", type=float, default=ROUND2_CEILING_USD)
    parser.add_argument("--with-optional", action="store_true", help="also send OPTIONAL calls")
    parser.add_argument("--dry-run", action="store_true", help="checks + wire + estimate only")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
