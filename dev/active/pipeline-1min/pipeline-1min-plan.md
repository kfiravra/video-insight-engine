# pipeline-1min — Plan

Last Updated: 2026-10-07
Status: PLANNED, REVIEWED by Kfir 2026-10-07 (8 fixes applied: C19–C21 added, C11/C18 resolutions changed, 1b.7 checkpoint, 1d.7, overview RAG fix in 1d.5, `FRAME_EXTRACTION_ENABLED` in 1d.6). EXECUTING since 2026-10-07 in the main tree on branch `feat/pipeline-1min` (worktrees retired) (one branch, six PR groups — see D14 in context.md).
Owner: Kfir. Executor: CC.
Brief: the `/task-plan pipeline-1min` message of 2026-10-07 (verbatim copy: `pipeline-1min-brief.md` in this folder).
Evidence: `evidence/A-DIGEST.md` (A1–A27 condensed), `evidence/ans-*.md` (the 27 answers, verbatim),
`evidence/CODE-MAP-orchestration.md`, `evidence/CODE-MAP-eval-web-registry.md` (file:line anchors, read 2026-10-07 on `docs/readme-rebuild` @ 015cf26 = `origin/main` code).

## Executive Summary

A 20-minute captioned video takes 171–240 s on prod and shows nothing until ~226 s because every
stage is serial (metadata → transcript ∥ frames-incl-vision → classifier → plan → ONE extraction call
→ synthesis ∥ enrichment → assembly → all tabs at once). The output clock is LLM output tokens
(~45 tok/s Sonnet, ~100 tok/s Haiku), and today's single extraction call emits ~8.6k tokens of which
~30 % feeds no tab.

Target: first visible content ≤ 30 s, every tab ≤ 60–65 s, equal-or-better quality, equal-or-lower
cost. Four shipped phases + a sweep, each behind a gate report and a prod deploy:

| Phase | What it buys | Expected on T1dQhQAm8Tc (240 s today) |
|---|---|---|
| 0 measure + foundations | `pipeline.timing`, replay harness, registry, eval gate, baseline | no change (measurement only) |
| 1 orchestration + prompts | plan ∥ memory no longer wait for frames; t=0 downloads; vision batched; honest prompts | ≈ 130 s, hero ≈ 25 s |
| 2 progressive output | per-tab `tab_ready`, provisional Moments, frontend reducer fixes | first visible tab ≤ 35 s |
| 3 the split (v9) | field-group extraction in parallel, reconcile, per-group assembly | ≤ 65 s all tabs, hero ≤ 30 s |
| 4 sweep + docs + report | dead code/settings gone, docs true, before/after report | — |

Principles that constrain every decision: readers get the full transcript, only writers get split;
output contract (domain-keyed JSON, Pydantic models, `tabs`/`meta` shape) unchanged; the plan has the
final say; nothing is invented to fill a schema; vision stays on Sonnet; reuse existing seams.

## Current State Analysis (verified against code, 2026-10-07)

### Measured critical path (prod run T1dQhQAm8Tc, request 725d2eb2)
metadata 17.1 s serial (yt-dlp 5.0 + caption 429 retry + description LLM 4.9 awaited inside the phase)
→ frames branch 85.7 s (pass-1 dl 11.1, scene detect 21.2, score 8.3, hi-res 6.7, S3 1.9, **Sonnet
vision 35.8**) → classifier 2.2 → plan 27.9 (1,389 out; cache written, never read) → chapter_detect
3.4 → ONE Haiku extraction 76 s / 8,606 out → synthesis 4.6 ∥ enrichment 12.0 → assembly → moment
fill re-downloads 720p (11.7 s) → first `tab_ready` ≈ 226 s. LLM $0.186/run, proxy 143 MB/run.
HIGH-tier vision (5 cooking runs): 37–40 frames in one call, 46–63 s, $0.13–0.15.

### Serial waits (CODE-MAP-orchestration §1)
1. Description LLM awaited inside metadata (`phases/metadata.py:37`) before transcript/frames start.
2. Classifier + plan wait for the whole transcript ∥ frames group incl. vision (`pipeline_runner.py:218→246`, `triage.py:44→84`).
3. Moment fill awaited before the moment `tab_ready` (`phases/assembly.py:105-110`).
4. 720p downloaded up to 3× (prefetch deleted `scene_extractor.py:734`; refiner `hires_refiner.py:141`; moment fill `moment_frame_fill.py:137`); the zero-frame return (`scene_extractor.py:574`) skips the static-camera fallback (fires only for 0 < n < 12) and cancels the prefetch.
5. Visual injection bug: `scene_frames.py:244` reads `startMs`, segments carry `start`/`duration` → all `[VISUAL]` lines stack at segment 0, pollute `clean_text` for plan/Qdrant/S3/faithfulness.

### Prompt/contract defects
- Extraction loads the full schema of every planned tag, never sees `dataSource`; ~30 % of output unused (median over 42 v8 docs: 32.6 % unused-by-plan).
- `plan.py:181` copies `dataSource` unvalidated → 3 of 7 extraction retries; retry improved 0 of 3; single-tag validator validates the raw wrapped dict (`domain_types.py:1206-1215`).
- Extraction prefix before the cache breakpoint is per-RUN (title, duration ×3, DNA, key frames, tab goals): 40 prefixes for 61 calls → cache reads only from chunked batch ≥ 3.
- Enrichment runs always, 60 % of docs use nothing, flashcards/scenarios rendered 0×; json_object mode put flashcards in `quiz` → whole EnrichmentData discarded.
- No stage sets `temperature`; `LLM_NUM_RETRIES` dead; LiteLLM `fallbacks=[openai/gpt-4o]` swaps provider on ANY exception before the retry wrapper sees it (`llm_provider.py:308-309`), fallback recorded under the configured model.
- Visual tier fixed from the uploader's YouTube category at frames-phase start (Entertainment ⇒ `cooking (0.15)` dict-order tie-break ⇒ food ⇒ HIGH); 2 of 3 prod HIGH runs were tech screen recordings.

