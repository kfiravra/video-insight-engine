# CODE MAP — eval, registry, frontend SSE reducer, API stream, prompt-registry ops, demo library, CI

Read on `docs/readme-rebuild` @ 015cf26, 2026-10-07. Every file:line below was opened. Prefixes: `W/` = `apps/web/src/features/video-output/`, `S/` = `services/summarizer/src/`.

---

## 1. EVAL (phase 0: `run_eval` sets bypassCache, schedule, gate.py, noise, golden assertions)

### 1.1 `scripts/run_eval.py` (616 lines)
| What | Where |
|---|---|
| Dataset load (`videos.yaml` → `videos[]`) | `:82-89` |
| `EvalResult` dataclass: id, domain, tab_count, expected_tab_count, tab_count_score, component_coverage, content_coverage, empty_tab_count, overall, forbidden_ok, notes | `:93-108` |
| `score_entry()` — the only scorer | `:122-194` |
| Weights: tabCount 0.15 · components 0.35 · keyContent 0.25 · empty-tabs 0.10 · forbidden 0.15 | `:177-184` |
| Expectation keys read: `expectedTabs`, `requiredComponents`, `keyContent`, `forbiddenComponents` | `:133-136` |
| Auth: register-then-login eval user; needs `EVAL_USER_PASSWORD` (no committed default) | `:197-262` |
| `run_pipeline(api_url, url, token, bypass_cache=False)` — POST `/api/videos` with `{"url", "bypassCache": bypass_cache}` | `:265-279` |
| **Consumes the SSE stream** (`GET /api/videos/{videoSummaryId}/stream`) until a terminal event (`done/complete/error/stream_error/cached`), 30-min cap | `:293-313` |
| Then polls `GET /api/videos/{userVideoId}` ×24 (5 s) for `status == completed` → returns `{meta, tabs}` | `:315-340` |
| **bypassCache is only `True` on the `--stability` re-run** — the main loop calls `run_pipeline(..., token)` with the default `False` (`:481`), so any already-processed golden video serves from cache and the eval scores the *stored* run, not the current code | `:481`, `:538` |
| `--stability N` layout probe: Jaccard of tab-component multisets, reported not gated | `:527-569` |
| Reports `reports/eval-{ts}.csv|.md` | `:366-412` |
| `post_to_langfuse(results, run_name)` → `create_dataset_run_item` per id (dataset `vie-golden-v1`) | `:416-438` |
| `--fail-under` exit-1 gate on **average overall only** | `:579-582` |
| CLI flags: `--api-url --output --dry-run --filter --limit --fail-under --stability --dataset-name --publish-run` | `:586-612` |

**Metrics that do NOT exist in run_eval today:** faithfulness, duplicate-item rate, per-video assertions beyond the four keys (no `expectedFormat`, no `minItems`, no quiz absent-or-last, no timestamp-beyond-duration). `format` is in the YAML (`videos.yaml:13-17`) but **never scored**. There is **no `gate.py`** and no noise file anywhere under `scripts/` or `dev/golden-dataset/`.

- Faithfulness: computed in `S/services/pipeline/faithfulness.py` (score dataclass `:85-89`, pass ≥ 0.7) and sunk **only** via `log_score(...)` to the Langfuse trace (`:512`); not on the Mongo doc. Sample rate `LANGFUSE_FAITHFULNESS_SAMPLE_RATE: float = 0.2` (`S/config.py:184`) — a golden gate on faithfulness needs sample rate 1.0 for eval runs and a Mongo/Langfuse read-back.
- "Duplicates": admin `GET /usage/duplicates` (`services/admin/src/routes/usage.py:591-612`) groups `llm_usage` by `prompt_hash` — duplicate *prompts*, not duplicate *items*. Item dedup lives in `S/services/pipeline/extraction_merger.py:82` (`_deduplicate_all_lists`). A duplicate-item-rate metric must be new (count before/after dedup, or a scorer over assembled tabs).
- Other eval helpers: `scripts/baseline_golden_dataset.py` (regenerates videos.yaml from current output — snapshot workflow, run inside the summarizer container), `scripts/build_golden_dataset.py` (uploads YAML to Langfuse dataset `vie-golden-v1`), `scripts/_bench_quality_scorers.py` (fast-model bench scorers; `score_classifier` at `:17` — reusable for the tier-probe A/B agreement metric).

