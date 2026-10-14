# Brief 4.3 — gate-4 report skeleton, phases 0–3 filled (parallel with 4.0 b–e, 4.1, 4.2)

Task row (tasks.md 4.3 + plan.md §Phase 4 4.3): "Report (on the final 4.0 models): before/after per
phase (phase walls, first tab, total, cost) for benchmark + regression sets; what each phase bought;
levers not taken with projected gain (outline from plan instead of memory; slicing long videos by
brief ranges; 720p vision; `WORKER_CONCURRENCY=2`; 1 h cache TTL; Haiku vision; m6i)."
Kfir 2026-10-13: "4.3 report skeleton with phases 0–3 filled (the 4.0 section last)" … END:
"`gates/g4.md` (gate 4.0 table; the 4.3 report: before/after per phase from the prod baseline to now,
what each phase bought, levers not taken with projected gain)".

You own `dev/completed/pipeline-1min/gates/g4.md` (new). Read `agent-rules.md` (phase-4 section) and
the sources: `pipeline-1min-brief.md` (targets), `gates/g0-prod-baseline.md` (+ `.json`) = the prod
v8 baseline; `gates/g1.md` + `g1-prod-benchmark.json` (prod after phase 1); `gates/g2.md`
§Post-deploy (prod after phase 2) + its dev rounds; `gates/g3.md` incl. §Prod + `g3-prod-benchmark.json`
(prod v9) + §Cost + §Cache + §Golden; `evidence/A-DIGEST.md` "Numbers to reuse" (phase walls of the
240 s run); `pipeline-1min-context.md` "Numbers to reuse".

## g4.md layout (skeleton — mark coordinator-filled parts `_(after 4.0)_`)
1. Header + verdict line `_(after 4.0)_`.
2. Gate 4.0 table `_(after 4.0)_`: quality vs noise, cost per video ≤ v9 on the 4 benchmarks, first
   extracted tab + total per benchmark, 0 refusals without a fallback on the golden, max prompt tokens
   per call type (new tokenizer), per-call-type before/after cost + latency.
3. What landed (commits) `_(coordinator)_`.
4. **4.3 report** — FILL NOW for phases 0–3, from PROD numbers wherever prod has them (dev only where
   prod has none, labelled):
   - Before/after per phase table per benchmark (T1dQhQAm8Tc, jMq8lEu-of0, uC45_4nnEAI) + regression
     (EqaMrzd9nZU; L51eiJEM45U no-captions if any phase measured it — else say not measured): first
     visible tab, first extracted tab, total (complete/done), cost, phase walls where recorded
     (frames / plan / extraction / synthesis), proxy bytes where recorded. Columns: prod v8 baseline
     (g0) → after phase 1 (g1 prod) → after phase 2 (g2 prod) → after phase 3 (g3 §Prod, medians) →
     after 4.0 `_(after 4.0)_`. Say n per cell; never invent a number — "—" + why.
   - What each phase bought (one short paragraph + the 2–3 numbers that show it, citing the gate).
   - Brief targets vs where we are (first visible ≤ 30 s, every tab ≤ 60–65 s, quality ≥, cost ≤).
   - Levers not taken, with a projected gain computed from recorded numbers (show the arithmetic and
     the source): the seven in the task row + the g3 questions that are levers (Q1 ≤ 2 visual groups,
     Q2 item-range split, Q-first smaller text1, Q-cost prefix trim, Q8 annotations) + the prod
     timeout fix (g3 Q-timeout-prod). Mark a projection "rough" when it rests on one run.
   - 4.0 section `_(after 4.0)_`.
5. Reviews `_(coordinator)_`. 6. Deviations — start with: "4.0a: aioboto3 → aiobotocore (every
   LiteLLM ≥ 1.103 needs boto3 ≥ 1.43.1; aioboto3 abandoned at 15.5.0, pins boto3 < 1.40.62)".
7. Questions for Kfir — start with these (coordinator's list so far):
   - 4.0a: `scripts/migrate-s3-keys.py:105` reads the error code as an attribute of a dict → the
     code is always empty and the script re-raises on every not-yet-migrated key (pre-existing) — fix?
   - 4.0a: LiteLLM loads the repo-root `.env` at import → in-repo venvs (tests, replay, capture) see
     the dev `.env` (one assistant test fails locally; CI has no `.env`) → export
     `LITELLM_MODE=PRODUCTION` in local test runs?
   - 4.0a: `.claude/skills/backend-python/resources/file-uploads.md` still recommends aioboto3 (your
     file).
   - R4a: `_carry_cache_split` (`llm_streaming.py`) is a no-op on LiteLLM 1.105 (the streamed rebuild
     keeps the 1h/5m split; streamed cost = non-streamed) — keep as a guard or remove?
   - R4a (pre-existing): `UsageRecord.litellm_version` is always "" (llm-common `callback.py:264`
     reads `litellm.version`, which doesn't exist) → `importlib.metadata.version("litellm")`?
   - Carried from g3: Q-timeout-prod (sub-second stale-connection timeouts drop the first Anthropic
     calls on prod), Q-vision-variance.
8. For Kfir — actions `_(coordinator)_`.

Rules: numbers only from the gate files / JSON (cite them); "rough" for single-run projections;
no code edits; no LLM spend; no runs. Flow: write → STOP uncommitted, report (what is filled, gaps,
numbers you could not find) → review → fold → commit `p4.3 docs(pipeline-1min): gate-4 report
skeleton, phases 0–3` (stage by path). The coordinator fills the 4.0 parts later.