### Settings
112 settings, 50 not passed through compose. `FRAME_VISION_*`, `FRAME_TIER_ENABLED`, `EXTRACTION_PARALLEL_BATCHES` (=2), all chunking knobs, `SCENE_THRESHOLD` are not in either compose anchor. Blank passthrough beats code defaults (Haiku vision/enrichment pins dead). `FRAME_EXTRACTION_ENABLED` defaults `false` in config, `true` in compose.

### Eval
`run_eval.py`: 24 golden entries, 10 disabled → 14 live; no language/music/narrative live. Main loop never sets `bypassCache` (`:481`; only `--stability` does, `:538`) → the 0.824 baseline scored cached output. No `gate.py`, no noise file, no faithfulness or duplicate-item metric in the score. `eval.yml` cron weekly (`0 3 * * 1`); live job boots compose from `.env.example` with no `YOUTUBE_PROXY_URL` → will fail from a GitHub IP.

### Frontend/SSE
`stream-event-processor.ts`: `meta` wipes tabs (`:225`); `position` is a splice index (`:279`); replace-by-id ignores position (`:273-277`); `synthesis_complete` REPLACES state (`:172-180`); no `final` flag anywhere. `OutputRouter.tsx:165,185-243`: `initialTab = tabDefs[0]` recomputed; placeholder/pending pills/Cancel vanish at first tab. `TabCoordinationContext.tsx:62-68` re-applies initialTab (a late VISIBLE tab steals focus; overview is filtered, never does). `resolve-display-tabs.ts:23-26` DB-vs-stream by tab count. Redis stream TTL only set in `mark_done` (`pipeline_event_stream.py:197-213`) → leaks.

### Registry
`packages/shared/src/config/domains.json` has 15 top-level keys, including an existing `enrichment` map (tag → enrich prompt path, 13 strings). `dist/domains.json` dated 2026-07-13 vs src 2026-08-25 (only local TS consumers; Docker/CI rebuild; Python reads src). `component_toolkit.txt:246-265` hardcodes 53 `valid_datasources` paths; all 50 domain paths resolve to schema keys. Bugs confirmed: default tab `tech.setup` (should be `tech.setup.commands`), `language.vocabulary → concept_canvas` (should be `flash_deck`).

## Brief-vs-code conflicts and how this plan resolves them

