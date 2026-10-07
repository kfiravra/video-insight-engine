# pipeline-1min — Tasks

Last Updated: 2026-10-07
Status: 🔄 IN PROGRESS — Phase 0 (started 2026-10-07). Live copy of these docs = worktree `../vie-p1min` (branch `feat/pipeline-1min`); the copy in the main tree is stale.
Legend: [x] done + committed · [~] in progress · [ ] not started.
Rules (Kfir 2026-10-07, supersede the per-phase-branch plan): ONE worktree `../vie-p1min` on `feat/pipeline-1min` from `origin/main`; ONE PR per group — (1) phase 0, (2) 1a+1b, (3) 1c+1d, (4) phase 2, (5) phase 3, (6) phase 4; merge `origin/main` into the branch at the start of each group (merge commits, never squash). Standing permission to COMMIT on `feat/pipeline-1min` per completed task id with tests green; never push / PR / rebase / force-push / squash without Kfir's word. Commits: one per task id, message prefixed `p0.3`, `p1b.2` …; tests in the same commit; large removals own commit; prompt + registration together; the 8 settings touch points together; gate report `gates/gN.md` + tasks.md checkboxes = last commit of the group; review fixes = new commits. Testing all on dev before a PR (suites never in parallel, replay, golden with bypassCache, dev runs of the 3 benchmarks); prod only after merge. State runs + cost before each golden/prod run. No prod `.env` edit until the end — every env change goes into `env-changes.md`; every merge must run on prod with the current `.env`. Scheduled eval stays manual-dispatch until the secrets are added at the end. Opus for the whole task (subagents too). Group checks pass → gate report → STOP and ask for the push word.

