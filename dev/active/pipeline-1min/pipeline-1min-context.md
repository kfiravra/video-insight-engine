# pipeline-1min — Context

Last Updated: 2026-10-08 — 🔄 IN PROGRESS (phase 1, one uninterrupted run 1a+1b → g1a → 1c+1d → g1 → review). Live docs in the main tree (branch `feat/pipeline-1min`). Phase 0 merged (PR #22).

## RESUME HERE (phase 1 run state)
- Rules for this run: top of `pipeline-1min-tasks.md` (local commits per task id without asking; never push; never stage CLAUDE.md / .claude/**; main tree only, no worktrees; stop only when g1a + g1 are written, review-fix commit last).
- Implementation is done by parallel Opus subagents following `agent-rules.md`; the coordinator commits (stage by path; a file shared by two in-flight tasks is staged hunk-wise via `git hash-object -w` + `git update-index --cacheinfo`; TS-only commits with partial files use `--no-verify` so lint-staged doesn't hide other agents' in-flight edits).
- Progress = tasks.md checkboxes + log. If the agents were lost (new session), re-launch the `[~]` tasks from tasks.md with the same file ownership; check `git status` for their uncommitted work first and keep it.
- Pending hand-offs for the wiring agent: `ctx.prompt_segments` (sponsor-filtered) used by extraction/plan/memory renders; `cached_response.resolve_synthesis` prefer `meta.keyTakeaways`; two `synthesis_complete` emissions; `chapter_detect` outline param from 1b.6; probe await before Step 6b; memory ∥ plan; delete classifier + `FRAME_TIER_EARLY_CLASSIFIER` + `category_confidence`. For 1c: remove the `inject_visual_context` call inside `build_prompt_transcript` (phases/extraction.py) with Phase 2.5; base_extraction.txt says markers are absolute video time.

## Why this task exists
Prod takes 171–240 s per 20-minute captioned video and shows nothing until ~226 s. Kfir's brief
(`pipeline-1min-brief.md`, 2026-10-07) sets: first visible content ≤ 30 s, every tab ≤ 60–65 s, equal
or better quality, equal or lower cost. Grounded in 27 research answers (A1–A27, 2026-10-06/07) and
two perf reports (session-scoped; their substance is preserved in `evidence/`).

## Where the facts live (read these before coding a phase)
| File | What it holds |
|---|---|
| `pipeline-1min-brief.md` | Kfir's brief verbatim — the spec. Where it and the code disagree → gate report, not guessing. |
| `evidence/A-DIGEST.md` | A1–A27 condensed: index table (A → claim → file:line → brief item), per-answer summaries, conflicts, "numbers to reuse". |
| `evidence/ans-*.md` | The 27 answers verbatim (378 KB). Mapping: A1–A2 consumer map; A3/A4 vision-worker-box; B5–B12 → A5–A12; C13–C16 → A13–A16; Q17–Q27 → A17–A27. |
| `evidence/CODE-MAP-orchestration.md` | Current call order with file:line; brief item → code anchor table (1a/1b/1c/1d/2/3); settings × touch-point matrix; prompt loading; tests; Mongo `pipeline.*` shape. |
| `evidence/CODE-MAP-eval-web-registry.md` | run_eval/eval.yml/golden set; domains.json keys + consumers + dist staleness; toolkit datasources; frontend reducer defects with lines; API relay + Redis stream; register_prompts; demo tooling; CI/deploy. |

Line numbers were read on `docs/readme-rebuild` @ 015cf26 (code dirs identical to `origin/main` d4d3fde). Re-verify after each merge.

## Benchmark and regression sets (fixed for the whole task)
| Role | Video | Why |
|---|---|---|
| Benchmark 1 (tech, STANDARD) | `T1dQhQAm8Tc` | the 240 s prod run; cassette source |
| Benchmark 2 (food, HIGH) | `jMq8lEu-of0` (Chris Makes BA's Best Lasagna, 1,001 s, 40 frames, vision 58.8 s / $0.149 local) | captioned, ~17 min, cleanest of the 5 HIGH cooking runs; must be run on prod in phase 0 for its baseline |
| Benchmark 3 (static camera) | `uC45_4nnEAI` | 171 s; 0-candidate scene path; HIGH via title keyword `opening` |
| Regression: no captions | `L51eiJEM45U` (Kyoto pizzeria, prod HIGH + Whisper, 1,357 s) — or Kfir's pick | already a prod run |
| Regression: non-English | `EqaMrzd9nZU` (מי הם LangChain ו LangGraph, 598 s, tech, Hebrew auto-captions `iw-orig`; completed v8 dev run 2026-08-19) — picked from the dev DB 2026-10-07 | verifies the output-language line (D16) |
| Golden additions (0.7) | food-travel vlog (no recipe comps), recipe with long story intro, `uC45_4nnEAI`, `Jru5B044HOs` (do-along, no quiz) | first two need ids — pick from the demo library / propose at gate 0 |

Prod measurement = median of 2 runs from `pipeline.timing` (phase 0 adds it). State run count + cost before every prod/eval run (≈ $0.19 standard / ≈ $0.33 HIGH per run).

## Key files (anchors; full tables in the CODE-MAPs)
| Area | File |
|---|---|
| Orchestrator | `services/summarizer/src/routes/pipeline_runner.py` (`stream_summarization`; Phase 2.5 inject at `:173`; plan waits `:218→246`; DONE log) |
| Phases | `services/summarizer/src/services/pipeline/phases/{metadata,transcript,frames,triage,extraction,enrichment,synthesis,assembly,translation}.py`; `pipeline_helpers.py` (`run_parallel_phases` + heartbeat) |
| Plan / classifier | `services/pipeline/plan.py` (`:181` dataSource copy, `:278-292` call, `_build_fallback_plan`), `classifier.py`, `triage.py` |
| Extraction | `services/pipeline/extractor.py` (fast-first `:201-224`, synthesis-fed retry), `extraction_merger.py`, `extraction_quality.py`, `prompt_builder.py` |
| Enrichment / synthesis | `services/pipeline/enrichment.py` (`ENRICHMENT_MAP` import-time snapshot), `synthesis.py` |
| Visual injection (to remove) | `services/pipeline/scene_frames.py:244` (`startMs` bug) |
| Vision | `services/media/frame_analyzer.py`, `phases/frames.py:78-118` (`_make_reselect_hook`, HIGH pass inside Step 6b), `scene_extractor.py:525,574,645-665,734` |
| Downloads | `media/hires_prefetch.py`, `hires_refiner.py:141`, `assembly/moment_frame_fill.py:137`, `stream_url.py`, `video/youtube.py` (`_detect_category :174-284`, `category_confidence :157`) |
| Tier | `media/visual_tier.py:25-49` (`derive_tier(category, title, tags)`), `domains.json:125-134` tier table |
| LLM plumbing | `services/llm.py`, `llm_provider.py` (`acompletion` symbol `:13`; cache_control system-only `:166-174`; fallbacks `:308-309`; model logging `:331`), `utils/llm_retry.py`, `llm_telemetry.py`; `video/description_analyzer.py:16,151` (own `acompletion` import) |
| Transcript | `services/transcript/cleaner.py`, `transcription/transcript_chunker.py`, `utils/transcript_slicer.py`, `pipeline_helpers.py:145-157` (segment shape `start`/`duration`) |
| Validation | `models/domain_types.py:1206-1215` (single-tag bug; `validate_domain_output` materializes defaults `:1211`), `models/pipeline_types.py:257-281` (`PlanResult`), `models/sse_events.py:99-106` |
| Mongo write | `phases/assembly.py:158-169` (`pipeline` `$set` — add `timing`, `reconcile` here); `ctx.phase_times` logged, never persisted |
| Config | `services/summarizer/src/config.py` (112 settings); worker `src/worker/{runner,pipeline,payload}.py` |
| Registry | `packages/shared/src/config/domains.json` (15 keys; existing `enrichment` map!), `services/summarizer/src/shared_config/domain_config.py`, `packages/shared/src/index.ts:102`; `prompts/component_toolkit.txt:246-265` (53 hardcoded datasources) |
| Prompts | `services/summarizer/src/prompts/` (`base_extraction.txt`, `plan.txt`, `classify.txt`, `chapter_detect.txt`, `synthesis.txt`, `enrich/*` 11 files, `schemas/*` 16, `examples/*` 8); registry-first loader `load_prompt_text` (no disk switch); `scripts/register_prompts.py --commit`; `vie-langfuse-init` re-syncs on compose up |
| Eval | `scripts/run_eval.py` (`score_entry :122-194`; SSE consume `:293-313`; no bypassCache `:481`; stability `:538`), `dev/golden-dataset/videos.yaml` (24, 14 live), `.github/workflows/eval.yml` (weekly) |
| SSE / API | `services/summarizer/src/services/cache/pipeline_event_stream.py:197-213` (TTL only in `mark_done`), `routes/pipeline_broker.py`, `api/src/routes/stream.routes.ts:156-166`, `packages/types/src/api.ts:116-128` |
| Frontend | `apps/web/src/features/video-output/.../stream-event-processor.ts` (`:172-180,225,273-279`), `OutputRouter.tsx:165,185-243`, `TabCoordinationContext.tsx:62-68`, `resolve-display-tabs.ts:23-26`, `sse-validators.ts:59,229` (`SSE_PHASE_MAP`, `VALID_SSE_PHASES`) |
| CI / deploy | `.github/workflows/ci.yml` (summarizer pytest, shared tsc), `deploy.yml` (`workflow_run` on CI success for `main` → merge = deploy) |
| Version | `packages/shared/.../pipeline-version.json` (single value; bind-mounted → force-recreate summarizer/worker/api after bump) |

## Decisions (made while planning; each reported at its gate)
- **D1 registry key**: brief's `enrichment {quizDomains, flavor}` becomes `quizEnrichment` (existing `enrichment` map collides); old map deleted in 1d with the enrich files.
- **D2 cache breakpoint**: provider gains a user-content block breakpoint (content as list of blocks) in 1c; verified by cache-read tokens in telemetry.
- **D3 temperature**: new kwarg plumbed through `LLMService`/`LLMProvider`/`call_llm_with_retry`/`_wrap_with_override` in 1b.
- **D4 fallback**: LiteLLM `fallbacks` removed; same-provider retry + cross-provider fallback inside `call_llm_with_retry`; fallback model tagged (1d.5).
- **D5 memory model**: reads `LLM_EXTRACTION_MODEL` exactly as the brief says (no new setting); Kfir confirms prod pins Haiku.
- **D6 chapter_detect gate**: duration > 40 min OR token estimate > `MAX_TOKENS_PER_BATCH`, AND no memory outline.
- **D7 HIGH vision order**: today vision runs on 1024-upscaled detection frames before hires; 1d.4 measures 360p-native vs 720p-after-hires and the reorder follows the winner.
- **D8 Langfuse spans**: client has no `.span()` → `phase_times` persisted in `pipeline.timing` + trace metadata; spans only if the installed SDK's trace object exposes them.
- **D9 prompt source switch**: `PROMPT_SOURCE=registry|disk`, dev-only, default `registry` (allowed by the brief). Counted outside the "two flags" rule because the brief itself asks for it.
- **D10 faithfulness + RAG input** (revised by Kfir 2026-10-07): faithfulness gets transcript + rendered `<visual_annotations>`; RAG keeps visual facts by indexing the rendered annotations as separate chunks (`source=visual`, with timestamps) in the same Qdrant collection; transcript chunks and the S3 blob stay clean. Lands in 1c.2.
- **D11 EXTRACTION_PARALLEL_BATCHES=6**: ships with the 429 counter as the guard; back off to 4 if any 429 at gate 1.
- **D12 vision max_tokens**: scaled per frame type (screen recordings ~190 tok/frame, food ~85), not only per count.
- **D13 eval cron + target** (revised by Kfir): `0 3 1,15 * *`; the scheduled job targets the prod API as the eval user (`EVAL_API_URL`, `EVAL_USER_EMAIL/PASSWORD`, Langfuse keys) — no compose boot on the runner, no proxy/S3/OpenAI secrets on GitHub. Gate-0 decision item: `bypassCache` allowed for the eval user; eval-user daily quota vs 18 videos × 2 (noise run); prod worker time at 03:00.
- **D14 branches** (revised by Kfir 2026-10-07, twice): `feat/pipeline-1min` checked out in the MAIN tree — no worktrees (Kfir wants every change visible live in the IDE; parallel subagents edit the same tree on disjoint files and never commit — the coordinator commits); ONE PR per group — (1) phase 0, (2) 1a+1b, (3) 1c+1d, (4) phase 2, (5) phase 3, (6) phase 4; merge `origin/main` in at each group start (merge commits only). Standing commit permission per task id with tests green; push/PR on Kfir's word only.
- **D20 env changes** (Kfir): no prod `.env` edit until the end; every needed change collected in `env-changes.md`; each merge must run on prod with the current `.env` (safe defaults in config + compose). Scheduled eval = manual dispatch only until the GitHub secrets land at the end.
- **D15 conditional domain requirements live in phase 1** (Kfir): 1b.2 renders `domain_requirements` conditional on the plan's own evidence; 1d.7 makes the assembly backfill check `requirementEvidence` against `plan.evidence`. Otherwise the food-travel-vlog assertion cannot pass at gate 1. Phase 3 reconcile adds only the memory second opinion + demotion.
- **D16 output-language line** (Kfir): removing `<outbound_links_instructions>` removes plan.txt's only explicit language instruction → probe, plan and memory prompts get an explicit line (generation language = English; labels/goals per today's rule); the non-English regression run verifies it.
- **D17 uC45 golden assertion** (Kfir): after the 1a.3 ladder uC45 has scene frames → assertion = "moment images present and no failure on 0 candidates".
- **D18 evidence tracking** (Kfir): `evidence/ans-*.md` gitignored (line added to `.gitignore` 2026-10-07); `A-DIGEST.md` + the two `CODE-MAP-*.md` stay tracked; before any commit, grep the evidence folder for keys, tokens, emails and credentialed URLs and report (first pass done 2026-10-07, see tasks.md log).
- **D19 1b checkpoint** (Kfir): after 1b, replay timing + one dev run on jMq8lEu-of0 as a visibility check, not a gate.
- **D21 tier-probe model** (Kfir, gate 0): Haiku 4.5 (`anthropic/claude-haiku-4-5-20251001`). 1b.1 forces bare JSON AND the parser strips ``` fences as a fallback (A/B: Haiku ignores `json_object`, wraps in fences, appends reasoning until max_tokens).
- **D22 faithfulness informational** (Kfir, gate 0): faithfulness is reported in every gate report but never gates (`gate.py` `INFORMATIONAL_METRICS`: no regression verdict, no lost-metric exit 2). Gating metrics = quality + duplicate rate + per-video assertions. A missing score = "not scored", never a regression.
- **D23 known-failing golden check** (Kfir, gate 0): `food-recipe-story-intro` step_player = xfail "plan sees 3,000 chars; fixed by 1b.2"; the video is in the PR-2 quick pass, where the check MUST pass (remove the xfail in 1b.2).
- **D24 golden set** (Kfir, gate 0): v8KaQr0MhjE + wCkLNqy5OHE approved; `review-airpods-pro` replaced by a review video of similar length; domains labelled by content (photosynthesis, double-slit = science; the Montreal vlog = its content domain, format vlog); expectedTabs fixed where they disagree.
- **D25 eval-user versions** (Kfir, gate 0): results produced by the eval user are saved as versions flagged `eval=true`, never become the served version for other users, and never prune non-eval versions → new task 1d.8. The scheduled eval stays manual-dispatch until 1d.8 ships (and the secrets land). 0.10 on the benchmark videos unchanged.
- **Confirmed by Kfir 2026-10-07**: jMq8lEu-of0, `quizEnrichment`, `PROMPT_SOURCE`, memory on `LLM_EXTRACTION_MODEL`. Prod `LLM_EXTRACTION_MODEL` = `anthropic/claude-haiku-4-5-20251001` (read from `docker compose config` on the box 2026-10-07; same box: `LLM_ENRICHMENT_MODEL=openai/gpt-4o-mini`, `LLM_VISION_MODEL=anthropic/claude-sonnet-4-6`, `LLM_MODEL`/`LLM_FAST_MODEL` blank → config defaults).

## Settings: the 8 touch points (modelled on `YOUTUBE_PROXY_URL`)
1. `services/summarizer/src/config.py` (+ `src/__tests__/config.test.py`)
2. `docker-compose.yml` `x-summarizer-env` anchor (shared by summarizer + worker)
3. `docker-compose.prod.yml` anchor
4. `.env.example`
5. `.env.production.example`
6. `docs/INFRASTRUCTURE.md` tuning table
7. `docs/SERVICE-SUMMARIZER.md` env block
8. the code reader + its tests
`docker-compose.override.yml` carries no env. Bool passthroughs need literal defaults (`${X:-true}`) or the container crash-loops on blank.
New settings allowed: `EXTRACTION_PARALLEL`, `FRAME_VISION_PARALLEL` (bool, default true) + `PROMPT_SOURCE` (D9). Dead settings deleted, not left: `FRAME_TIER_EARLY_CLASSIFIER`, `EXTRACTION_USE_FAST_FIRST`, `LLM_NUM_RETRIES` (dead), old enrich caps.

## Numbers to reuse (from A-DIGEST "Numbers to reuse")
- 240 s run: frames 85.7 (vision 35.8) → classifier 2.2 → plan 27.9 → chapter_detect 3.4 → extraction 76 / 8,606 out → synth 4.6 ∥ enrich 12.0 → first tab ≈ 226; $0.186; proxy 143 MB.
- Rates: extraction 101–112 tok/s (Haiku); vision wall ≈ 6.6 + 16.2 s per 1k out tokens; image tokens 792/frame upscaled, 314 native 360p, ~1,000 real 720p.
- HIGH all (n=14): median 63.6 s / $0.149; STANDARD (n=16): median 25.6 s / $0.028.
- Prompt cache: Haiku floor 4,096 tokens/block → videos < ~8 min (3/21) never cache; with a 250–400-token memory block the threshold rises to ~10 min.
- 42 stored v8 docs: 32.6 % extraction unused-by-plan, 27 % unused-by-final-tabs.
- Rate limits: zero 429s ever recorded; Start tier > 12× headroom.

## Gotchas (inherited + new)
- `git stash` BANNED; no commit/push/PR without an explicit verb in the current turn; `.claude/hooks/.git-safety-override` is gitignored (line 78) and currently absent — delete it if it reappears before any commit.
- The git-safety hook blocks Bash heredocs whose prose contains "git" + "clean" → write docs with the Write tool.
- `dev/active/` is tracked: task docs + `A-DIGEST.md` + the two `CODE-MAP-*.md` get committed; `evidence/ans-*.md` is gitignored (D18). Secrets grep of the evidence folder before every commit.
- Langfuse registry label `production` beats local `.txt` in both envs until `PROMPT_SOURCE=disk` (0.9) lands; `register_prompts.py --commit` leaves orphans for deleted prompt files → delete in the UI (list them in the gate).
- `pipeline-version.json` is bind-mounted as a single file → editing it needs force-recreate of summarizer + worker + api.
- NEVER run the 3 test suites in parallel (memory-server contention). Summarizer tests: `cd services/summarizer && .venv/bin/python -m pytest` (ruff only in the summarizer venv, absolute path). Formatter hook strips not-yet-used imports mid-edit → add import and use in one edit.
- Worker/admin containers have no `--reload` → `docker restart` after Python edits; vie-web container serves a stale build → test UI via local vite (`VITE_API_URL=/api` on :5174).
- SSE stream DRIVES the pipeline (POST only registers) → the replay driver and any headless client must consume the stream.
- `run_eval.py` needs `EVAL_USER_EMAIL/PASSWORD` exported (`set -a; eval "$(grep -E '^EVAL_USER_' .env)"; set +a`).
- Vision today = exactly ONE call per run in every tier (`frames.py:36-39` skips the second pass once `frame_descriptions` is set).
- `FRAME_EXTRACTION_ENABLED` defaults `false` in config but `true` in both compose anchors.
- Shared tree, parallel sessions: `git branch --show-current` before any branch op.
- Dev `vie-api` runs `node dist/index.js` baked into its image (src is mounted but unused) → API code changes need `docker compose build vie-api` + force-recreate. On this WSL box the build fails with "error getting credentials" → build with `DOCKER_CONFIG=<dir with {} config.json + cli-plugins symlink>`.
- After a WSL hiccup the host port proxies (27017/6379/3000) can accept then reset connections → `docker compose up -d --force-recreate --no-build vie-mongodb vie-redis vie-api …` fixes it (api route tests fail with ECONNRESET / "Plugin did not start in time" otherwise).

## Skills
summarizer / llm-common → `backend-python`; api → `backend-node`; web → `react-vite`. Read SKILL.md + the listed resources when the hook fires, before code.

## Kfir's side (Appendix F, kept current)
- `.env.production`: `EXTRACTION_PARALLEL_BATCHES=6`; `LLM_EXTRACTION_MODEL` verified by Kfir (value to fill in D-list); the two new booleans default on (no entry needed).
- GitHub secrets for the eval (prod-API mode, D13): `EVAL_API_URL`, `EVAL_USER_EMAIL`, `EVAL_USER_PASSWORD`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (existing: `ANTHROPIC_API_KEY`, Langfuse pair, `EVAL_USER_PASSWORD`). No proxy/S3/OpenAI secrets on GitHub. Eval user: `bypassCache` allowed + daily quota ≥ 36 runs for the noise run (gate-0 decision item).
- Budget alert; approvals at each gate; tier-probe model decision after the A/B (gate 0); fill the Hebrew regression video id; the two golden ids (food-travel vlog, story-intro recipe); demo library re-run go (phase 3); the word to create the phase-0 worktree.