### 1.2 `dev/golden-dataset/videos.yaml` (289 lines)
- **24 entries, 10 `disabled: true`** (dead links verified 2026-07-14) → **14 live**. Per-entry fields: `id, url, domain, format, language, expectedTabs, requiredComponents, forbiddenComponents?, keyContent, disabled?` (`:9-24`).
- Live ids: gaming-op17-unboxing, gaming-op13-box-opening, gaming-op17-set-verdict, tech-agentic-engineering, tech-react-hooks, tech-docker-basics, food-knife-skills, learning-photosynthesis, learning-double-slit, science-crispr-intro, science-black-holes, travel-vietnam-10day, fitness-pushup-form, review-airpods-pro.
- Disabled (need replacements): food-sourdough-bread, food-ramen-broth, language-spanish-basics, language-japanese-particles, travel-tokyo-3day, travel-iceland-ring-road, fitness-mobility-routine, review-iphone-16, music-bohemian-rhapsody-analysis, narrative-mt-everest-1996. → **no live language, music or narrative entry.**
- Quiz coverage today: `forbiddenComponents: [quiz_arena]` on gaming ×3 and food (`:48,:58,:68,:107`); `requiredComponents: [overview, quiz_arena]` on learning (`:139,:148` — the second also requires `flash_deck`, which phase 1d removes from enrichment → **expectation must change when flashcards go**).
- The brief's four new golden videos (food-travel vlog, long-intro recipe, uC45_4nnEAI, Jru5B044HOs) are absent; uC45_4nnEAI/T1dQhQAm8Tc/Jru5B044HOs appear nowhere in the repo.
- `videos.yaml` is gitignored except itself and `baseline.json` (`.gitignore:7-9`).

### 1.3 `dev/golden-dataset/baseline.json`
Keys: `retrievalEval` (recall@3 1.0, MRR 0.865, floors 0.85/0.7, 2026-07-12), `faithfulnessJudge` (accuracy 1.0 over 20 labelled claims, floor 0.8), `goldenEval` (`averageOverall: 0.824`, `liveEntries: 14`, `failUnder: 0.75`, `layoutStability {avg 0.65, n 2, gated false}`, `report: reports/eval-20260811-142602.md` — **that report is gitignored (`reports/`) and absent locally**). The `produce` recipe (`baseline.json` goldenEval.produce) = compose up → `run_eval.py --publish-run golden-baseline-$(date)` → hand-copy average.

### 1.4 `.github/workflows/eval.yml` (203 lines)
- Triggers: PR to main on paths `services/summarizer/src/prompts/**`, `.../services/pipeline/**`, `.../services/vector/**`, `tests/eval/**`, assistant rag files, `scripts/run_eval.py`, `dev/golden-dataset/**`, the workflow itself (`:21-37`); **schedule `0 3 * * 1` (weekly Monday)** (`:38-40`); `workflow_dispatch` (`:41`).
- Jobs: `golden-eval-dry-run` (PR, zero spend, `--dry-run --fail-under $FLOOR` with FLOOR from baseline.json; uploads `reports/eval-*.md` 14 d) `:48-75`; `retrieval-eval` (ephemeral Qdrant) `:77-127`; `golden-eval-live` (schedule/dispatch; `timeout-minutes: 350`; boots the whole stack via `docker compose up -d --build` on the runner with `.env.example` + 3 keys; runs `run_eval.py --fail-under $FLOOR --publish-run weekly-$(date)`; uploads `reports/eval-*` 90 d; `docker compose down -v`) `:129-203`.
- **Secrets referenced:** `ANTHROPIC_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `EVAL_USER_PASSWORD` (`:136-139`). The live job skips cleanly when `ANTHROPIC_API_KEY`/`EVAL_USER_PASSWORD` are empty (`:145-153`).
- **Gaps vs brief:** the runner's compose env has no `YOUTUBE_PROXY_URL` (YouTube blocks datacenter IPs — see memory `project_youtube_block_aws_20261004`; a GitHub runner is one), no `OPENAI_API_KEY`/`GEMINI_API_KEY` (fast tier / transcript fallback), no S3 creds → frames branch will degrade. Secrets Kfir would need to add for a real live run: `YOUTUBE_PROXY_URL`, `OPENAI_API_KEY` (if the fast tier/tier-probe lands on gpt-4o-mini), `GEMINI_API_KEY` (no-caption regression video), `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`S3_BUCKET` (or the eval runs with `FRAME_VISION_ENABLED=false` and loses the uC45_4nnEAI moment-frame assertion). Names only — verify against `.env.example` before listing to Kfir.
- Changing cadence to every two weeks: cron can't express "every 14 days"; use `0 3 1,15 * *` or a `schedule` + day-of-year parity guard step.
- Noise: nothing measures it today. Smallest build = run `golden-eval-live` twice (`--stability` already re-runs with bypassCache) and persist per-metric |Δ| as `dev/golden-dataset/noise.json`; `gate.py` compares a new report's per-metric means against baseline ± noise.