## Pre-flight (before phase 0 code)
- [x] Kfir: plan reviewed 2026-10-07 (8 fixes applied — see log)
- [x] `evidence/ans-*.md` gitignored (D18); first secrets grep of the evidence folder done (see log)
- [x] Prod `LLM_EXTRACTION_MODEL` read from the box's compose config (2026-10-07): `anthropic/claude-haiku-4-5-20251001` (also `LLM_ENRICHMENT_MODEL=openai/gpt-4o-mini`, `LLM_VISION_MODEL=anthropic/claude-sonnet-4-6`, `LLM_MODEL` blank → config default)
- [x] Hebrew regression video picked from the dev DB: `EqaMrzd9nZU` (598 s, tech, Hebrew auto-captions `iw-orig`, completed v8 run 2026-08-19)
- [x] Worktree `../vie-p1min` on `feat/pipeline-1min` from `origin/main` @ bc157d1 (upstream unset so a bare push can't target main)
- [x] `.claude/hooks/.git-safety-override` absent and gitignored

## Phase 0 — measure and foundations (0/10) — 🔄 IN PROGRESS
- [~] 0.1 `pipeline.timing` on the Mongo doc (phases, LLM calls incl. cache/retry/fallback/429, downloads, first tab_ready, complete, done) + trace metadata + DONE line planned/assembled/emitted — schema test; present on a replay run
- [~] 0.2 Description LLM routed through `LLMProvider` (traced, ledgered, fakeable)
- [ ] 0.3 Replay harness: `acompletion` fake keyed by `llm_feature_var`+ordinal with recorded `latencyMs`; stubs (yt-dlp/transcript, frames+S3, Whisper, embeddings/Qdrant) with sleeps; driver over `stream_summarization`; cassettes for T1dQhQAm8Tc / jMq8lEu-of0 / uC45_4nnEAI from Langfuse + Mongo; `scripts/replay.py --video <id> [--speed 0]`; zero-network assertion
  - [ ] T1dQhQAm8Tc replay within 10 % of 240 s (phase walls)
- [ ] 0.4 Registry: `dataSources` (53), `demoteTo`, `requirementEvidence`, `quizPolicy`, `quizEnrichment` (D1), `grouping`; fix `tech.setup→tech.setup.commands`, `language vocabulary→flash_deck`; `outputWeight` from 42 v8 docs; rebuild `packages/shared/dist`; toolkit `<valid_datasources>` rendered from the registry
  - [ ] Registry tests (paths→schema fields, defaultTabs exist, components exist, evidence vocabulary, caps ints, `tsc` on @vie/shared)
- [ ] 0.5 Plan-time dataSource validation (`plan.py:181`): sibling with same component else drop + tests
- [ ] 0.6 Single-tag validator fix (`domain_types.py:1206-1215`) + regression test
- [ ] 0.7 Eval: `bypassCache` in the main loop; cron `0 3 1,15 * *`; scheduled job targets the PROD API as the eval user (`EVAL_API_URL`, `EVAL_USER_*`, Langfuse keys; no compose boot, no proxy/S3/OpenAI secrets on GitHub); 4 golden videos with per-video assertions (uC45: moment images present + no failure on 0 candidates); faithfulness + duplicate-item metrics; live set ×2 → noise file; `scripts/gate.py` (non-zero on drop > noise) + unit test; baseline as CI artifact + Langfuse dataset run
  - [ ] Gate-0 decision item: eval-user `bypassCache` permission; eval-user daily quota vs 18 × 2 runs; prod worker time at 03:00; final secrets list
- [ ] 0.8 Tier-probe A/B offline (gpt-4o-mini vs Haiku 4.5, temp 0, B.1 input, golden set) → recommendation
- [ ] 0.9 `PROMPT_SOURCE=registry|disk` dev-only switch through the 8 touch points + test
- [ ] 0.10 Prod baseline after deploy: 3 videos × 2 runs (state 6 runs ≈ $1.1 first) → phase walls, first tab, total, cost
- [ ] GATE 0 report: harness ±10 %; baseline + noise; registry tests green; A/B recommendation; deviations C1/C10/C17; env/secrets list; open questions → STOP

## Phase 1 — orchestration + prompts (0/22)
### 1a inputs and media
- [ ] 1a.1 t=0 group: metadata ∥ low-res dl ∥ 720p dl ∥ description (`validate_duration` first); shared `extract_info` if possible; metadata ≤ 6 s
- [ ] 1a.2 One 720p file per job: drop refiner + moment-fill downloads; prefetch not cancelled on zero-frame return; hi-res + moment fill seek the kept file; stream-URL pass removed
- [ ] 1a.3 Zero-candidate ladder 0.3 → 0.15 → uniform; ffmpeg rc checked + test
- [ ] 1a.4 `render_transcript(segments, every=20)` markers; prompts only; chunks keep absolute times + tests
- [ ] 1a.5 Filler removal into basic cleaning; spaCy behind `TRANSCRIPT_CLEANING_ENABLED=false`
### 1b probe, plan, memory
- [ ] 1b.1 `tier_probe.txt` + `derive_tier(probe, title)` + 3 s cap/fallback in frames; temperature plumbing (D3); explicit output-language line (D16); delete `FRAME_TIER_EARLY_CLASSIFIER`, `category_confidence`
- [ ] 1b.2 `plan.txt` rewrite (full transcript, briefs, evidence, terms; drops; caps from registry; routing on evidence; `domain_requirements` CONDITIONAL on the plan's own evidence (D15); explicit output-language line (D16); 6 examples; 45 s / 1 retry / no cache_control) + readers of dropped fields + requirements-render test + language-line test
- [ ] 1b.3 `memory.txt` (Haiku via `LLM_EXTRACTION_MODEL`, ∥ plan; output-language line D16) + fallbacks + tests
- [ ] 1b.4 `video_memory` renderer into `{video_context}` (byte-identical test)
- [ ] 1b.5 `synthesis_complete` early (superset re-emit; reducer merges; API relay persists both; typed event)
- [ ] 1b.6 `chapter_detect` gated by D6 pre-check; receives real description
- [ ] 1b.7 CHECKPOINT (visibility, not a gate): replay timing ×3 + one dev run on jMq8lEu-of0 → note here (plan start, hero time, surprises)
### 1c extraction prompt (one call)
- [ ] 1c.1 `base_extraction.txt` edits (Appendix D) + render tests
- [ ] 1c.2 `<visual_annotations>` + key_frames renderer; remove `inject_visual_context` + Phase 2.5; plan/memory/probe never see annotations; faithfulness gets the block; RAG indexes the rendered annotations as separate `source=visual` chunks with timestamps, transcript chunks + S3 blob clean (D10) + ingestion test
- [ ] 1c.3 Cache layout: system rules-only; user `[transcript + video_memory]` breakpoint (D2 provider support); static-block hash test across two videos; cache-read tokens observed in replay telemetry
- [ ] 1c.4 16 schema files stripped (SCALING, floors, `{duration_minutes}`, narrative own key, travel weight, finance confirmed); examples header; no learning fallback
- [ ] 1c.5 Remove synthesis-fed retry + count gate + `EXTRACTION_USE_FAST_FIRST` / fast-first path (`extractor.py:201-224`) through the 8 touch points
### 1d tail
- [ ] 1d.1 `enrich_quiz.txt` demand-driven quiz-only; salvage; remove flashcards/scenarios, 11 enrich files, old `enrichment` map + readers, toolkit entries, plan.txt claim
- [ ] 1d.2 `quick_quiz` host exclusions + quiz-last ordering (`quizPolicy`) + tests
- [ ] 1d.3 `synthesis.txt` trimmed (masterSummary + seo); ∥ moment fill; meta tldr/takeaways from memory
- [ ] 1d.4 Vision batching: 8/call, ≤ 5 parallel, max_tokens per batch+frame type (D12), 1 retry, index→frame mapping, no 1024 upscale, `FRAME_VISION_PARALLEL=false` = single call, `LLM_VISION_MODEL` default primary, prompt to file+registry; measure 360p-native vs 720p-after-hires on 2 videos (D7) → pick + report
- [ ] 1d.5 Heartbeats (plan, extraction); embeddings preload at worker start; `drive_pipeline` status check; retry-after + same-provider retry + tagged fallback inside `call_llm_with_retry` (D4); `EXTRACTION_PARALLEL_BATCHES` default 6; overview tab RAG-indexed (`output_chunker` reads `props.*`, overview stores `props.data.*` — A23) + chunker test
- [ ] 1d.6 Flags `EXTRACTION_PARALLEL`, `FRAME_VISION_PARALLEL` + passthrough fixes (`FRAME_VISION_ENABLED`, `FRAME_TIER_ENABLED`, `EXTRACTION_PARALLEL_BATCHES`, chunking knobs) — 8 touch points, literal defaults in compose; `FRAME_EXTRACTION_ENABLED` config default (false) vs compose anchors (true) made to agree
- [ ] 1d.7 Assembly backfill checks `requirementEvidence` against `plan.evidence` before backfilling required components (D15) + table test; food-travel-vlog assertion passes at gate 1
### Phase 1 verification
- [ ] Unit suite green (summarizer, api, web — never in parallel)
- [ ] Replay ×3: plan starts before frames-done; metadata ≤ 6 s; one 720p download; heartbeats present
- [ ] Golden full run → `gate.py` within noise; new assertions pass
- [ ] Prompts re-registered in both Langfuse projects; orphans listed
- [ ] Prod (after deploy): benchmark 3 × 2 + regression × 1 (state ≈ 8 runs ≈ $1.5 first)
- [ ] GATE 1 report (expect T1dQhQAm8Tc ≈ 130 s, HIGH vision 15–20 s, hero ≈ 25 s; cost ≤ baseline; 0 429/fallback) → STOP

## Phase 2 — progressive output (0/2)
- [ ] 2.1 Backend: per-tab `tab_ready` as final; provisional Moments from memory outline (fallbacks: chapters / description timestamps); final re-emit `final: true` + positions; `final`/`provisional` typed in `sse_events.py` + `packages/types`; Redis stream reset per run + TTL at start; leaked streams cleaned; SSE replay test with the real broker
- [ ] 2.2 Frontend: `meta` never wipes tabs; `position` sort key; `initialTab` sticky; replace-by-id honours position+final; DB-vs-stream by final/version; timeline/pills/Cancel stay; provisional state; phases in `VALID_SSE_PHASES` + `SSE_PHASE_MAP`; reducer tests per rule; Playwright smoke (first tab before `complete`, no flicker)
- [ ] GATE 2: first visible tab ≤ 35 s on benchmark; no replay duplicates; golden unchanged → STOP

## Phase 3 — the split, v9 (0/9)
- [ ] 3.1 Field-aware renderer + round-trip tests ×16
- [ ] 3.2 Reconcile at plan-done (Appendix C): memory second opinion + doubly-false demotion (conditional requirements already live from 1b.2/1d.7) + `pipeline.reconcile` + table-driven tests
- [ ] 3.3 Grouping from the registry (partition, domain, split by budget, caps, group-1 rule) + tests
- [ ] 3.4 Firing: staggered text groups at plan-done, visual at frames-done, semaphore, `EXTRACTION_PARALLEL=false` single call; per-group tails; stagger + cache-hash tests
- [ ] 3.5 Per-group validation without defaults; key filtering; merge (scalar policy D12-C12, chunk-major, deterministic) + tests
- [ ] 3.6 Per-group assembly + `tab_ready{final:true}`; attachments at final re-emit; overview/meta last; cross-tab links at end; quiz after owning group; synthesis ∥ moment fill ∥ faithfulness after last group + tests
- [ ] 3.7 Long videos: outline-merged chunks ≤ 50k tokens / 40 min; chunks × groups; annotations sliced per chunk; chapter_detect only without outline + synthetic 90-min test
- [ ] 3.8 Failure paths (group fail → demote/drop + `droppedTabs`; all fail → error; plan fail → fallback set) + tests
- [ ] 3.9 `PIPELINE_VERSION` → v9 (force-recreate summarizer/worker/api); prompts re-registered; demo library re-run script (runs on Kfir's go)
- [ ] Verification: replay ×3 with synthesized group timings; golden full run; prod benchmark 3 × 2 + regression; cost per video by group from the ledger
- [ ] GATE 3 (HIGH cooking ≈ 55–60 s, text-only ≤ 55 s, hero ≤ 30 s, cost below baseline, 429 = 0, duplicates ≤ baseline) → STOP

## Phase 4 — sweep, docs, report (0/3)
- [ ] 4.1 Sweep leftovers (list in plan 4.1) + `.env.example` / `.env.production.example` + `docs/INFRASTRUCTURE.md` flags table
- [ ] 4.2 Docs: summarizer docs stale lines; `docs/summarizer-workflow.md` v9 flow; README numbers; eval "twice a month" after first scheduled run
- [ ] 4.3 Report: before/after per phase; what each phase bought; levers not taken with projected gain
- [ ] Move task to `dev/completed/` with ARCHIVED note

## Open questions (carry into gate reports)
- Golden ids for the food-travel vlog and the story-intro recipe (propose at gate 0).
- Does the installed Langfuse SDK's trace object expose spans (D8)?
- Will LiteLLM honour a user-content cache breakpoint for Anthropic (D2)? Verified in 1c.3.
- Eval-user quota / `bypassCache` permission for the prod-API eval (gate-0 decision item, D13).

## Progress Log
- 2026-10-07 — Task created from Kfir's brief. Three mapping agents produced `evidence/A-DIGEST.md`, `evidence/CODE-MAP-orchestration.md`, `evidence/CODE-MAP-eval-web-registry.md`; the 12 `ans-*.md` answer files copied from the (volatile) session scratchpad into `evidence/`. Benchmark cooking video fixed: `jMq8lEu-of0`. 18 brief-vs-code conflicts recorded in the plan (C1–C18) with resolutions; 14 decisions in context (D1–D14). No code touched. Note: `dev/active/` is tracked by git.
- 2026-10-07 (later) — Kfir's plan review applied, docs only: (1) conditional domain requirements moved to phase 1 (1b.2 prompt + new 1d.7 backfill check; C19/D15); (2) C11/D10: visual annotations indexed as `source=visual` Qdrant chunks, not dropped from RAG; (3) C18/D13: scheduled eval targets the prod API as the eval user, gate-0 decision item + secrets list; (4) output-language line in probe/plan/memory (C20/D16); (5) uC45 assertion reworded (C21/D17); (6) overview-tab RAG indexing fix into 1d.5; (7) `FRAME_EXTRACTION_ENABLED` agreement into 1d.6; (8) 1b.7 visibility checkpoint (D19). Four CC choices confirmed. `.gitignore` gained `dev/active/pipeline-1min/evidence/ans-*.md` (D18). Secrets grep of `evidence/` + task docs (keys, tokens, emails, credentialed URLs, Mongo URIs, IPs, SSH targets, proxy hosts): no key/token values, no emails, no credentialed URLs anywhere; env-var NAMES only in tracked files. The prod EC2 IP + `~/.ssh/vie-demo-key.pem` path appear in `evidence/ans-vision-worker-box.md:155,192` — that file is gitignored, so nothing tracked carries them (tracked `A-DIGEST.md` / `CODE-MAP-*.md` are clean). Worktree NOT created (waiting for the word).
- 2026-10-07 (execution start) — Kfir: "Go", single worktree/branch + 6 PR groups, standing commit permission. `origin/main` had moved to bc157d1 (timedtext-429 merge: `youtube.py`, `transcript*.py`, `download_utils.py`, `config.py` changed — re-verify those anchors). Worktree `../vie-p1min` created; task docs copied from the main tree; `.gitignore` evidence line re-applied on the branch. Prod model + Hebrew id recorded. Dev stack containers bind-mount the MAIN tree → dev runs of branch code need summarizer + worker (+ api/web when touched) recreated from the worktree (`docker compose -p video-insight-engine` from `../vie-p1min`, with `.env` available there).
