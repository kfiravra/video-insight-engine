# g4 working notes (coordinator) — routed items + questions to carry into g4.md

## Routed (who applies what)
- 4.1 → 4.0d commit (config/compose are 4.0's): delete `LLM_MAX_TOKENS`, `LLM_FAST_MAX_TOKENS`, `SCENE_MAX_FRAMES`
  (`src/config.py`; `docker-compose.yml` ~21–22 + `docker-compose.prod.yml` ~41–42 for the two LLM keys);
  `tests/test_config.py` `_DELETED` += the three names; `tests/test_gemini_transcriber.py:660-664` reads
  `settings.LLM_MAX_TOKENS` (delete the test or compare a literal) + `gemini_transcriber.py:50` comment.
  Optional: `LLM_CHAPTER_DETECT_MODEL: ${LLM_CHAPTER_DETECT_MODEL:-}` passthrough in both anchors (can't reach
  containers today) — tie to the chapter_detect model question. If declined, INFRASTRUCTURE.md needs 2 rows back.
- 4.1 → 4.2 (docs): `SERVICE-SUMMARIZER.md:187` (`LLM_FAST_MAX_TOKENS`), `DATA-MODELS.md:38-55,138` (old enrich
  table, `categoryConfidence`), old mentions in `summarizer-workflow.md`, `PROJECT-BRIEFING.md:156,160,194,419`
  (no owner in the map → 4.2).
- 4.2 → after 4.0b lands: ERROR-HANDLING §LLM error handling, SERVICE-SUMMARIZER retry block, DATA-MODELS
  `pipeline.timing` fields (refusal fallback, model guards) → 4.2 part 2.
- 4.1 part 2 / 4.2 part 2 wait for the 4.0 model picks (env examples' model block, INFRASTRUCTURE model rows; docs
  model names; README numbers from g3 §Prod + gate 4.0).
- Skipped (minor): `routes/stream.py` docstring "Quiz/flashcards/cheat sheet", "(1-3 calls)" — editing it makes the
  formatter reflow the file.

## Questions for g4.md (beyond those already in g4 from brief-4.3)
- 4.1 Q1 file splits deferred from g1 still over 500 lines (youtube.py 921, transcript_chunker.py 862, extractor.py 727,
  scene_extractor.py 595, assembly/core.py 1932, output_chunker.py 685, video.service.ts 887, video.repository.ts 667)
  — not in the 4.1 row; one move per commit if wanted.
- 4.1 Q2 `plan.txt` still asks for `identity`, `corePromise`, `uniqueAngle`, `extractionGuidance`; nothing reads them —
  ≈ 90 output tokens ≈ 1.9 s on the plan call (critical path); dropping = prompt change (new 1h write, DEV Langfuse,
  golden). Drop or keep as reasoning scaffold?
- 4.1 Q3 older dead code: `assemble_quiz/scenario/gallery/verdict` (no dispatcher since 2026-07-12), v1 `VIEResponse`
  class (test-only), TS legacy VIEResponse flashcards/scenarios (still read for stored v1 docs), api `updateEnrichment`
  (no caller) — sweep?
- 4.1 Q4 rename `LLM_CLASSIFIER_MODEL` (g1 review asked "in 4.1") — config/compose + env-changes entry → with 4.0's
  tier-probe change, or drop the rename?
- 4.1 Q5 `tests/fixtures/llm_responses/*.json` use the v8 plan shape → refresh to v9?
- 4.1 Q6 web `state.enrichment` (quiz) set, never read → remove?
- 4.2 Q1 llm-cost-model + docs cite `dev/active/pipeline-1min/gates/g3.md` (moves on archive) → keep paths or inline?
- 4.2 stale claims left: ERROR-HANDLING "> 3h → VIDEO_TOO_LONG" vs `MAX_VIDEO_DURATION_MINUTES` 600 (API limit not
  checked); API-REFERENCE `metadata`/`meta` examples don't match the emitted shape; CROSS-CUTTING §VIEResponse v1/v2
  fallback order; ARCHITECTURE "29 renderers", moment-fill "60s + 150s" unchecked.
- 4.3: g3 Q-download-silent + Q-timeout-prod (prod), carried.
- R4.2: `phases/assembly.py` rawTranscriptRef write-back filters `{"_id": ctx.video_summary_id}` with a str while the
  repository writes `ObjectId(...)` (`mongodb_repository.py:54`) → the update matches nothing (real bug, untracked) —
  track or fix?
- R4.2 → 4.2 part 2: TIMING_SCHEMA_VERSION 3 → 4 (4.0b: Refusal llmFailures rows with tokens/cost, refusedModel /
  refusalCategory, counts.refusals, costUsd counts failures) — summarizer-workflow.md:270, OBSERVABILITY.md:76,
  DATA-MODELS.md:226.
- 4.0b: venv loads LiteLLM's REMOTE model map at import (≈ 3.7 s, revision 3d3846c2…; it HAS claude-haiku-5-5 at the
  docs prices) — pin `LITELLM_LOCAL_MODEL_COST_MAP=True` for deterministic pricing, or keep remote? The remote map
  prices Sonnet 5.5 cache READ at $0.10/MTok vs the docs + bundled map $0.20.