---

## 2. REGISTRY (`domains.json`, `@vie/shared`, toolkit datasources, Pydantic models, single-tag validator)

### 2.1 Location, build, staleness
- Source: `packages/shared/src/config/domains.json` (23,159 B, mtime 2026-08-16). Loader `packages/shared/src/config/index.ts:8` (`import domainsJson from './domains.json' with { type: 'json' }`); `tsconfig.json` has `resolveJsonModule: true`, `outDir: dist`, `include: ["src"]`; `package.json` `build: rm -rf dist && tsc`, `main/exports → dist/index.js`.
- **`packages/shared/dist/domains.json` is stale** (21,936 B, mtime 2026-07-13, md5 differs): missing top-level `playbooks`, `playbooksNote`, `visualCriticality`, `visualCriticalityNote`; `domainRequirements`, `domainRequirementsNote`, `enrichment` differ. Impact: **local** TS consumers only (`apps/web` and `packages/types` import `@vie/shared/config` via workspace → dist; vite has no src alias, `apps/web/vite.config.ts:35-37` only aliases `@`). Docker images and CI rebuild it (`apps/web/Dockerfile:34`, `api/Dockerfile:19`, `ci.yml:32,115,159` `pnpm --filter @vie/types --filter @vie/shared build`), and **Python reads the src JSON directly** (compose bind-mounts `./packages/shared/src/config/domains.json → /app/shared/domains.json` at `docker-compose.yml:375,426`, `docker-compose.prod.yml:360,396`; loader `S/shared_config/domain_config.py:17-37`, `_DOCKER_PATH` then `_LOCAL_PATH`). So prod summarizer is correct; the stale dist only breaks local web typecheck/tests that read `rawConfig`.
- TS consumers: `apps/web/.../output-type-config.ts:5`, `VideoGrid.tsx:6`, `VideoCard.tsx:9`, `TabLayout.tsx:4`, `OutputRouter.tsx:4`, tests `tab-prop-schemas.test.ts:2`, `composable-output-parity.test.tsx:2` (`rawConfig`, `SECONDARY_COMPONENTS`); `packages/types/src/output-types.ts:6`, `vie-response.ts:10`. `ContentTag`/`Modifier` unions are hand-typed at `index.ts:15-33`.
- Python accessors (`domain_config.py`): `get_config:40`, `valid_content_tags:50`, `valid_modifiers:55`, `valid_components:60`, `component_tiers:65`, `secondary/primary/ordered_components:75-85`, `render_valid_component_names:90`, `density_gates:97`, `assembler_item_caps:108`, `domain_requirements:115`, `get_playbook:123`, **`effective_requirements(domain, content_format):135`**, `visual_criticality_config:156`, `render_density_gate_table:161`, `map_category_to_tag:177`, `get_default_tab_ids:188`, `get_tab_meta:195`, `get_enrichment_map:205`, `build_fallback_tabs:213`. New registry maps get sibling accessors here.

