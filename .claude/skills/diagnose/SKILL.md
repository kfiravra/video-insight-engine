---
name: diagnose
description: Diagnosis loop for hard bugs and performance regressions. Use when the user says "diagnose" or "debug this", or reports something broken, throwing, failing, flaky or slow. Builds a red feedback loop before any theory.
---

Adapted from mattpocock/skills `diagnosing-bugs` (MIT, a496f55), plus this repo's 3-fix rule.

## 0. Check what is already known

[dev/gotchas.md](../../../dev/gotchas.md) and the auto-memory index first: this repo has a long list of bugs that look new
and are not (caption 429 rotation, SSE phase maps, `tsc --noEmit` being hollow, Qdrant sync calls,
worker not reloading, replica-set `directConnection`). Redact secrets in everything you show.

## 1. Build a feedback loop (this is the skill)

A tight pass/fail signal that goes red on this bug. Spend disproportionate effort here. In order of
preference: a failing test at the nearest seam; a curl against the running service (api :3000,
summarizer :8000, admin :8002); a Python harness calling the stage directly; a Playwright script on
the dev server; replaying a captured payload or transcript; a bisection harness (`git bisect run`)
when the bug appeared between two known states; a differential run (old vs new config).

For non-deterministic bugs raise the reproduction rate (loop 100 times, add load, narrow timing)
until it is debuggable. Tighten the loop: faster, sharper assertion, deterministic (seed, pin time,
isolate FS, no network).

Done when you can name one command you have already run, that drives the real code path, asserts
the user's exact symptom, and finishes in seconds. No red-capable command, no Phase 2. If you
genuinely cannot build one, say so, list what you tried, and ask for an artifact (log dump, HAR,
Langfuse trace id, admin report).

## 2. Reproduce and minimise

Confirm the loop shows the user's failure, not a neighbour. Then cut inputs, callers, config and
steps one at a time, re-running after each cut, until every remaining element is load-bearing.

## 3. Hypothesise

Write three to five ranked, falsifiable hypotheses: "if X is the cause, then changing Y makes the
bug disappear". Show the list to the user before testing; they often re-rank it instantly. Do not
block on the answer.

## 4. Instrument

One variable at a time, each probe tied to a prediction. Prefer a debugger or REPL; otherwise
targeted logs tagged `[DEBUG-xxxx]` so cleanup is one grep. For performance, measure a baseline
first (Langfuse stage timings, `performance.now()`, query plans), then bisect.

## 5. Fix with a regression test

Write the regression test before the fix if a correct seam exists (one that exercises the real
bug pattern); if the only seam is too shallow, say so, that is a finding. Watch it fail, apply the
fix, watch it pass, re-run the original loop. The 3-fix rule: after three failed fixes, stop,
write down what each attempt ruled out, and go back to Phase 3.

## 6. Clean up

Original repro no longer reproduces; regression test passes or its absence is documented; every
`[DEBUG-` tag removed; throwaway harnesses deleted or parked under `dev/diagnostics/`; the
confirmed hypothesis stated in the commit message; a new gotcha recorded in auto-memory.