- 4.0b: OpenAI STREAMED refusals are undetectable through LiteLLM 1.105 (refusal deltas dropped → empty answer); no
  pipeline OpenAI call streams today. Anthropic refusals → `finish_reason="content_filter"`, `stop_details` dropped
  on the chat path → `refusalCategory` null.
- 4.0b: after a refusal the split path's lost-answer re-ask (`extraction_groups.py`) re-sends the refused call once →
  surface the refusal as an error instead of None so callers skip the re-ask?
- 4.0b: refusal on the previous model's ONE re-send that ERRORS → LLMRefusalError (no transient retry) — rare × rare.
- R4.1: `.env.example:178` shows `# LLM_CHAPTER_DETECT_MODEL=…` as a working override but compose doesn't pass it
  (ties to D29's chapter_detect question) — part 2: mark "not passed through" or add the passthrough in 4.0d.
- R4.1: the 11 retired `summarizer:enrich:enrich_*` prompts are still registered in dev + prod Langfuse (orphans) —
  archive them?
- R4.1: `scene_extractor.py:337` unused `max_frames` parameter (SCENE_MAX_FRAMES dead) — 4.1 removes it if trivial.
- 4.2: web `stream-error-messages.ts` says "Maximum duration is 4 hours" / "Minimum … 30 seconds" vs the summarizer's
  `MAX_VIDEO_DURATION_MINUTES` (default 600; both `.env` templates set 30) and 60 s — fix the web text?
- 4.2 part 1 COMMITTED 92c49d3 (11 files incl. PROJECT-BRIEFING + TLDR). Part 2 waits: model names, README numbers,
  ARCHITECTURE / briefing v8 cost lines, TIMING_SCHEMA_VERSION 4, 4.0b refusal/guard docs.
- R4b-B: daily cost cap counts refused attempts (cost-monitor sums every priced llm_usage row; refusal rows are
  success:true + priced, the fallback row too) — accept users paying for a content-triggered refusal?
- R4b-B: admin can't see refusals (`services/admin/src/routes/usage.py` `_build_run_call` :621, summary
  `success_count` :146; `alert_evaluator.py:218` counts only success==False) — surface refusals in admin?
- R4b-B: the Haiku 5.5 map entry is registered only in the summarizer process; the assistant's ledger would price a
  Haiku-5.5 chat at $0 → move the registration into llm-common?
- R4b-B: `llm_usage.fallback_from` tags refusal fallbacks only (D4 rows untagged) while timing `fallbackUsed` covers
  both — one meaning?
- → 4.2 part 2: pipeline.timing v4 field list (llmFailures Refusal rows with tokens/cost/cache, refusedModel,
  refusalCategory, counts.refusals (+ the split counts 4.0b adds), costUsd includes refused attempts; llm_usage
  `refusal`, `refusal_category`, `fallback_from` (refusal fallbacks only)).
- R4b-A: a transient error (429/529/sub-second drop) on the previous model during the refusal re-send ends the call
  (LLMRefusalError → None, no retry) — accept, or allow one immediate resend on the previous model (never the refused)?