### 2.2 Top-level keys today (15)
`components` (22 names), `componentTiers`, `densityGates` (per component `{min, max, chars}` as **strings**, e.g. step_player `{"min":"3","max":"10","chars":"instruction ≤ 200"}`), `assemblerItemCapsNote`, `assemblerItemCaps` (comparison 10, moment_track 20, info_grid 20, flash_deck 15, spot_explorer 25, step_player 25, checklist 30, claims_tracker 20, tier_list 30, formation_diagram 23), `domainRequirementsNote`, `domainRequirements` (14 domains; shape `{required: [..], max: {comp: n}, forbidden?: [..]}` — food `{required:[step_player, checklist], max:{flash_deck:1}, forbidden:[quiz_arena]}`, tech `{required:[code_playground], max:{flash_deck:1, quiz_arena:1}}`), `playbooksNote`, `playbooks` (`gaming:unboxing`, `review:unboxing`; shape `{required, preferred, forbidden, planGuidance}`), `visualCriticalityNote`, `visualCriticality` (`tiers {high {overselect 40, visionMax 40, keep 25}, standard {visionMax 8}, low {visionMax 0}}, highDomains [travel, food, fitness, project, sport], lowDomains [podcast, news], highTitleKeywords [...]`), `domains` (14; each `{emoji, label, gradient, defaultTabs[{id, component, dataSource, label, emoji, goal}]}`), `modifiers` (`narrative`, `finance`: `{emoji, label}` only), **`enrichment`** (domain → enrich prompt path: default/learning `enrich_study.txt`, tech, fitness, food, music, travel, review, project, language, science, podcast/gaming → `enrich_recall.txt`; no entries for news, sport), `categoryMap` (YouTube category → tag).
- **Conflict with brief Appendix A:** the brief adds a top-level `"enrichment": { "quizDomains", "flavor" }` — **that key already exists** with the prompt-path map (consumed by `get_enrichment_map():205` and the ENRICHMENT_MAP in enrichment.py). Either (a) replace its contents in phase 1d when the 11 enrich files die (same phase, consistent with "removal happens in the phase that replaces the thing"), or (b) name the new map `quizEnrichment`. A6 says no new keys inside existing entries; this is a whole-key replacement, so (a) is defensible but must happen in 1d, not phase 0. Phase 0 can only add `dataSources`, `demoteTo`, `requirementEvidence`, `quizPolicy`, `grouping`.
- Brief's "also fix": confirmed — `domains.tech.defaultTabs[1].dataSource == "tech.setup"` (toolkit lists `tech.setup.commands`); `domains.language.defaultTabs[3]` = `{id: vocabulary, component: concept_canvas, dataSource: language.vocabulary}` → brief wants `flash_deck`.

### 2.3 `component_toolkit.txt` `<valid_datasources>` (`S/prompts/component_toolkit.txt:246-265`)
**53 paths** (50 domain + 3 enrichment):
tech: snippets, patterns, cheatSheet, **setup.commands**, topics · learning: keyPoints, concepts, takeaways, timestamps · project: steps, materials, tools, safetyWarnings · food: ingredients, steps, tips, equipment, substitutions · travel: itinerary, budget, packingList · review: pros, cons, specs, comparisons, verdict · fitness: exercises, warmup, cooldown, timer · music: analysis, structure, lyrics, credits · language: phrases, rules, drills, vocabulary · science: concepts, keyFacts, experiments · podcast: segments, guests, quotes, topics · news: storyTimeline, entities, claims, context · gaming: highlights, loadout, walkthrough, rankings · sport: matchEvents, formation, statComparison · narrative: keyMoments, quotes, takeaways · enrichment: quiz, flashcards, scenarios.
- Cross-check against schema skeleton top-level keys (`S/prompts/schemas/*.txt`, 2-space-indented `"key":` lines): **all 50 domain paths resolve** (`tech.setup.commands` → `tech.setup` object). Scalars/non-list keys per schema that are *not* datasources (the "derived scalars" in Appendix A): finance `costs, savingTips`; fitness `meta, tips`; food `meta, nutrition`; gaming `title`; language `targetLanguage, nativeLanguage, level`; learning `keyQuestion, summary`; music `title, artist, genre, themes`; news `headline`; podcast `show, host`; project `projectName, difficulty, estimatedTime, estimatedCost`; review `product, price, rating`; science `field, level`; sport `title`; tech `languages, frameworks`; travel `accommodationTips, transportationTips, bestSeason`. Note `learning.keyPoints`, `science.keyFacts`, `travel.packingList`, `news.storyTimeline`, `tech.cheatSheet` are datasources that the registry must carry caps for.
- Quiz/scenario text to delete in 1d: `:164` ("quiz_arena ⭐ NEW (absorbs scenario)"), `:165-166`, `:170` (`DATASOURCE: enrichment.quiz, enrichment.scenarios.`), `:262` (enrichment line).
- Datasource list is a **hardcoded prompt block**; rendering it from the registry means a new `{valid_datasources}` placeholder in `component_toolkit.txt` + a renderer in `domain_config.py` (pattern: `render_valid_component_names:90`, `render_density_gate_table:161`).

