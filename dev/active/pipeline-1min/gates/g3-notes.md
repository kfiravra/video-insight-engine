# Gate 3 — running notes (coordinator; folded into g3.md at the end)

## Deviations (inside the nine tasks — decided, reported)
- A1 per-group `tab_ready` = `final:false`; closing re-emit sends every tab `final:true` once (brief says final per group; the brief's own "attachments at the final re-emit" + the 2.1 ledger contract make an early final wrong).
- A5 "code terms in the transcript" read as the existing evidence definition ("actual code, commands or configuration"); not widened — widening keeps mention-only talks' code tabs (T1dQhQAm8Tc).
- 3.1 builds no RULES/COMMON-MISTAKES or example slicing — Kfir's 3.4 "full schema block + emit ONLY keys" makes them dead.
- 3.3 requested set = tabs only (no unplanned modifier fields; finance keys only when a tab reads `finance`/`finance.*`) — spec "still no unplanned fields"; output-changing (see Q-mod).

## Questions for Kfir (outside the nine tasks / need data)
- Q1 one visual group for code tutorials (snippets+patterns+cheatSheet+setup after frames-done) can miss 60–65 s → allow ≤ 2 visual groups?
- Q2 one heavy list field (jMq8 food.steps ≈ 42 s, T1dQ learning.concepts ≈ 33 s) is the long pole → item-range split?
- Q3 keep the 1h marker on block 1 although it is below Haiku 4.5's 4,096-token minimum on all three benchmarks (no-op there)?
- Q4 `GROUP_RELEASE_FALLBACK_S` = 1.5 s vs measured text1 TTFT (`ttftMs` recorded) → tune?
- Q5 the rendered `<video_memory>` evidence `✗` line uses `merge_evidence` (either reader false) while reconcile uses strict doubly-false → align?
- Q6 the false path's `extraction_merger` keeps longest-string + hash-order quirks (C12 policy only on the group path) → align later?
- Q7 rollback lever = `EXTRACTION_PARALLEL=false` in prod `.env` (not applied; env only).
- Q8 text groups get no `<visual_annotations>` (B.5) — food steps lose visual cues → annotations for text groups, or `waitsForVisual` for food.steps on HIGH?
- Q-mod synthesis / quiz / faithfulness no longer get narrative/finance modifier output when no tab reads it (today's single call emits it) → keep, or request modifiers for context?
- Q-fin `finance.costs` / `finance.savingTips` have no registry entries (no cap/outputWeight; flat 60-token estimate) → add them?
- Q-workout `workout_room` side reads (warmup/cooldown/timer ≈ 3.45k est. output tokens, up to 4 fitness text groups) are only a fallback when the exercises list is empty (core keys them by tab id) → narrow?
- Q-schema (dev only) — none open; 3.1's schema parse is cached per (name, text) so registry/disk edits are followed.
- Q-code-memo a code tab kept by screen code at frames-done while `<video_memory>` (rendered at plan-done, cached head block) still says `has_code ✗` → the extraction prompt contradicts itself on such videos; re-rendering would break the shared head block.
- Q-science science's required `spot_explorer` has no defaultTab → never backfilled (pre-existing in assembly).
- Q-arrow the screen-code pattern `=>` also matches arrows on prose slides (false keep).
- Q-demote-scope (3.2 deviation) demotion candidates = the source's siblings + its own domain only (not other active domains — else T1dQ's workflow tab becomes a learning tab); widen?
- (fixed in 3.2) `_drop_accounting` no longer counts reconcile-backfilled/filled tabs in `tabsDesigned`.
- (R32, declared output) the three new `has_code` gates feed `video_memory._evidence_ranges` → tech videos planning patterns/cheatSheet/setup get `has_code ✓ <ranges>` in `<video_memory>` (extraction head, enrichment, synthesis input).
- (R32, A4 report) `triage_complete` + `meta` now wait for memory: when memory retries (25 s timeout + 1 retry) the UI phase + pills arrive up to ~25 s after plan-done; hero unaffected (`synthesis_complete` is memory's own event).
- 3.2 now emits a fresh `meta` at frames-done when deferred code tabs drop/demote (and re-fills to 3).
- (3.4) LiteLLM 1.91 streamed usage drops Anthropic's 1h/5m `cache_creation` split (sent only in `message_start`; last-wins merge) → text1 records `cacheWrite1h/5m` = 0 and the ledger prices a 1h write at the 5m rate (≈ $0.003–0.004 under per 1h write; no effect on the 3 benchmarks — block 1 < 4,096). R34a checks whether it is recoverable from the first chunk.
- (3.4) Q7 refinement: `EXTRACTION_PARALLEL=false` also serialises long videos' chunked batches (v8 prod ran them in parallel) → split the lever into two flags?
- (3.4) streaming probe (A16, DEV, Haiku 4.5): cache read/creation totals survive streaming, ledger callback once; TTFT 852 ms cold write / 577 ms read on a ~5k prefix (< the 1.5 s release fallback). Spend $0.007–0.011.
- (3.4 deviation) an empty requested set → empty extraction instead of today's error; the visual group uses the same release rule (warm or 1.5 s) as other groups; `pipeline.timing` v3 (`ttftMs`), new `split_extraction` phase; `extraction` phase = plan-done → last group on the split path.
- (3.4) replay (synthesized group timings, speed 1, from run start): T1dQ complete 98.5 vs 163.8 s today · jMq8 117.8 vs 200.4 · uC45 98.0 vs 140.8 (frames-done 75.3 / 114.1 / 87.3 s dominates; cassettes are v8 recordings).
- (R34a) split-path timing: `transcript_frames` spans extraction (member inside the phase-two group), DONE-log `extraction=` nested in `split_extraction`, `frames` includes `visual_inject` → phase walls not comparable 1:1 with v8 — compare first tab / complete / done instead.
- (R34a) with LiteLLM 1.91 `on_first_chunk` / `ttftMs` fire at the first CONTENT token (message_start is emptied and skipped) — just after the cache entry is readable; matches Kfir's "first token".
- (3.5, A5 by design) absent instead of default: `_has_meaningful_data` no longer sees numeric/bool defaults (travel/review/fitness/project/science) → an effectively empty split extraction skips the quiz (`empty_extraction`); the overview no longer copies default `""` scalars; the `pipeline.extraction_quality` log uses requested fields on the split path → not comparable across modes.
- (3.5) unrequested keys absent on the split path (T1dQ learning.keyPoints/takeaways, tech.cheatSheet/frameworks/languages/setup; jMq8 food.equipment/nutrition/substitutions; uC45 gaming.walkthrough) — the intended "unused extraction" cut; split-mode tab payloads on the 3 cassettes equal the false path's.
- (3.6, replay split mode, synthesized group timings, s at speed 1; cassettes = v8 recordings) first assembled tab T1dQ 43.0 · jMq8 43.8 · uC45 46.0 (pre-3.6 split path ≈ 85–113); complete 89.4 / 116.8 / 96.6 (3.4: 98.5 / 117.8 / 98.0; today's path 163.8 / 200.4 / 140.8). jMq8 + uC45 complete waits on frames-done (113.1 / 93.0).
- (3.6) quiz input on the split path = its owning TEXT groups' merged output (B.6) → T1dQ's quiz no longer sees code snippets (visual group); DONE-line `quiz=` = the late group's wait, not the call wall (call wall in llmCalls).
- (3.6 deviation) assembly-added tabs (backfills, filmstrip, minimum-3 fallbacks) appear only at the closing re-emit; a per-group tab is re-sent when its position changes.
- (R36b g3) B.6 quiz input is built when its owning text groups land (before frames-done) → `enrich_quiz(tabs=plan.tabs)` lists plan-done tabs incl. deferred code tabs frames-done may drop → accept or re-read at frames-done?
- (R36a g3) per-tab attachments (stat_banner, frame_strip, tip_callout) stripped from per-group payloads → pop in at the closing re-emit; keep, or strip only quick_quiz + links?

## Dev cold runs after 3.6 (37c5678, eval user, 2026-10-13 ~02:30 local; spend ≈ $0.69 = 0.192 + 0.181 + 0.158 + 0.156)
| video | provisional | first assembled | complete / done | tabs | cost |
|---|---|---|---|---|---|
| T1dQhQAm8Tc | 29.5 | 34.7 | 47.0 / 47.0 | stack_overview, harnesses, key_moments, quiz, overview | $0.192 |
| jMq8lEu-of0 | 29.3 | 41.4 | 59.7 / 59.7 | moments, tips, ingredients, steps, overview | $0.181 |
| uC45_4nnEAI | 24.5 | 43.9 | 74.9 / 74.9 | highlights, set_facts, overview — **pulls (tier_list) DROPPED** | $0.158 |
| EqaMrzd9nZU | 25.6 | 69.1 | 71.0 / 80.2 | moments, code, quiz, overview — **concepts DROPPED** | $0.156 |
**BUG found (3.4):** the streamed warm-up call `extraction_text1` returns SCRAMBLED text on long answers (deltas out of order — also in LiteLLM's own Langfuse rebuild) → "group answer is not a JSON object" → text1 `failed` on 3/4 runs (uC45 pulls + Eqa concepts dropped `assembler_returned_none`; jMq8 text1 failed too, ingredients still shipped via fallback). Non-streamed groups fine. Debug agent on it → `p3.4 fix`. These numbers are pre-fix; re-run after the fix.
- Eqa: plan said has_code=false, memory true → reconcile backfilled the required code_playground (tech.snippets → visual group) — first assembled tab waits for frames-done (69.1 s).
- (3.7 g3) long video without chapters/outline on the split path: no force-split fallback (today force-splits > 20k words) → one chunk; each chunk's text1 writes block 1 (no-op < 4,096); up to 5 s description + 25 s chapter_detect wait at plan-done on long videos. (R37 judging whether the force-split gap is a 3.7 fix.)
- (3.7 deviation) `plan_chunks` async; imports today's private chunk helpers (no false-path byte risk); key frames sliced per chunk on the split path (today's chunked batches got all of them).
- (3.8 g3, pre-existing since 41501dd on BOTH paths) after "Extraction failed to produce data" the orchestrator still runs assembly → previews, `complete`, `done` follow the error and the row is saved `completed` (overview + Key Info), overwriting FAILED → stop the run after the error?
- (3.8 deviations) `degraded` now set by any failed group (`flag_failed_groups`), not only when a timestamped field exists; only "no usable data" drops of tabs that waited on a failed field are relabelled `extraction_group_failed`; one extra NON-streamed call per unusable answer (`retried: parse|empty` on the `_groups` row); text1's waiting groups released before its retry.
- **(3.4 fix 6988803) scrambled streamed text1 — root cause:** LiteLLM 1.91 `stream_chunk_builder` sorts streamed chunks by `_hidden_params["created_at"]` (`time.time()`); the WSL2 wall clock steps back ~0.9–1.2 s every 30 s → a stream crossing a step gets ~2 s of deltas reordered (our rebuild AND LiteLLM's Langfuse/ledger rebuild). Fix: `_keep_arrival_order` (monotonic stamps) before both rebuilds; offline repro with LiteLLM's real Anthropic stream; $0. Committed by the debug agent BEFORE its review → reviewed after commit (R34c) — process deviation, stated.
- (6988803 g3) vie-assistant streams too (`services/assistant/src/services/llm_provider.py:270`): user-visible text fine, but its LiteLLM Langfuse output on long chats is likely scrambled the same way on WSL — not checked/fixed (outside the nine tasks). Any `time.time()` duration on dev can be off by ~2 s.
- (R38 g3) all groups fail → the error now arrives up to ~120 s later (every group asked twice per spec); cost bound ≤ 2 `call_llm_with_retry` per group per chunk (second = single attempt, no fallback).
- (R34c g3) vie-assistant streams: its text + Langfuse log are arrival-order (never scrambled), but its usage (LiteLLM sorted rebuild) + the ledger callback go through the same sort → a stream crossing a WSL clock step can record wrong tokens/cost (dev only; assistant LiteLLM unpinned `>=1.80.0`) → apply the same stamp fix there? Prod EC2 slews (chrony) → the bug is dev-box only.
- (3.7 g3) a long video whose transcript is metadata-only can pass the transcript check and be force-split into tiny chunks (today: one call).
- (3.8) an answer saying "nothing here" (`{}` / the group's keys null) = ok-empty (no extra call, no degraded); if EVERY group answers so, the run gets an empty extraction instead of today's error. `extraction_record` keeps `_groups` rows even with no data.

## Dev cold runs post stream-fix (6988803 + 3.7/3.8, 56ef5c3; 2026-10-13 ~03:11 local; spend $0.629 = 0.118 + 0.189 + 0.158 + 0.164)
| video | provisional | first assembled | complete / done | final tabs | groups | cost |
|---|---|---|---|---|---|---|
| T1dQhQAm8Tc | 27.3 | 48.4 | 92.5 / 92.5 | stack_overview, moments, quiz, overview | text1 ok · text2 **failed (empty ×2)** | $0.118 |
| jMq8lEu-of0 | 30.1 | 43.8 | 67.6 / 67.6 | cook_steps, ingredients, moments, tips, overview | 3 ok | $0.189 |
| uC45_4nnEAI | 26.2 | 41.5 | 77.2 / 77.2 | highlights, pulls, set_facts, overview | 3 ok | $0.158 |
| EqaMrzd9nZU | 29.0 | 33.5 | 79.3 / 89.9 | code, concepts, moments, when_to_use, quiz, overview | 4 ok | $0.164 |
- Stream fix confirmed live: every text1 ok (uC45 pulls + Eqa concepts back).
- Decision 7 live: T1dQ `agent_workflow` (step_player ← tech.patterns) deferred at plan-done (plan + memory has_code false) → dropped at frames-done (no screen code).
- **BUG (3.3 × 3.5):** T1dQ's plan tagged `tech` only; the kept-for-moment-fallback `moments` tab (← learning.timestamps, inactive domain) was still REQUESTED → text2 answered correctly → `validate_group` discards inactive domains → empty → retry → failed → degraded, ~60 s + 4.8k out tokens wasted. Fix agent: never request an inactive domain's fields (`p3.3 fix`).
- T1dQ droppedTabs has an entry with an EMPTY id (`''`, `requirement_evidence_false`) — assembly's requirement record for a non-backfilled required component → final review.
- (3.9) v8 → v9 in `pipeline-version.json`; prompts: only `extraction_group_job.txt` changed since origin; DEV already serves v1 = file (45 unchanged) → PROD staging list for the push word = `summarizer:extraction_group_job` only (`register_prompts.py --commit --label pipeline-1min`); the plan static half changes through `domains.json` toolkit rendering (first prod plan writes a new 1h entry ≈ $0.10).
- (3.9) demo re-run script `scripts/demo_rerun.py` (NOT run): dry run on dev = 38 v8 videos ≈ $5.70–12.54 (incl. the 10-h `nMfPqeZjc2c` — band understates it); prod seed `demo-data/20260915T221548Z/urls.txt` = 10 videos ≈ $1.50–3.30. `--apply` logs in as the DEMO user (eval rows are never served, D25).
- (3.9 g3) demo user is free tier: `VIDEO_DAILY_LIMIT=5`/24 h + `USER_COST_LIMIT_FREE=$1` → a 10-video prod re-run takes 2+ days unless the limits are raised (env, Kfir's call); prod Mongo unreachable from the host → prod run uses `--urls`; every listed video lands in the demo user's library.
- (3.9) api suite: 1 flaky failure (`cost-monitor` concurrency; passes alone 27/27, also fails without 3.9).
- (R33b g3) plan-parse guard `_inactive` returns False when `data_source(path)` is None → bare `<d>` / `<d>.*` / modifier-field tabs on an UNTAGGED domain (`review`, `learning.*`, `finance.costs` with tags=[tech]) pass with no reconcile record, now request nothing, and are dropped at assembly with a generic reason → re-point/drop them as `reconcile_inactive_domain`?
- (R39 g3) `deploy.yml` = `git pull` + `up -d --build`: a container whose image did not change is not recreated and its single-file bind mount of `pipeline-version.json` keeps v8 → if the api stayed on v8 while the summarizer wrote v9, new docs would look stale + dedup would attach users to v8 rows. This deploy likely recreates all three (their images change), but nothing checks it → add a restart of api/summarizer/worker or a boot `pipeline_version` match check?
- (R39 g3) after a version bump, dev re-run + demo-export/import cannot refresh prod (dedupKey carries v9; export's "attaches without a pipeline run" stops holding) → prod demo refresh = `demo_rerun.py --urls --api-url <prod> --apply` only?
- (3.9 g3) with `--urls`/`--all-cache` a run that fails and is restored (`restoreServedVersion`) before the script reads the result is reported ok → `scripts/_eval_api.py` `to_output` should also return the submission's `videoSummaryId` (outside 3.9's files). Dev dry run: demo user's library = 1 video (F3lL98Pj90o); `--all-cache` 38 (32 × band ≈ $4.80–10.56 + 6 chunked unpriced).
- (R40B data, dev ledger) **extraction spend split vs the latest v8 single call, same videos:** T1dQ $0.046 ($0.035 without the since-fixed text2 retry) vs $0.022 · jMq8 $0.041 vs $0.038 · uC45 $0.037 vs $0.028 · Eqa $0.057 vs $0.041 → +8–60 % on extraction (schemas + example now in the cached prefix: per-run write 8.4–10.2k tok vs 6.5–7.4k; per-group tails; side reads). v8's single call wrote its prefix every run and never read it.
- (R40B data, Q4) text1 ttftMs 703 / 582 / 612 / 658 ms; released groups start 1–6 ms after text1's first chunk; every released call read the full prefix (cacheRead = text1's write); the 1.5 s fallback never fired.
- (R40B data, Q3) `cacheWrite1hTokens` = 0 on every call of the 4 runs (block 1 < 4,096) — the 5m entry holds blocks 1 + 2.
- (R40B g3, pre-existing) instant "Timeout" drops (14–15 ms, stale pooled connection after ~26 s idle) + 1 s backoff hit description_analysis / tier_probe / faithfulness / synthesis / a uC45 visual group on every dev run → check prod `llmFailures`.
- (R40B) prod deploy recreates api (types dist), summarizer, worker, langfuse-init → all on v9; `vie-langfuse-init` promotes `extraction_group_job` on `up` but without ordering vs the worker; first prod plan pays one 1h write of the 16,290-token static half ≈ $0.10.
- (R40A g3) critical coverage (`compute_extraction_coverage`) needs learning.timestamps / narrative.keyMoments / news.storyTimeline items; on the split path news.storyTimeline is requested only when a tab reads it → a news run with a truncated transcript loses the `critical`/`degraded` signal → accept?
- (R40A) preview positions = final positions on all 4 dev videos; every tab exactly one final, overview last; every registry/scalar/side-read field is a schema + Pydantic dump key.
- (final fix) A1 extended: tech's REQUIRED code_playground is deferred to frames-done when has_code is doubly-false and no code tab was planned (screen code → backfill before the visual cut + fresh meta; else `not_backfilled`); a strip-only quiz then also waits for frames-done. Heavy groups get up to 300 s (estimate ÷ 60 tok/s + 30, clamp 120–300). Registry misses negative-cached 60 s (in `langfuse_client.fetch_prompt_with_obj`; `max_retries=0` NOT used — in the 2.60 SDK it means retry forever).
- (final fix g3) (`.env*.example` comments updated in the p3 fix commit) domains.json `requirementEvidenceNote` does not mention the deferral (editing domains.json = shared rebuild + recreate) → left for 4.1.
- (R41 g3) a frames-done code backfill on a FULL plan (6 tabs) trims a planned tab `over_max` at frames-done — on the split path its text group already fired (output paid, preview withdrawn at `complete`) → keep, allow 7 at frames-done, or reserve a slot at plan-done when a requirement is deferred?

## END dev cold runs (d94d7ac, v9, eval user, 2026-10-13 ~04:50 local; spend $0.696 = 0.200 + 0.183 + 0.158 + 0.155)
| video | hero (synthesis) | provisional | first extracted | complete / done | final tabs | cost |
|---|---|---|---|---|---|---|
| T1dQhQAm8Tc | 16.8 | 29.0 | 51.1 | **54.4** / 54.4 | stack_overview, moments, quiz, overview | $0.200 (plan $0.127 = a 1h plan WRITE — entry expired) |
| jMq8lEu-of0 (HIGH cooking) | 11.9 | 24.8 | 38.2 | **59.6** / 59.6 | cook_steps, ingredients, moments, tips, overview | $0.183 |
| uC45_4nnEAI | 15.7 | 24.1 | 38.3 | 71.4 / 71.4 | highlights, pulls, set_facts, overview | $0.158 |
| EqaMrzd9nZU | 11.3 | 27.1 | 49.6 | 69.9 / 79.4 | code, concepts, moments, quiz, overview | $0.155 |
Groups all ok, none degraded. Cost by group (ledger `summarize:extraction:{gid}`): T1dQ text1 $0.0207 (only group — moments kept on inactive learning → outline fallback; workflow_steps dropped by code evidence; code requirement not backfilled) · jMq8 text1 0.0172 / text2 0.0154 / text3 0.0078 (Σ 0.0404) · uC45 text1 0.0175 / text2 0.0094 / visual 0.0089 (3 ledger rows — 2 instant stale-connection retries) (Σ 0.0359) · Eqa text1 0.0232 / text2 0.0072 (2 rows) / visual 0.0190 (Σ 0.0494). Biggest other costs: frames (vision) 0.038–0.098, plan 0.030–0.037 when it reads the 1h entry.
Summary-doc ids (cache_hit_rate --ids): 6acd8e773cd39244d7ffff67 6acd8eae3cd39244d7ffff69 6acd8ee93cd39244d7ffff6b 6acd8f2e3cd39244d7ffff6d
Cache per call type (end runs, `scripts/cache_hit_rate.py --ids …`): extraction_text1 4 calls, 34,845 tok WRITTEN (5m; 1h = 0 — block 1 < 4,096) → hit 0 % (it is the warm-up writer by design) · extraction_text2 3 calls 100 % (97.6 % of input cached) · text3 1 call 100 % (97.3 %) · visual 2 calls 100 % (85.4 %) · plan 4 calls 75 % (1 × 1h write 16,290 + 3 reads; 56 % of input cached) · faithfulness (OpenAI auto) 64.3 % cached · memory / tier_probe / frame_vision / synthesis / description / enrichment / translation: 0 (no breakpoint or below the provider minimum).
429 / fallbacks (end runs): 0 rate-limited, 0 fallbacks; 17 sub-second "Timeout" failures (all < 1 s = the known stale pooled connection, every one recovered by the immediate resend: faithfulness 11, description_analysis 2, frame_vision 1, extraction_visual 2, extraction_text2 1).