- R4b-A: OpenAI streamed refusal → empty answer → retried on the same model then D4 hop (only Claude streams today).
- 4.0b COMMITTED f79f0c1 (R4b 7 W folded; summarizer 6,452 / 3 skipped; llm-common 126; capture 12/12). FINAL
  pipeline.timing v4 (→ 4.2 part 2): `version: 4`; llmCalls + `refusedModel`, `refusalCategory` only on the previous
  model's answer to a refused call; llmFailures + `refusedModel` on any row recorded during the re-send (refusal /
  error / timeout); `error: "Refusal"` rows add `refusalCategory`, `inputTokens`, `outputTokens`, `cacheReadTokens`,
  `cacheWriteTokens`, `cacheWrite5mTokens`, `cacheWrite1hTokens`, `costUsd`; counts `refusals` (refusal rows w/o
  refusedModel), `refusalFallbacks` (answers with refusedModel), `refusalsUnanswered` = refusals − refusalFallbacks,
  `fallbacks` = cross-provider (D4) + silent model swaps only; `costUsd` includes refused calls. llm_usage: `refusal`
  bool, `refusal_category` (always null on LiteLLM 1.105 — stop_details dropped), `fallback_from` = refused model id
  on the re-send's row. New call option `propagate_refusal`; `call_deadline` converts a timeout during the re-send.
- A/B analysis (4.0d step 1, offline): extraction H5.5 low stops "when done" (A pads every list to its cap) but LOSES
  real recipe steps on jMq8 (food.steps 20 → 10: folds the end, drops the preheat steps) and some uC45 cards; half the
  item gap = B's own run-to-run noise; concepts more specific; 0 defaulted. → golden decides; extraction-medium arm
  measured as DATA (spec: medium only for plan) → g4 question if it recovers the steps.
- A/B: vision H5.5 mislabels 3 jMq8 cooking frames `talking_head` (presenter-filler drop risk) → Sonnet 5.5 (rule).
  All arms read all 5 uC45 card names at 720p; today's stored 360p descriptions misread 3/5 (size, not model).