### 2.4 Pydantic models (`S/models/domain_types.py`)
Per-domain `*Data` roots: `TravelData:125`, `FoodData:268`, `LearningData:334`, `ReviewData:409`, `TechData:467`, `FitnessData:598`, `MusicData:652`, `ProjectData:704`, `NarrativeData:752`, `FinanceData:774`, `LanguageData:830`, `ScienceData:892`, `PodcastData:941`, `NewsData:989`, `GamingData:1036`, `SportData:1083`. Item models (for phase-3 per-group validation "without materializing defaults"): e.g. `FoodIngredient:186`, `FoodStep:209`, `TechSnippet:444`, `TechSetup:436`, `LearningConcept:308`, `GamingHighlight:1004`, etc. Maps: `DOMAIN_MODELS:1169`, `MODIFIER_MODELS:1186`.

**Single-tag validator bug (A25) — confirmed at `domain_types.py:1206-1215`:** `validate_domain_output()` with `len(content_tags) == 1` calls `model_cls.model_validate(data)` on the **raw top-level dict**. If the LLM wrapped the output (`{"tech": {...}}` or `{"techData": {...}}`), every TechData field defaults to empty and validation *passes* → empty extraction stored. The multi-tag branch (`:1218-1221`) correctly unwraps `data.get(f"{tag}Data") or data.get(tag)` first. Fix = apply the same unwrap in the single-tag branch (and keep the flat fallback). Call sites: `S/services/pipeline/extractor.py:352, :397, :683` (grep only — pipeline dir owned by the other fork).

---

## 3. FRONTEND SSE REDUCER (`apps/web`) — phase 2 defects, with lines

Reducer = `W/lib/streaming/stream-event-processor.ts`; validators = `W/lib/streaming/sse-validators.ts`; display resolution = `W/lib/streaming/resolve-display-tabs.ts`; hook = `W/hooks/use-summary-stream.ts`; page = `apps/web/src/pages/VideoDetailPage.tsx`; router = `W/components/OutputRouter.tsx`; tab focus = `W/components/output/TabCoordinationContext.tsx`.

