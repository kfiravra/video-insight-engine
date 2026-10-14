# Gate 1a — 1a + 1b (phase 1, first half) — 2026-10-08

**Verdict: PASS with one eval fix, verified on stored output.** 1a + 1b code is complete and committed; quality is up, the
plan no longer waits for frames, the hero lands at memory-done. Two environment incidents cost
two golden passes (details below). Continued straight into 1c + 1d (Kfir's run rules).

## What landed (commits since c57c2a6)
1a.1 eabe954 · 1a.2 bf618ee · 1a.3 e18e948 · 1a.4 c311479 · 1a.5 6137c27 · 1b.1 1e8db2f 8010731 203904e 4b273bf 44e4325 ·
1b.2 b7bb9a5 da3a61d (registry placeholder guard) 505de70 da14284 (eval) · 1b.3 d61ecc8 c62edb1 · 1b.4 4f43993 · 1b.5 530c3ac
db7240a · 1b.6 4b453fb · runner split 9e58acf · early from 1c/1d: 1c.3 provider 99bff3b · 1d.5 LLM retry/fallback 90576a0 ·
1d.8 00a68bc 2673fd4 · incident fix (bot-check proxy rotation) 7304540 e925e2b.

## Timing — replay (1/20 speed, rescaled to recorded seconds)
| | T1dQhQAm8Tc | jMq8lEu-of0 | uC45_4nnEAI |
|---|---|---|---|
| total before → now | 239.5 → **185.0** | 261.9 → **228.4** | 171 rec (219 with the ladder's vision) → **176.9** |
| metadata | 17.1 → 5.0 | 5.6 → 2.2 | 18.7 → 5.0 |
| plan runs | 13.8–41.8 (was after frames ~93) | 4.1–30.4 | 16.8–39.8 |
| hero (first synthesis_complete) | **25.9** | **16.2** | **28.8** |
| frames done / first tab | 92.9 / 182.0 | 129.7 / 226.3 | 119.4 / 175.4 |
Replay acceptance: plan starts before frames-done ✓ ×3 · metadata ≤ 6 s ✓ · one 720p download ✓ ×3 · heartbeats: phase 2
(incl. plan) ✓ max silence 12.7 s; **extraction has none (1d.5, in 1c/1d)**. First tab is still gated by the single
extraction that waits for frames (phase 1 by design; 1c.2 and phase 3 move it).

## 1b.7 checkpoint — dev run jMq8lEu-of0 (frames manifest cached → no download/vision)
metadata 5.6 s · transcript 5.7→25.5 s (caption fetch moved here) · probe 1.0 s · plan 26.5→63.2 s (Sonnet, 1,814 out) ·
memory 26.6→34.6 s → **hero 34.6 s** · extraction 63.2→117.7 s · synthesis ∥ enrichment →134.5 s · **total 134.5 s** · $0.149.
Surprise: the 19.8 s transcript wall (proxy caption fetch) delays the probe → plan/memory → hero by ~10 s vs the brief's ~24 s.

## Suites
summarizer 4,269 passed / 2 skipped + replay 123 / 1 · api 1,062 / 3 · web 1,349 · web `tsc -b` · pyright (CI config) 0 errors.

## Quick golden (10 videos, dev, concurrency 2) — `reports/eval-20261008-051307`
| metric | now | baseline (same 10) | tolerance | |
|---|---|---|---|---|
| quality | **0.907** | 0.897 | ±0.025 | ok |
| duplicateRate | 0.081 | 0.053 | ±0.050 | ok (watch: gaming-op17-unboxing 0.317, static-camera 0.292, vietnam 0.179) |
| faithfulness (informational) | 0.908 | 0.727 | ±0.175 | n=8 |
Assertions: Montreal food vlog **XPASS** (no recipe checklist — the plan's evidence-conditional requirements work);
`food-recipe-story-intro` step_player FAIL in the stored report → the plan DID plan step_player (9 steps, has_steps) and
assembly's existing promotion (≥ 8 steps) rendered step_flow_canvas (same data contract). Fixed in the eval (da14284:
assertions accept a component's promotion target); re-checked on that run's stored output: **8/8 pass**. The stored report
can't be re-scored without a paid re-run; the full golden at g1 re-checks it live.
Spend: quick golden ≈ $2.5 + two failed passes (≈ $1) + checkpoint $0.15.

## Incidents (not code) — 2 golden passes lost
1. **WSL2 out of memory** (7.9 GB VM, swap 100 %) froze Docker Desktop's DNS → Mongo name resolution failed mid-run.
   Fix: stopped this session's own LSP servers (1.7 GB), restarted vie-summarizer. Recorded in dev/gotchas.md + memory.
2. **YouTube bot check on the main proxy exit (prod AND dev, since ~00:04 UTC 10-08)** → every submission failed in 2–5 s.
   Fix 7304540 + e925e2b: rotate the sticky exit on the bot check (not only 429), remember the last working exit.
   Dev `.env` `YOUTUBE_PROXY_EXIT_COUNT=3`. **Prod is down until Kfir acts** (env-changes.md, URGENT row).

## Output-changing changes in 1a + 1b (for review)
Marked transcript in extraction/plan/memory · English-only filler removal (short list) · plan rewrite (full transcript,
briefs/evidence/terms, registry caps, conditional requirements, language block) · tier from the probe (Haiku) not YouTube
category · probe Hint line · memory call (+$0.013/run) · video_memory in {video_context} · chapter_detect gated (15–40 min
videos use time-split chapters) · static-camera videos get frames + vision · moment fill seeks the kept 720p · eval-user
runs skip shared caches.

## Deviations (from brief/plan)
Downloads start after `validate_duration`, not at t=0 (rejected videos cost nothing) · format URLs not shared with
extract_info (different player clients) · filler list cut to never-content phrases · probe 4 s / no retry; plan waits ≤ 5 s ·
memory outline sections ≥ 1 min (brief) not chapter_detect's 2 min · `LLM_CLASSIFIER_MODEL` default = Haiku (D21) ·
1c.3 provider half, 1d.5 LLM-error half and 1d.8 landed before the quick golden (error-path / API only).

## For Kfir
- **Prod incident:** set `YOUTUBE_PROXY_EXIT_COUNT` (≥ 3) / point `YOUTUBE_PROXY_URL` at a working sticky session now
  (env-changes.md). The rotation fix ships with this branch.
- Prompts: the registry placeholder guard (da3a61d) serves the shipped prompt whenever the registry's placeholders differ,
  so deploy-then-register is safe; register on prod right after the deploy (list in g1).
- Dev `vie-api` runs its image's `dist` → API changes need a rebuild (gotcha recorded).
- 0.10 prod baseline is partial (3/6, `g0-prod-baseline.md`); finishing needs the proxy fix on prod.
- No open questions blocking 1c + 1d.