- A/B: plan Sonnet 5.5 — the "required tab missing" was a SCORER bug (step_flow_canvas is an accepted equivalent);
  its higher cost = one 1h write (A's cache was warm from dev runs); warm 5.5 ≈ $0.030/call vs $0.036, 2.5× faster;
  1h block +35 % tokens (22,070 vs 16,290). Quality signal: 5.5-low points several tabs at one data source (1.5 vs
  0.25) → medium arm.
- A/B: faithfulness judge is informational only in the pipeline (background, 20 % of claims, Langfuse score + warn log;
  never drops output); gpt-6-luna strictly stricter (8 A-grounded → not grounded, never the reverse; sometimes wrong)
  → keep gpt-4o-mini; switching needs a re-baselined golden — question.
- A/B: tier probe timeout (4.0 s) → no retry / no fallback; consumers fall back (tier from category, plan without hint)
  → run degraded, not failed. H5.5 had 1 call at 4,058 ms.
- A/B step 2 ($0.144; total $1.084): plan S5.5 medium reads the warm 1h block ($0.027–0.035/call), 11.3 s, duplicate
  data sources unchanged 1.5 vs 0.25; tier probe H5.5 has_visual_demo stable across 2 passes (2 systematic misses:
  travel-vietnam-10day, review-airpods-pro-2), latency p50 1.14 / p95 1.32 / max 4.06 s (1/36 over 4 s) vs H4.5 max
  1.32; extraction H5.5 medium (DATA) items 0.866 (noise 0.904), jMq8 recipe steps NOT recovered, T1dQ tech.topics
  31 / 25 items vs cap 20 (cap not enforced in code).
- PICKS (coordinator, rule quality → latency → cost): plan S5.5 low (golden duplicate-rate gate decides; reconcile
  dedupe is per (dataSource, component) only); vision S5.5; extraction + memory + tier probe STAY Haiku 4.5;
  description / quiz / output + RAG translation gpt-6-luna; faithfulness / synthesis / chapter_detect unchanged
  (gpt-4o-mini fast tier). Deviations from D29's targets (extraction, memory, tier probe stay) → g4 with evidence.
- Questions: extraction prompt change ("one step per action", fill to cap?) + re-test before H5.5 extraction; a
  separate LLM_MEMORY_MODEL (memory on H5.5 = 4.1 vs 9.1 s → earlier hero); enforce list caps in code; reconcile
  drop/re-point a second tab on the same dataSource; the vietnam / airpods visual-demo labels; the faithfulness judge.
- 4.0d config (uncommitted, pre-R4d): primary MODEL_MAP anthropic default → anthropic/claude-sonnet-5-5 (plan +
  /health only); LLM_EXTRACTION_MODEL default → anthropic/claude-haiku-4-5-20251001 (config + both anchors);
  LLM_VISION_MODEL → anthropic/claude-sonnet-5-5; LLM_ENRICHMENT_MODEL / LLM_DESCRIPTION_MODEL /
  LLM_TRANSLATION_MODEL → openai/gpt-6-luna; dead LLM_MAX_TOKENS / LLM_FAST_MAX_TOKENS / SCENE_MAX_FRAMES removed;
  MODEL_CHAR_LIMITS 600K chars for the 5.5 models + gpt-6-luna; llm-common cache rates S5.5 $2.00/$0.20, H5.5
  $0.10/$0.01, gpt-6-luna $0.10/$0.01. Dev `.env`: keep LLM_EXTRACTION_MODEL; remove LLM_MAX_TOKENS,
  LLM_FAST_MAX_TOKENS (ignored now); no vision/enrichment/description/translation lines → defaults apply.
- HAND-OFF for part 2 (4.1: env examples + INFRASTRUCTURE; 4.2: docs): `.env.example` 156 `# LLM_MODEL=anthropic/claude-sonnet-5-5`;
  175 → `# LLM_ENRICHMENT_MODEL=openai/gpt-6-luna` (live value today — overrides the default!); 176
  `# LLM_VISION_MODEL=anthropic/claude-sonnet-5-5`; 179 + 181 `openai/gpt-6-luna` (description, translation); 184
  `# LLM_EXTRACTION_MODEL=anthropic/claude-haiku-4-5-20251001`. `.env.production.example` 80 + 82 comments (blank =
  Sonnet 5.5 / gpt-6-luna), 159 extraction stays Haiku 4.5. INFRASTRUCTURE 319 LLM_MODEL = Sonnet 5.5 (plan only),
  322 delete, 323 extraction default Haiku 4.5, 324/325/327 gpt-6-luna, 328 Sonnet 5.5 (+ the two dead
  LLM_*MAX_TOKENS rows). SERVICE-SUMMARIZER 187 remove LLM_FAST_MAX_TOKENS, 196 LLM_MODEL Sonnet 5.5 (plan), 238
  vision Sonnet 5.5, 395 frames on Sonnet 5.5, 690 translation gpt-6-luna, 1271 extraction default Haiku 4.5.
  `tests/conftest.py` docstring (enrichment pinned to Haiku 4.5) → 4.1.
- R4d-A: every llm_usage row has `provider: "unknown"` (extract_provider on the unprefixed model — same root cause as
  the cache-rate miss) — fix? (changes admin per-provider views).
- R4d-A: OPENAI_API_KEY is optional in both composes while 4 stage defaults are now openai/gpt-6-luna → a deploy with
  only an Anthropic key silently loses description / quiz / translation (fast-tier calls never fall back) → startup
  warning when a stage default is openai/* and no key is set?
- R4d-A: confirm gpt-6-luna is enabled for the PROD OpenAI key/org (the A/B ran on the dev key) — added to the
  env-changes rate-limit row.
- PICK CHANGE after R4d-B: plan → Sonnet 5.5 at effort MEDIUM (low flipped is_learnable on T1dQ ×2 → no quiz tab).
  Description / quiz decided by a re-measure with noise floors (4.0d agent; outcome → here).
- R4d-B questions: golden tier probes ran without YouTube description/category/tags (prod probe gets them) — the 2
  has_visual_demo misses may come from missing inputs; H5.5 can't run at temperature 0 (A's floor at temp 0 unknown);
  memory's own numbers favour H5.5 (4.05 vs 9.05 s, 9× cheaper, outline valid, evidence 0.82) — stays only via
  LLM_EXTRACTION_MODEL coupling (→ LLM_MEMORY_MODEL question); BOTH Sonnet 5.5 plan arms flip `has_ranking` on uC45
  (gaming.rankings removed) — watch in the golden.
- (post-crash, 2026-10-13 ~21:00) → FINAL-REVIEW FIX COMMIT: (1) `_model_ab_report.py` "p50" = the LOWER-middle value
  for even n (quiz 2 calls: table 4.98 / 6.88 s vs true medians 5.2 / 10.5 s used in Picks + config.py; vision A true
  median 22.2 s vs table 16.8 s; B 5.2 vs 4.49; C 8.9 vs 8.6) → true median + `--rescore` (offline; keep the Picks
  section) and re-check every number quoted from the tables (config.py comments, env templates, docs, g4.md).
  (2) config.py comments (4.1 part-2 agent): "None → the fast tier; extraction + memory → primary" omits vision (blank →
  primary too); `LLM_MODEL` example still `claude-sonnet-4-6`; `CHUNKED_EXTRACTION_THRESHOLD` "parallel Sonnet calls"
  (extraction runs on Haiku 4.5). (3) g4-notes HAND-OFF / PICKS / "4.0d config" bullets above are STALE: quiz stays
  gpt-4o-mini, plan at effort medium; R4d-A "4 stage defaults openai/gpt-6-luna" → 2 (description, translation).
- R4.1b (4.1 part 2 review, 0 C / 5 W, folded): prod template — OPENAI_API_KEY → REQUIRED, description + translation
  lines added, `LLM_FAST_PROVIDER=openai` (prod value; the gpt-4o-mini judge baseline), extraction live pin commented;
  `.env.example` LLM_FAST_MODEL also read by the assistant's quiz tool. Left: `tests/conftest.py:177` mock model
  `claude-sonnet-4-6` (fixture data) + conftest's pre-existing ruff debt (I001 / F401 / E501 on HEAD).
- g4 question (R4.1b, pre-existing): `.env.example:148` live `LLM_FALLBACK_PROVIDER=gemini` with a placeholder Gemini
  key → a fresh dev clone falls back to Gemini with an invalid key (dev `.env` uses openai) — match the template?
- g4 question (R4.1b): docs/DEPLOY.md never mentions an OpenAI key, which the 4.0d openai/* defaults now need.
- g4 question (4.2 part 2): `PIPELINE_VERSION` still v9 after bac879b moved plan / vision / description / translation to
  new models; `docs/IDEMPOTENCY.md:102` lists a model change as a reason to bump → v10 (cross-user dedup recomputes,
  `vie:response` keys roll) or v9 covers phase 4? Decide BEFORE the golden (a bump changes what the golden/dev runs hit).
- g4 question (4.2 part 2): `CLAUDE.md` Quick Start says only "Add ANTHROPIC_API_KEY" — since 4.0d the default setup
  needs OPENAI_API_KEY too (Kfir's file). README + DEPLOY + SECURITY + prod template now say both.
- 4.2 part 2 HELD for gate-4.0 numbers: `README.md:148` cost line ("About $0.29 … v8 ledger"); eval "weekly" →
  "twice a month" after the first scheduled run.
- R4.2b (4.2 part 2 review: lens A 0 C / 4 W, lens B 0 C / 7 W) → all 11 folded by the 4.2 agent (docs).
- → FINAL-REVIEW candidate (code, 4.0 scope): frame vision's refused-batch re-send (Sonnet 5.5 → Sonnet 4.6) runs inside
  `asyncio.wait_for` sized for one call, no `call_deadline` (frame_analyzer.py:276-318) — a re-send timeout lands as a
  plain TimeoutError on the REFUSED model, no `refusedModel`, no LLMRefusalError; same for the description's 30 s cap
  (description_analyzer.py:194-200). Vision is now on Sonnet 5.5 → `_send_batch` under `call_deadline(attempt.timeout+5)`?
- g4 question (R4.2b): `pipeline_timing.py:26,:53` comments say `counts.fallbacks` is "cross-provider only"; code
  (:191-193 + `is_fallback_response` :328) also counts provider-side model swaps — comment behind.
- g4 question (R4.2b): `llm_usage.provider` = "unknown" (R4d-A) also blinds `scripts/audit_cache_credits.py` (exit 1 "no
  Anthropic calls") and admin `/usage/stats?provider=` (usage.py:130-131) — derive provider like `_rate_key`?
- g4 question (R4.2b): docs cite `dev/active/pipeline-1min/gates/g4-model-ab.md` (SERVICE-SUMMARIZER ×4, OBSERVABILITY ×2,
  summarizer-workflow) — paths break on archive → repoint at close-out (ties to 4.2 Q1).
- g4 question (4.2 part 2 agent): the assistant routing docs (PROJECT-BRIEFING.md:368, SERVICE-ASSISTANT.md) describe a
  `cross_reference` tool and a keyword intent router that don't exist in `services/assistant/src` (`tool_router.py` maps
  action names only) — rewrite those sections?