| Brief rule | Today | Where |
|---|---|---|
| `meta` must not wipe tabs | `handleMetaEvent` does `setState(prev => ({...prev, meta, tabs: [], tabCount, tabLabels}))` — **wipes tabs** (comment: "Keep the tabs reset — it protects reconnects and cached replay") | `stream-event-processor.ts:203-226` (reset at `:225`) |
| `position` is a sort key, not a splice index | `tabs.splice(position ?? tabs.length, 0, tab)` — **splice index**, clamped to length; replace-by-id (`existingIdx >= 0`) **ignores the new position** | `:265-281` (splice `:279`, replace `:273-277`) |
| replace-by-id honours `final` | no `final` field read anywhere; `TabEntry` has none | `:238-282`; `packages/types/src/api.ts:116-128` (`SSETabReadyEvent` has `position?` only, no `final`/`provisional`) |
| `initialTab` sticky once chosen | `initialTab = tabDefs[0]?.id` recomputed every render (`OutputRouter.tsx:165`); `TabCoordinationProvider` re-applies whenever `initialTab` **changes** (`TabCoordinationContext.tsx:62-68`: `if (initialTab && appliedInitialRef.current !== initialTab) setActiveTabRaw(initialTab)`) → a tab spliced at position 0 later (e.g. overview) **steals focus** | `OutputRouter.tsx:165,186`; `TabCoordinationContext.tsx:50,62-68` |
| a late overview cannot steal focus | same mechanism as above; `TabLayout.tsx:30-37` also falls back to first tab when active id vanishes | `TabLayout.tsx:30-37` |
| DB-vs-stream by `final`/version, not tab count | `resolveDisplayTabs`: DB wins when `status === completed && dbTabs.length >= streamTabs.length`, else stream if non-empty — **count comparison** | `resolve-display-tabs.ts:23-26`; caller `VideoDetailPage.tsx:185-201` |
| timeline / pending pills / Cancel visible while tabs exist | `OutputRouter.tsx:185-245`: `hasData && tabDefs.length > 0` → `CommandDeck + TabPanel` branch (no `StreamingPlaceholder`, no `onCancel`); else branch renders `VideoHero(pendingTabs=tabLabels)` + `StreamingPlaceholder(onCancel=onCancelStream)` → **placeholder, pending pills and Cancel disappear at the first tab** | `OutputRouter.tsx:185-243`; `StreamingPlaceholder.tsx:123-237` (Cancel reveal `CANCEL_REVEAL_MS`, `:127-132`) |
| provisional tab state | nothing; `TabEntry` has no state flag | `packages/types` `TabEntry` (vie-response.ts) |
| `synthesis_complete` merge not replace | `handleSynthesisComplete` **replaces** `synthesis` with `{tldr, keyTakeaways, masterSummary: "" if absent, seoDescription: ""}` → an early `{tldr, keyTakeaways}` is fine (first write), but the **final** re-emit must carry all four or it blanks nothing — and conversely an early event with empty strings is harmless. Verify: the brief's "reducer merges, doesn't replace" is **false today**; make it `{...prev.synthesis, ...nonEmptyFields}` | `stream-event-processor.ts:172-180`; validator `sse-validators.ts:134-153` (`tldr`, `keyTakeaways` required by zod literal schema at `:134`) |
| `complete` carries assembled tabCount | yes (`:284-297`) | — |
| API relay persists synthesis | **only when `event.masterSummary` is truthy** (`api/src/routes/stream.routes.ts:156-166`) → the early memory-only `synthesis_complete` won't be persisted (fine: the final one is); metadata persisted at `:129-150` | `stream.routes.ts:100-178` |

- Phase lists: `VALID_SSE_PHASES` (`sse-validators.ts:229-241`: metadata, metadata_fallback, transcript, transcript_cached, audio_transcription, whisper_transcription, triage, extraction, enrichment, synthesis, translation) and `SSE_PHASE_MAP` (`stream-event-processor.ts:59-71`) — any new backend `phase` value (e.g. `plan`, `memory`, `vision`, `reconcile`) must be added to **both** or `validatePhaseEvent` drops it and the UI phase freezes.
- Event dispatch table: `stream-event-processor.ts:337-347` (`triage_complete, extraction_complete, enrichment_complete, synthesis_complete, tab_ready, complete, …`).
- Summarizer-side event models: `S/models/sse_events.py` — `SynthesisCompleteEvent:79-83` (all fields default ""/[]), `MetaEvent:91-96`, `TabReadyEvent:99-106` (`position: int | None`, no `final`), `CompleteEvent:109-112`. Adding `final`/`provisional` = edit `TabReadyEvent` + `packages/types/src/api.ts:116-128` + `TabEntry`.
- Cancel: `use-summary-stream.ts:229-233` (`abortStream(videoSummaryId)` → phase `cancelled`).
- Existing tests: `W/lib/streaming/__tests__/{stream-event-processor-v2.test.ts, resolve-display-tabs.test.ts, sse-validators.test.ts, should-open-stream.test.ts}`; `W/hooks/__tests__/{use-summary-stream.test.ts, use-focus-band.test.tsx, use-celebration-trigger.test.ts}`. Run: `cd apps/web && npm test` (vitest). Typecheck web with `pnpm --filter web exec tsc -b` (root `tsc --noEmit` is a no-op, `ci.yml:40-43`).