| # | Brief says | Code reality | Resolution (reported in the phase's gate) |
|---|---|---|---|
| C1 | Appendix A adds top-level `enrichment {quizDomains, flavor}` | `domains.json["enrichment"]` already exists (tag → prompt path); object values break `index.ts:102`, 3 tests, `ENRICHMENT_MAP` import-time snapshot | Phase 0 adds the new map as **`quizEnrichment`**. Phase 1d deletes the old `enrichment` map with the enrich files. Deviation reported at gate 0. |
| C2 | B.5 cache breakpoint on the user message | `llm_provider.complete()` puts `cache_control` only on a system block (`:166-174`); `call_llm_fast` has none | Phase 1c adds provider support for a user-content block breakpoint (content as list of blocks). |
| C3 | temperature 0 for probe/memory | No stage sets temperature; Langfuse `modelParameters: None` | Phase 1b plumbs a `temperature` kwarg through `LLMService`/`LLMProvider`/`call_llm_with_retry`/`_wrap_with_override`. |
| C4 | honour `retry-after`, one same-provider retry before cross-provider fallback | LiteLLM `fallbacks` fires on any exception first; `_LITELLM_NUM_RETRIES=0`; fallback logged under configured model | Phase 1d moves fallback into `call_llm_with_retry`; LiteLLM `fallbacks` dropped. |
| C5 | memory on Haiku via `LLM_EXTRACTION_MODEL` | setting defaults `None` (= primary Sonnet) | Memory reads `LLM_EXTRACTION_MODEL` as written; no new setting. Kfir confirms `.env.production` pins it to Haiku (Appendix F list). |
| C6 | fast-first path "in `llm_retry.py`" | it lives in `extractor.py:201-224`; `llm_retry.py` only routes `use_fast_model` (shared by other stages) | Remove from `extractor.py`; `llm_retry.py` keeps `use_fast_model` for the stages that still use it. |
| C7 | `chapter_detect` only when > 1 batch | `batch_chapters` requires chapters; batch count unknown before chapters | Pre-check: call only when duration > 40 min OR token estimate > `MAX_TOKENS_PER_BATCH` AND no memory outline. |
| C8 | vision after hi-res from the kept 720p | HIGH vision runs on 1024-upscaled 360p detection frames INSIDE Step 6b, before hires; STANDARD after upload | Phase 1d: measure 360p-native vs 720p-after-hires on 2 videos; the HIGH-path reorder is part of the chosen option. |
| C9 | `synthesis_complete` early; reducer must merge | reducer REPLACES (`:172-180`); API relay persists only when `masterSummary` present (`stream.routes.ts:156-166`) | Phase 1b: second emission is a superset; reducer changed to merge in phase 1b (small, needed for the hero). |
| C10 | Langfuse spans with `end_time` for non-LLM phases | summarizer Langfuse client has no `.span()` | Phase 0: persist `phase_times` in `pipeline.timing` + attach as trace metadata; add spans only if the installed SDK's trace object exposes them (check version first). |
| C11 | remove `inject_visual_context` | Qdrant ingestion and the faithfulness judge read the annotated `clean_text` | Visual facts stay in RAG: the rendered `<visual_annotations>` are indexed as separate chunks (`source=visual`, with timestamps) in the same collection; transcript chunks and the S3 blob stay clean; faithfulness receives transcript + the rendered block. (Kfir, plan review 2026-10-07.) |
| C12 | merge scalars first-wins | `extraction_merger` keeps the LONGEST string, numbers first-wins; renumber clobbers shirt numbers; dedup key order nondeterministic | Phase 3: scalar policy made explicit + deterministic key order; renumber limited to list indices. |
| C13 | `EXTRACTION_PARALLEL_BATCHES` → 6 | currently 2, not in compose; rationale measured on Sonnet; org tier unverifiable; zero 429s ever | Default 6 + compose passthrough; the 429 counter in `pipeline.timing` is the guard; back off to 4 if any 429 at gate 1. |
| C14 | vision `max_tokens` scaled per batch | floor already truncated an 8-frame call (2,000 out) | Scale per frame TYPE (screen recordings ~190 tok/frame vs food ~85), not only per count. |
| C15 | "late overview cannot steal focus" | overview is filtered; a late VISIBLE tab steals focus via initialTab re-apply | Phase 2 rule: `initialTab` sticky once chosen (covers both). |
| C16 | description LLM "joins the trace" | `description_analyzer.py:151` imports `acompletion` directly, bypasses provider + Langfuse | Phase 0: route it through the provider (also makes the replay seam cover it). |
| C17 | dev disk-vs-registry prompt switch | none exists; registry wins whenever Langfuse keys are set; vision prompt is an inline constant | Phase 0 adds `PROMPT_SOURCE=registry\|disk` (dev-only, default registry); vision prompt gets a file + registry entry in 1d. |
| C18 | eval "every two weeks" + live CI set | cron is weekly; live job boots compose on the runner and lacks proxy/S3/OpenAI secrets | Phase 0: cron `0 3 1,15 * *`; the scheduled eval targets the **prod API as the eval user** (`EVAL_API_URL`, `EVAL_USER_EMAIL/PASSWORD`, Langfuse keys) instead of booting compose on GitHub — no proxy/S3/OpenAI secrets on GitHub. Gate-0 decision item records what that requires: `bypassCache` allowed for the eval user; the eval user's daily quota vs 18 videos × 2 for the noise run; prod worker time at 03:00. (Kfir, plan review.) |
| C19 | conditional domain requirements in phase 3 reconcile | the food-travel-vlog golden assertion (no recipe components) cannot pass at gate 1 if backfill stays unconditional | Moved to phase 1: 1b.2 renders `domain_requirements` conditional on the plan's own evidence; 1d.7 makes the assembly backfill check `requirementEvidence` against `plan.evidence`. Phase 3 reconcile keeps the memory second opinion and the demotion. |
| C20 | drop `<outbound_links_instructions>` | it is the only explicit output-language instruction in `plan.txt` | 1b.1/1b.2/1b.3 add an explicit output-language line (generation language = English; labels/goals per today's rule) to probe, plan and memory; the non-English regression run verifies it. |
| C21 | uC45_4nnEAI golden assertion "moment frames without scene frames" | after the 1a.3 ladder uC45 WILL have scene frames | Assertion becomes "moment images present and no failure on 0 candidates". |

## Proposed Future State (v9)

See brief §2 for the timeline. The shape in one line: at t=0 metadata ∥ low-res download ∥ 720p
download ∥ description; at transcript-ready a 1 s tier probe, then PLAN (Sonnet, full transcript with
`[m:ss]` markers) ∥ MEMORY (Haiku, outline/evidence/tldr/takeaways → hero ≈ 24 s); vision in 8-frame
parallel Sonnet batches; at plan-done RECONCILE (code) → field groups from the registry → text groups
fire staggered under one cached `[transcript + video_memory]` block, the visual group at frames-done;
each group assembles and emits its own tabs; quiz only when a quiz tab survived; synthesis ∥ moment
fill ∥ faithfulness; persist in background. Done ≈ 62 s.

## Implementation Phases

Conventions for every phase: the main tree on `feat/pipeline-1min` (no worktrees) (six PR groups: p0 · 1a+1b · 1c+1d · p2 · p3 · p4), never switch branches
in the shared tree; commits per task id under Kfir's standing permission, no push/PR without his word; gate report ≤ 1
page then STOP; prod benchmark only after the merge deploys (CI → `deploy.yml` on `workflow_run`);
state run count + estimated cost before every prod/eval spend; changed prompts re-registered in both
Langfuse projects at phase end; every touched setting goes through all 8 touch points (context.md).

### Phase 0 — measure and foundations (one PR, no user-visible change) — effort L

| # | Task | Acceptance criteria | Effort | Depends |
|---|---|---|---|---|
| 0.1 | `pipeline.timing` on the Mongo doc: phase start/end (from `ctx.phase_times`, today logged never persisted), every LLM call (feature, model, in/out/cache-read/cache-write tokens, wall, retries, fallback used, 429 count), every download (which, bytes, wall), first `tab_ready`, `complete`, `done`. Written in the existing `pipeline` `$set` (`phases/assembly.py:158-169`). Trace metadata carries the same. DONE log line logs planned/assembled/emitted tab counts. | Schema test; record present and complete on a replay run; DONE line shows 3 counts. | M | — |
| 0.2 | Description LLM through `LLMProvider` (C16) so it is traced, ledgered and fakeable. | Langfuse trace shows the description observation; ledger row tagged. | S | — |
| 0.3 | Replay harness (test fixture under `services/summarizer/tests/replay/`): fake at `llm_provider.acompletion` (4 call sites) keyed by `llm_feature_var` + ordinal, replaying recorded outputs with recorded `latencyMs`; stubs for yt-dlp/transcript, frames + S3, Whisper, embeddings/Qdrant with configurable sleeps; driver over `stream_summarization`; cassettes from Langfuse + Mongo dumps for the 3 benchmark videos; `scripts/replay.py --video <id> [--speed 0]`. | T1dQhQAm8Tc replay reproduces phase walls within 10 % of 240 s; test asserts zero network (socket guard). | L | 0.1, 0.2 |
| 0.4 | Registry in `domains.json` as NEW top-level maps: `dataSources` (one entry per `valid_datasources` path, 53), `demoteTo`, `requirementEvidence`, `quizPolicy`, `quizEnrichment` (C1), `grouping`. Fix `tech.setup → tech.setup.commands`, `language vocabulary → flash_deck`. `outputWeight` from the 42 stored v8 docs (tokens per item per field). Rebuild `packages/shared/dist`. `component_toolkit.txt` `<valid_datasources>` rendered from the registry via a `{valid_datasources}` placeholder in `domain_config.py`. | Registry tests: every path resolves to a schema field; every `defaultTabs.dataSource` exists; every component in `domainRequirements`/`playbooks`/`demoteTo` exists; evidence keys ∈ vocabulary; caps ints; `tsc` on `@vie/shared` green; toolkit render test. | M | — |
| 0.5 | Plan-time dataSource validation against the registry at `plan.py:181`: sibling with the same component, else drop the tab (logged + `droppedTabs`). | Unit tests: invalid path → sibling; no sibling → dropped. | S | 0.4 |
| 0.6 | Single-tag validator fix (`domain_types.py:1206-1215`): unwrap the wrapped response like the multi-tag branch. | Regression test with OuNKBjuV7A4's shape. | S | — |
| 0.7 | Eval: `run_eval` main loop sets `bypassCache`; cron → `0 3 1,15 * *`; the scheduled job targets the **prod API as the eval user** (`EVAL_API_URL`, `EVAL_USER_EMAIL/PASSWORD`, Langfuse keys) — no compose boot on the runner (C18); 4 new golden videos with per-video assertions (expected format, forbidden components, quiz absent-or-last, min items, no timestamp beyond duration): a food-travel vlog → no recipe components; recipe with long story intro → recipe components; uC45_4nnEAI → moment images present and no failure on 0 candidates (C21); Jru5B044HOs → no quiz. Add faithfulness + duplicate-item rate as primary metrics. Run the live set twice → per-metric noise file. `scripts/gate.py` exits non-zero when a primary metric drops more than its noise. Baseline stored as CI artifact + Langfuse dataset run. Gate-0 decision item: `bypassCache` allowed for the eval user; eval-user daily quota vs 18 videos × 2; prod worker time at 03:00; final secrets list. | `gate.py` unit test with a synthetic drop; noise file produced; assertions schema test; workflow dry-run against prod with `--limit 1`. | L | — |
| 0.8 | Tier-probe A/B, offline: gpt-4o-mini vs Haiku 4.5, temperature 0, Appendix B.1 input, over the golden set; agreement with expected domain/format and `has_visual_demo`. Recommend one. | Report table in the gate; no pipeline spend. | S | 0.7 (set) |
| 0.9 | `PROMPT_SOURCE=registry\|disk` dev-only switch (C17) through the 8 touch points. | Test: disk mode never calls Langfuse. | S | — |
| 0.10 | Baseline on prod: 3 benchmark videos × 2 runs → phase walls, first tab, total, cost (from `pipeline.timing` + ledger). State runs + cost first (6 runs ≈ $1.1). | Table in gate 0. | S | 0.1 deployed |

**Gate 0**: harness reproduces 240 s ± 10 %; baseline + noise recorded; registry tests green; A/B recommendation given; C1/C10/C17 deviations reported.

### Phase 1 — orchestration + prompts (PIPELINE_VERSION unchanged) — effort XL

Still one extraction call per batch with the planned domains' schemas, so the golden gate measures the prompt effect before the split.

**1a — inputs and media**

| # | Task | AC | Effort |
|---|---|---|---|
| 1a.1 | t=0 parallel group: metadata ∥ low-res download ∥ 720p download ∥ description analysis (`validate_duration` first); one yt-dlp `extract_info` shared by metadata/subtitles/format URLs if the code allows. Target metadata ≤ 6 s. | Replay: metadata ≤ 6 s (stubbed); description no longer awaited in metadata. | M |
| 1a.2 | One 720p file per job: remove refiner fallback download and moment-fill download; prefetch kept (not cancelled on the zero-frame return); hi-res frames + moment fill seek the kept file; remove the stream-URL pass. | Replay: exactly one 720p download; proxy bytes/run reported. | M |
| 1a.3 | Zero-candidate scene path → static-camera fallback via threshold ladder 0.3 → 0.15 → uniform sampling; ffmpeg rc checked. | Unit test on the ladder; uC45 cassette yields frames. | S |
| 1a.4 | `render_transcript(segments, every=20)` → `[m:ss]` at segment starts crossing each 20 s boundary; prompts only (`clean_text`, Qdrant, S3 unchanged); chunked slices keep absolute times. | Unit tests: spacing, absolute times on chunks. | S |
| 1a.5 | Filler removal into basic cleaning; spaCy pass stays behind `TRANSCRIPT_CLEANING_ENABLED=false`. | Cleaner tests updated. | S |

**1b — tier probe, plan, memory**

| # | Task | AC | Effort |
|---|---|---|---|
| 1b.1 | `classify.txt` → `tier_probe.txt` (B.1): title, channel, description ≤ 500, category, tags, duration, 3 clean windows (~700 chars; never annotations) → `{domain, format, has_visual_demo, confidence}`; temperature 0 (C3 plumbing); max_tokens 80; model per A/B. Consumers: `derive_tier(probe, title)`; playbook selection; one `Hint:` line in plan. Runs at transcript-ready; frames branch awaits it before Step 6b with a 3 s cap, else metadata rule. Explicit output-language line (C20). Delete `FRAME_TIER_EARLY_CLASSIFIER` + `category_confidence` (both confirmed dead). | Validator tests; tier resolver cap + fallback test; `temperature` visible in Langfuse `modelParameters`. | M |
| 1b.2 | `plan.txt` (B.2): full transcript with markers replaces the 3,000-char preview; drop `reasoning`, `outboundLinks`, `identity.audience`, `itemCounts`; add `brief {what, where, expect}`, `evidence` (Appendix C keys), `terms` ≤ 12; remove `<outbound_links_instructions>`, the `enrich` attachment hint, "Always include learning"; "3–6 tabs, only tabs the content supports"; `<extraction_caps>` from the registry; `<format_routing>` on the plan's own evidence; **`domain_requirements` rendered conditional on the plan's own evidence** (food: checklist + step_player only when `has_ingredients` / `has_steps`) (C19); explicit output-language line (generation language = English, labels/goals per today's rule) replacing the one lost with the outbound-links block (C20); 6 examples rewritten; timeout 45 s, 1 retry, no `cache_control`; `_build_fallback_plan` unchanged (empty briefs). Readers of the dropped fields updated. | Plan validator tests (new schema + fallback); no reader of removed fields left (grep test); requirements-render test (evidence false → requirement absent); language line present in the rendered prompt (test). | L |
| 1b.3 | `memory.txt` (B.3) on `LLM_EXTRACTION_MODEL` (C5): outline 4–12 sections (chapter_detect rules), evidence, tldr ≤ 150 chars, takeaways 3–5; temperature 0, max_tokens 1,200, timeout 25 s, 1 retry; explicit output-language line (C20); runs ∥ plan from probe-done. Failure: no outline; evidence = plan's; tldr/takeaways from synthesis as today. | Validator + fallback tests; language line test. | M |
| 1b.7 | **Checkpoint (visibility, not a gate)**: after 1b lands, replay timing on the 3 cassettes + one dev run on jMq8lEu-of0; short note in tasks.md (plan start time, hero time, any surprise). | Note recorded. | S |
| 1b.4 | `video_memory` renderer (B.4) from plan + memory into the existing `{video_context}` slot of extraction/enrichment/synthesis; byte-identical across calls of a run. | Renderer test (identical bytes across 3 calls). | S |
| 1b.5 | `synthesis_complete` emitted at memory-done `{tldr, keyTakeaways}`; full superset dict re-emitted at synthesis-done (C9); reducer `:172-180` → merge; API relay persists both. | Reducer unit test; SSE event typed in `packages/types`. | S |
| 1b.6 | `chapter_detect` only when the pre-check says > 1 batch AND no outline (C7); it receives the real description. | Unit test on the gate. | S |

**1c — extraction prompt (still one call)**

| # | Task | AC | Effort |
|---|---|---|---|
| 1c.1 | `base_extraction.txt` (B.5 / Appendix D): remove `<critical_rule>`, "Fill all fields", `<completeness>` → registry caps; `<tabs_to_serve>` = briefs; `{video_context}` = video_memory; `<visual_context_guide>` only with annotations; "an empty array is correct…"; example density line → specificity. | Prompt render tests; placeholder test updated. | M |
| 1c.2 | Annotations out of `clean_text`: `frame_descriptions` + OCR render as `<visual_annotations>` (chronological `[m:ss] caption \| on-screen text`) after the transcript, + ≤ 12-line key_frames; plan/memory/probe never see them; remove `inject_visual_context` and Phase 2.5 (`pipeline_runner.py:173`); faithfulness gets the rendered block; **RAG keeps visual facts**: the rendered annotations are indexed as separate Qdrant chunks (`source=visual`, with timestamps) in the same collection, transcript chunks + S3 blob clean (C11). | Renderer test (chronological, OCR joined); plan/memory inputs contain no annotation text (test); ingestion test: visual chunks tagged `source=visual` with timestamps, transcript chunks annotation-free. | M |
| 1c.3 | Cache layout (A26, C2): system = rules only; user = `[transcript + video_memory]` ← one breakpoint (provider gains a user-block breakpoint), then `[schema(s) + briefs + caps + example + annotations]`; `{duration_minutes}` and every per-video value out of the static block. | Test hashing the static block across two videos (identical); cache-read tokens > 0 on batch 2 in replay telemetry. | M |
| 1c.4 | 16 schema files: strip SCALING blocks + invention floors; keep skeleton, `> UI`, RULES, COMMON MISTAKES; narrative "merge INTO" → own key; travel weight wording; finance consumer confirmed; `{duration_minutes}` removed. Examples: header line changed; no learning fallback for missing-example domains. | Schema parse test after stripping; example-selection test. | M |
| 1c.5 | Remove `_attempt_synthesis_fed_retry`, `decide_extraction_retry`, the count-validation retry gate (coverage stays a metric); remove `EXTRACTION_USE_FAST_FIRST` + fast-first path in `extractor.py:201-224` (C6), through the 8 touch points. | Tests for removed paths deleted; coverage metric test kept. | S |

**1d — tail**

| # | Task | AC | Effort |
|---|---|---|---|
| 1d.1 | Enrichment → quiz only: one `enrich_quiz.txt` (B.6) with flavor from `quizEnrichment.flavor`; demand-driven (runs only with a `quiz_arena` tab or an allowed `quick_quiz` host); input = extraction data + video_memory + tab goals; `quiz` 2–8; strict schema, salvage valid items; timeout 30 s; never blocks tabs. Remove flashcards/scenarios, their caps, the 11 enrich files, the old `enrichment` map (C1) + `get_enrichment_map()`, `enrichment.flashcards/scenarios` from the toolkit, the plan.txt scenarios claim. | Salvage test; demand gate test; removed readers gone. | M |
| 1d.2 | `quick_quiz` hosts exclude `step_player`, `step_flow_canvas`, `checklist`, `workout_room`, `packing_mission`; quiz tab forced last (`quizPolicy`). | Host-exclusion + ordering tests. | S |
| 1d.3 | `synthesis.txt`: `masterSummary` + `seoDescription` only; input = video_memory + compact final extraction + tab labels; ∥ moment fill after extraction; `meta.tldr/keyTakeaways` from memory (same shape). | Synthesis validator test; meta shape test. | S |
| 1d.4 | Vision: 8 frames/call, ≤ 5 parallel (semaphore), max_tokens scaled per batch and frame type (C14), 1 retry, Sonnet, index→frame mapping in the prompt (A18); no 1024 upscale; `FRAME_VISION_PARALLEL=false` = single call; `LLM_VISION_MODEL` default = primary; vision prompt to a file + registry. Measure 360p-native vs 720p-after-hires on 2 videos (C8), pick, report. | Batching index-mapping test; semaphore test; measurement table in gate. | L |
| 1d.5 | Reliability: heartbeats around plan and extraction (reuse `run_parallel_phases`' heartbeat); embeddings preloaded at worker start; `drive_pipeline` status check before running (A4); LLM errors: honour `retry-after`, one same-provider retry before the cross-provider fallback inside `call_llm_with_retry`, fallback model tagged correctly (C4); `EXTRACTION_PARALLEL_BATCHES` default 6 + passthrough (C13); **overview tab RAG-indexed** — `output_chunker` reads `props.*` while overview stores `props.data.*`, so the overview is never indexed today (A23). | Retry/fallback unit tests (side_effect sequences); heartbeat present in replay; duplicate-run test; chunker test on an overview tab. | M |
| 1d.6 | New flags `EXTRACTION_PARALLEL`, `FRAME_VISION_PARALLEL` (bool, default true) + passthrough fixes for `FRAME_VISION_ENABLED`, `FRAME_TIER_ENABLED`, `EXTRACTION_PARALLEL_BATCHES`, chunking knobs — all 8 touch points; literal defaults in compose (A16 crash-loop note); **`FRAME_EXTRACTION_ENABLED`** defaults `false` in config and `true` in both compose anchors — make them agree (one value, documented). | `config.test.py`; compose config validates in dev + prod; config default == compose default. | S |
| 1d.7 | Assembly backfill of domain `required` components checks `requirementEvidence` against `plan.evidence` before backfilling (`effective_requirements()`); evidence false → no backfill, recorded in `droppedTabs`/log (C19). | Table test per domain in `requirementEvidence`; food-travel-vlog golden assertion passes at gate 1. | S |

**Phase 1 verification**: replay of 3 cassettes with the new orchestration (plan starts before frames-done; metadata ≤ 6 s; one 720p download; heartbeats present); golden full run → gate within noise + new assertions; prod benchmark 3 × 2 + regression set × 1 (state ~8 runs ≈ $1.5 first).

**Gate 1**: golden within noise (quality, faithfulness, duplicates); assertions pass; benchmark table; cost ≤ baseline; 0 429/fallback events in timing; prompts re-registered in both projects.

### Phase 2 — progressive output — effort M/L

| # | Task | AC | Effort |
|---|---|---|---|
| 2.1 | Backend: `tab_ready` per tab as it becomes final; provisional Moments tab at plan-done from the memory outline (fallback: YouTube chapters / description timestamps) replaced by the final one; final re-emit of all tabs with `final: true` + positions; `tab_ready` type gains `final`/`provisional` in `sse_events.py` + `packages/types/src/api.ts`; Redis stream key reset per run + TTL at start; leaked no-TTL streams cleaned (one-off script). | SSE replay test with the real broker on a scratch id (no duplicates, order, final flag). | M |
| 2.2 | Frontend reducer (`stream-event-processor.ts`): `meta` never wipes tabs; `position` is a sort key; `initialTab` sticky once chosen (C15); replace-by-id honours new position + `final`; DB-vs-stream by `final`/version (`resolve-display-tabs.ts`); timeline, pending pills, Cancel visible while tabs exist (`OutputRouter.tsx`); provisional tabs render with a "provisional" state; new phases in both `VALID_SSE_PHASES` and `SSE_PHASE_MAP`. | Reducer unit test per rule; Playwright smoke on dev: first tab before `complete`, no flicker on re-emit. | M |

**Gate 2**: first visible tab ≤ 35 s on phase-1 code (benchmark); no replay duplicates; golden unchanged.

### Phase 3 — the split (PIPELINE_VERSION → v9) — effort XL

| # | Task | AC | Effort |
|---|---|---|---|
| 3.1 | Field-aware renderer: dataSources set → JSON skeleton with only those keys (+ domain scalars for the domain's first group), RULES/COMMON MISTAKES lines naming those fields, example sliced, caps per field. | Round-trip test ×16 domains: slice parses, contains only requested keys. | L |
| 3.2 | Reconcile at plan-done (Appendix C): registry validity → sibling/drop; evidence demotion via `demoteTo` only when plan AND memory say false (the memory second opinion); conditional domain requirements already live since 1b.2/1d.7 — reconcile adds the doubly-false rule; quiz policy; dedupe (dataSource, component); 3–6; `pipeline.reconcile = [{tab, action, reason}]`. | Table-driven tests: every evidence combination, every domain's requirements, quiz policy. | M |
| 3.3 | Grouping from the registry: requested = ∪ dataSource + fallbacks + upgrades + scalars; partition `waitsForVisual` vs text; text by domain; split by field when `outputWeight × expect` > `groupOutputBudget`; ≤ `maxTextGroups` + 1 visual; group 1 = first planned tab's group. | Grouping tests (partition, split, caps, group-1 rule). | M |
| 3.4 | Firing: text groups at plan-done; group 1 first, wait for its first streamed token (or 1.5 s), then the rest; visual group at frames-done; semaphore = `EXTRACTION_PARALLEL_BATCHES`; `EXTRACTION_PARALLEL=false` → one call with the full requested field set. Per-call layout = 1c; tail per group; visual tail adds annotations + key_frames. | Stagger test (first token before the rest); cache-hash test across groups (identical block). | L |
| 3.5 | Validate per group against the same Pydantic item models without materializing defaults (A5); filter to own keys; deep-union merge chunk-major, explicit scalar policy + deterministic order (C12); `extraction_data` shape unchanged; count/coverage on requested fields only. | Merge tests (keys filtered, scalar policy, chunk order). | M |
| 3.6 | Per-group assembly + emission: a tab assembles when all its requested fields landed; `tab_ready{final:true}`; attachments at the final re-emit; overview/meta last; `resolve_cross_tab_links` at the end. Quiz after its owning group; synthesis ∥ moment fill ∥ faithfulness after the last group. | Per-group assembly test (tab waits for all fields); ordering test. | L |
| 3.7 | Long videos: chunks from the memory outline merged to ≤ 50k tokens / 40 min; calls = chunks × groups under the semaphore; annotations sliced per chunk for the visual group; merge chunk-major then group-union; `chapter_detect` only without outline. | Chunk × group test with a synthetic 90-min cassette. | M |
| 3.8 | Failure paths: one group fails after one retry → its tabs demote/drop, rest ship, `droppedTabs`; all fail → error as today; plan failure → fallback plan's dataSources as the requested set. | Failure-path tests. | S |
| 3.9 | Bump `PIPELINE_VERSION` (single file `packages/shared/.../pipeline-version.json`; force-recreate summarizer/worker/api); re-register prompts; demo library re-run script (`demo-export.sh` urls + `run_pipeline(bypass_cache=True)`), runs on Kfir's go. | Script dry-runs; version visible on new docs. | S |

**Phase 3 verification**: replay 3 cassettes with group timings synthesized from recorded output sizes; golden full run; prod benchmark 3 × 2 + regression set; cost per video from the ledger (requests tagged by group).

**Gate 3**: golden within noise on quality + faithfulness; assertions pass; duplicate items ≤ baseline; benchmark table; cost; 429 count 0. Expected: HIGH cooking ≈ 55–60 s, text-only ≤ 55 s, hero ≤ 30 s, cost below baseline.

### Phase 4 — sweep, docs, report — effort M

| # | Task | AC |
|---|---|---|
| 4.1 | Sweep leftovers: `inject_visual_context`, retry paths, chapter_detect on the single path, flashcards/scenarios, fast-first, `FRAME_TIER_EARLY_CLASSIFIER`, `to_video_context_full`/`video_dna_text`, `outboundLinks`/`itemCounts` readers, plan cache_control, old enrich files, `category_confidence`; `.env.example`/`.env.production.example`; `docs/INFRASTRUCTURE.md` flags table (document what is passed through). | grep-tests for removed symbols; compose config validates. |
| 4.2 | Docs: summarizer docs' stale lines (S3/region defaults, "SHORT < 30 min" vs 900 s, "3–6 LLM calls", pass-2 stream seek, fast model on prod, vision model, chunked semantics, new phases + v9 flow in `docs/summarizer-workflow.md` and `SERVICE-SUMMARIZER.md`); README numbers that changed; eval sentence → "twice a month" after the first scheduled run. | `/update-docs` pass; no stale claim left from the list. |
| 4.3 | Report: before/after per phase (phase walls, first tab, total, cost) for benchmark + regression sets; what each phase bought; levers not taken with projected gain (outline from plan instead of memory; slicing long videos by brief ranges; 720p vision; `WORKER_CONCURRENCY=2`; 1 h cache TTL; Haiku vision; m6i). | Report file in this folder + artifact link. |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Replay harness cannot reproduce 240 s within 10 % (sleeps vs real CPU-bound scene detect) | M | Blocks gate 0 | Model scene-detect/score as configurable sleeps from recorded `phase_times`; the 10 % target is on phase walls, not CPU. |
| Golden noise is large (14 live videos, LLM variance) so the gate never fires or always fires | M | Gate meaningless | Two baseline runs → per-metric noise; primary metrics = quality, faithfulness, duplicates; per-video assertions are deterministic and carry the real signal. |
| Prompt changes (no floors, "empty array is correct") thin out outputs | M | Quality regression | Briefs carry `expect` counts from the plan; golden `min items` assertions; gate 1 before any split. |
| Parallel groups hit 429 at `EXTRACTION_PARALLEL_BATCHES=6` | L (zero 429s ever, >12× headroom) | Latency spikes | 429 counter in timing; staggered firing; back off to 4. |
| User-block cache breakpoint not honoured by LiteLLM for Anthropic | L | No cache reads, cost ↑ | Verify in 1c with cache-read tokens in telemetry; fall back to system-block placement of the transcript if needed (report). |
| Registry `enrichment` key collision (C1) | certain | Build/tests break | `quizEnrichment` in phase 0; old map removed in 1d. |
| Removing annotations from `clean_text` changes Qdrant/faithfulness inputs (C11) | certain | RAG would lose visual facts | Visual annotations indexed as separate `source=visual` chunks with timestamps; faithfulness gets the rendered block; ingestion test in 1c.2. |
| Scheduled eval against prod at 03:00 competes with prod jobs / eval-user quota (C18) | M | Eval fails or slows prod | Gate-0 decision item: eval-user `bypassCache` permission, quota ≥ 36 runs/day for the noise run, `--limit` on the cron run; worker idle at 03:00 on a closed demo. |
| HIGH vision reorder (after hires) costs more at 720p (~1,000 tok/frame vs 314 native) | M | Cost ↑ | Measurement in 1d.4 decides; 360p-native is the default if quality equal. |
| Langfuse registry overrides disk edits mid-iteration | certain today | Wasted debugging | `PROMPT_SOURCE=disk` in dev (0.9); re-register at phase end. |
| Shared tree + parallel sessions; branch moves mid-session | M | Wrong-branch edits | One worktree per phase; `git branch --show-current` before any branch op. |
| Long-video path (chunks × groups) multiplies calls | M | Cost/latency on 90-min videos | Semaphore; outline-merged chunks ≤ 50k tokens; regression set includes a long no-caption video. |
| Frontend reducer changes break the DB-cached (non-stream) path | M | Blank tabs on cache hit | Reducer tests cover both; Playwright smoke on a cached video. |

## Success Metrics

| Metric | Baseline (phase 0 measures) | Target |
|---|---|---|
| First visible content (hero `synthesis_complete` or first `tab_ready`) | ≈ 226 s | ≤ 30 s (phase 2: ≤ 35 s) |
| All tabs final, 20-min captioned video | 171–240 s | ≤ 60–65 s |
| Golden quality score / faithfulness / duplicate rate | run twice in phase 0 | within noise, never below |
| Per-video assertions (4 new) | — | all pass every phase |
| LLM cost per benchmark video | ≈ $0.186 (standard), ≈ $0.15 vision share (HIGH) | ≤ baseline (phase 3: below) |
| 429 / fallback events per run | 0 | 0 |
| Proxy bytes per run | 143 MB | ≤ ~90 MB (one 720p) |
| Settings passthrough | 50/112 missing | every setting this task touches wired through 8 touch points |

## Required Resources and Dependencies

- Kfir: approvals at each gate; `.env.production` edits (`EXTRACTION_PARALLEL_BATCHES=6`; prod `LLM_EXTRACTION_MODEL` = `anthropic/claude-haiku-4-5-20251001` (verified on the box 2026-10-07)); GitHub secrets for the eval, prod-API mode (C18): `EVAL_API_URL`, `EVAL_USER_EMAIL`, `EVAL_USER_PASSWORD`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (+ `ANTHROPIC_API_KEY` only if the offline tier-probe A/B runs in CI) — no proxy/S3/OpenAI secrets on GitHub; eval-user `bypassCache` permission + daily quota sized for 18 × 2 runs; budget alert; tier-probe model decision after the A/B; demo library re-run go; the non-English regression video = `EqaMrzd9nZU` (picked from the dev DB 2026-10-07).
- Spend: phase 0 baseline 6 prod runs ≈ $1.1; golden ×2 ≈ 28 runs ≈ $5; each later phase ≈ 8 prod runs + 1 golden run ≈ $4. Replay harness is free.
- Prod access for the ledger/log reads: `ssh -i ~/.ssh/vie-demo-key.pem ec2-user@<vie.ad IP>`; prod code = `origin/main`.
- Langfuse: both projects; `register_prompts.py --commit` (labels `production`, leaves orphans for deleted files → delete orphans in the UI; list at each gate).

## Timeline Estimate (working sessions, gate waits excluded)

| Phase | Sessions | Notes |
|---|---|---|
| 0 | 3–4 | harness + eval are the long poles |
| 1 | 6–8 | 1b + 1c are prompt-heavy; 1d.4 vision has a measurement step |
| 2 | 2–3 | mostly frontend + SSE tests |
| 3 | 5–6 | renderer + grouping + per-group assembly |
| 4 | 1–2 | — |

Each phase: worktree → implement → suites green → replay → golden → gate report → STOP → (Kfir: review, commit word, PR, merge → deploy) → prod benchmark → gate closes.