---

## 4. API + Redis event stream

- Relay route: `api/src/routes/stream.routes.ts:29-185` — `GET /api/videos/:videoSummaryId/stream` proxies `${SUMMARIZER_URL}/summarize/stream/{id}` (`:50`), line-parses `data:` frames, persists `metadata` (`:129-150`, + WS broadcast) and `synthesis_complete` when `masterSummary` present (`:156-166`), forwards raw chunks (`:177`). **No Redis on the API side** (no xread anywhere in `api/src`); the summarizer's `routes/stream.py` drains Redis for the HTTP SSE.
- Broker: `S/services/cache/pipeline_event_stream.py` — keys `vie:pipeline:events:{id}` (`:109-110`), `vie:pipeline:lock:{id}` (`:113-114`); `publish()` = `XADD … MAXLEN ~ PIPELINE_STREAM_MAXLEN` (`:187-195`); `mark_done()` appends `SENTINEL_DONE` **then** `EXPIRE PIPELINE_STREAM_TTL_SECONDS` (`:197-213`) → **TTL is only set at DONE**: a run that dies before `mark_done` leaves a no-TTL stream (A12 leak). `purge(id)` deletes stream+lock (`:127-138`); lock TTL `PIPELINE_LOCK_TTL_SECONDS = 600` (`S/config.py:234`), `PIPELINE_STREAM_TTL_SECONDS = 120` (`:245`), `PIPELINE_STREAM_MAXLEN = 2000` (`:246`). Phase-2 fix: set EXPIRE on first `publish` (or on lock acquire) and `purge` at run start ("stream key reset per run"), plus a one-off sweep of `vie:pipeline:events:*` without TTL.

---

## 5. PROMPT REGISTRY OPS

- Loader: `S/services/pipeline/prompt_builder.py:66` `load_prompt_text(path)` — **registry-first, file fallback**, for any path under `PROMPTS_DIR` (`:15`); names derived from the relative path (`:23-39`). Fetch: `S/services/observability/langfuse_client.py:368-383` `fetch_prompt_with_obj(name)` → `client.get_prompt(name)` (SDK default label = `production`). **There is no disk-vs-registry switch** (no `settings.*`/env read in `prompt_builder.py`; `S/config.py:180-192` has only keys/base URL/sample rate/user-id mode). The brief's "point dev at disk (add a dev-only switch if none)" → add e.g. `LANGFUSE_PROMPTS_ENABLED: bool = True` in `config.py` and short-circuit `load_prompt_text` when false; wire through the touch points.
- Uploader: `scripts/register_prompts.py` — names `summarizer:base_extraction`, `summarizer:schema:food`, `summarizer:enrich:enrich_study`, `assistant:rag_system` (`:3-9`); `--dry-run` default, `--commit` uploads (`:17-24`); label `production` (`:198-224`); skips when keys blank (`:246-250`); secret-pattern/size guard (`:77`). Walks `prompts/**` so **new files (`tier_probe.txt`, `memory.txt`, `enrich_quiz.txt`) register automatically; renamed/deleted files leave orphans in Langfuse** (`summarizer:classify`, `summarizer:enrich:*` ×11) that keep the label — harmless (nothing fetches them) but need UI cleanup if tidiness matters. Nothing else needs the UI; runtime code reads by name.
- Boot sync: `vie-langfuse-init` one-shot service in both compose files (`docker-compose.yml:329-345`, `docker-compose.prod.yml:329-345`; `restart: "no"`, runs `register_prompts.py --commit` with the repo `scripts/` + `prompts/` bind-mounted) → **prod re-registers on every `docker compose up`** that includes the service; manual: `docker compose run --rm vie-langfuse-init` (`docs/OBSERVABILITY.md:84`). Precedence doc: `docs/OBSERVABILITY.md:20, 62-90` ("registry-first with file fallback", `promptVersions` on the trace `:59,:74`). "Both projects" = dev vs prod Langfuse projects = the two key pairs; the script takes keys from env, so run it once per project.

---

## 6. DEMO LIBRARY / re-run tooling

- **No library re-run script exists.** Closest: `scripts/demo-export.sh` (selects `videoSummaryCache` docs with `{"status":"completed","pipelineVersion":$PIPELINE_VERSION}` at `:40`, writes `urls.txt` one YouTube URL + title per line at `:65`) and `scripts/demo-import.sh` (restores an export; refuses on version mismatch with `packages/shared/src/config/pipeline-version.json`). `scripts/wipe-data.sh` wipes S3 `transcript.json`, Mongo video collections, Redis FLUSHALL (dry-run without `--yes`). `scripts/test-pipeline-live.sh <youtube_id>` drives one video through the summarizer directly via mongosh + curl (bypasses vie-api auth).
- Phase-3 "demo library re-run script" = new: read the current library (demo-export's query at the **old** version, or `urls.txt`), loop `run_eval.run_pipeline(api, url, token, bypass_cache=True)` sequentially with a politeness gap; reuse `run_eval.authenticate()`. Since a `PIPELINE_VERSION` bump changes `dedupKey`, old owners regen only on resubmit — the re-run is the resubmit.

---

## 7. CI / deploy

- `ci.yml`: `ts-quality` (`:14-44`: `pnpm --filter @vie/types --filter @vie/shared build` → API `tsc --noEmit` → web `tsc -b`), `python-lint:56`, `api:74`, `web:137`, **`summarizer:177-211`** (Python 3.11, `requirements.lock` + llm-common, spaCy model, `pyright@1.1.407 --project .` (basic), `pytest --cov=src`), `assistant:213`, `admin-backend:245`, `admin-ui:264`, `llm-common:285`, `docker-build:304`. A registry `tsc` test for `@vie/shared` = add a test under `packages/shared` or assert in the summarizer pytest (JSON-level); `ts-quality` already builds `@vie/shared`.
- `deploy.yml`: `workflow_run` of **CI** completed on `main` (`:11-16`), gated `event == 'push' && conclusion == 'success' && head_branch == 'main' && same repo` (`:33-38`) → "Deploy to EC2" via OIDC. So **merge to main → CI green → deploy** is automatic; prod benchmarks can run right after the deploy job finishes. (`eval.yml` live job is independent of deploy.)
- `.gitignore:78` already ignores `.claude/hooks/.git-safety-override` (file currently absent); `.gitignore:4-9,15,85`: `dev/research/*`, `dev/completed/*`, `dev/golden-dataset/*` (except baseline.json, videos.yaml), `reports/`, `dev/diagnostics/` — `dev/active/` is **not** ignored at the root level (check before committing evidence files; memory says prior commits excluded dev/active by hand).

---

## 8. Open questions / brief-vs-code mismatches (this map's scope)

1. `run_eval` main loop never bypasses cache (`:481`) — the weekly "live" eval has been scoring cached runs since whenever each golden video was first processed; the 0.824 baseline is partly historical output. Phase-0 fix is one arg; expect the **first bypassing run to shift the baseline** independently of any pipeline change.
2. No `gate.py`, no noise file, no faithfulness/duplicate metrics in the eval; faithfulness is Langfuse-only at 20 % sampling.
3. `enrichment` top-level key already exists in `domains.json` with a different shape (prompt-path map) — brief's Appendix A collides; decide replace-in-1d vs rename.
4. Golden set has no live language/music/narrative video; learning-double-slit requires `flash_deck` (dies in 1d).
5. The eval runner cannot reach YouTube from GitHub without `YOUTUBE_PROXY_URL`; the live job as written will likely fail on metadata for every entry.
6. `synthesis_complete` reducer replaces, not merges; `meta` wipes tabs; `position` is a splice index; `initialTab` is re-applied on change; DB-vs-stream is a count comparison; placeholder/Cancel vanish at first tab — all confirmed with lines above (brief's A8/A12 claims hold).
7. Stream TTL only set at `mark_done` — leak path confirmed.
8. No disk-vs-registry prompt switch exists; must be added.
9. `packages/shared/dist` stale locally only; Docker/CI rebuild; Python reads src.
