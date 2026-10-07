# /task pipeline-1min — brief (verbatim, Kfir, 2026-10-07)

Owner: Kfir. Executor: CC. Evidence base: your 27 answers (A1–A27, 2026-10-06/07), the
prompt files, `domains.json`, and the two perf reports. Where this brief and the code
disagree, say so in the gate report instead of guessing.

## 0. Goal and principles

A 20-minute captioned video on prod: first visible content ≤ 30 s, every tab ≤ 60–65 s
(today 171–240 s), equal or better output quality, equal or lower cost.

Benchmark set (prod, median of 2 runs, measured with `pipeline.timing`):
T1dQhQAm8Tc (tech, standard tier, 240 s today), one of the five HIGH-tier cooking runs
(you pick; it stays fixed for the whole task), uC45_4nnEAI (static camera, 171 s).
Regression set, run once per phase: one no-caption video, one non-English video.

Principles:
1. **Every reader gets the full transcript; only the writers get split.** Reading is cheap
   and fast (prefill); output tokens are the clock (~45 tok/s Sonnet, ~100 tok/s Haiku).
2. **Output contract unchanged.** Extraction stays domain-keyed JSON validated by the same
   Pydantic models; `tabs`/`meta` shape unchanged; assemblers unchanged except where
   listed. L4 (new contract) is out of scope.
3. **The plan has the final say** on domain, format, tabs. It reads everything.
4. **Nothing is invented to fill a schema.** Floors come from what the plan counted; an
   empty array is a correct answer.
5. Vision stays on Sonnet. More than one video at a time is not a requirement. No instance
   change (A15).
6. Reuse, don't rewrite: `run_parallel_phases`, the `{video_context}` /
   `batch_partial_context` slots, `extraction_merger`, the assembly cascade +
   promote/demote ladder, `droppedTabs`, `effective_requirements()`.

## 1. Rules for the whole task

- One worktree/branch per phase. Nothing is committed, pushed or PR'd without Kfir's
  explicit word. Never switch branches in the shared tree.
- Each phase ends with a **gate report** (≤ 1 page): timing table, golden diff vs
  baseline, cost per video, env/flag changes for Kfir, open questions. Then stop and wait.
- Each phase deploys to prod after merge (CI → deploy). Prod benchmarks are measured
  after that deploy.
- The Langfuse prompt registry wins over disk in both envs (A5). Every prompt you change
  is re-registered in both projects at the end of the phase; while iterating, point dev at
  disk (if no such switch exists, add a dev-only one). List anything that needs the UI.
- Flags: exactly two new ones, `EXTRACTION_PARALLEL` and `FRAME_VISION_PARALLEL`
  (booleans, default true). Every flag/env you touch goes through all 8 touch points
  (A16). Fix passthrough for the existing settings this task depends on
  (`FRAME_VISION_ENABLED`, `FRAME_TIER_ENABLED`, `EXTRACTION_PARALLEL_BATCHES`, chunking
  knobs). Dead settings are deleted, not left in place.
- Env changes: name + value for Kfir; he edits `.env.production` and copies it to the
  box. No secrets in any output, ever.
- Spend: orchestration work runs on the replay harness (phase 0). Before each prod
  benchmark/eval run, state the number of runs and the estimated cost.
- Removal happens in the phase that replaces the thing, with its tests; phase 4 is only
  the sweep for leftovers.
- Delete and gitignore `.claude/hooks/.git-safety-override` before any commit.

## 2. Target architecture (what v9 looks like)

```
t=0   metadata (one yt-dlp info call) ∥ low-res download → scene detect + score
      ∥ 720p download (kept: hi-res frames + moment fill) ∥ description analysis
~10s  transcript ready → [m:ss] markers rendered at prompt-build time
~10s  tier probe (fast model, 1 s): domain, format, has_visual_demo, confidence
~12s  PLAN (Sonnet, full transcript)   ∥   MEMORY (Haiku, full transcript)
      → tabs + brief per tab + DNA +        → outline w/ boundaries, evidence,
        evidence + canonical terms            tldr + takeaways  → hero at ~24 s
~25s  visual tier resolved from the probe → hi-res from the kept 720p → vision in
      8-frame parallel batches (Sonnet) → annotations block at ~45 s
~36s  plan-done → RECONCILE (code, ms) → field groups from the registry
      text groups fire (staggered 1–2 s, one cached block) → ~52 s
      visual group fires at frames-done → ~62 s
      each group assembles its own tabs → tab_ready as they land
      quiz (only if a quiz tab survived) after its owning group → last tab
      synthesis (masterSummary + seo) ∥ moment fill ∥ faithfulness → done ≈ 62 s
      persist (Mongo / Redis / Qdrant / S3) in background
```

Who sees what in v9: tier probe = clean transcript windows; plan = everything; memory =
everything; groups = everything + video_memory + their schema slice; visual group = the
same + annotations; enrichment = owning group's output + video_memory; synthesis =
video_memory + final extraction + tab labels.

## 3. Phases

### Phase 0 — measure and foundations (one PR, no user-visible change)

**Add**
- `pipeline.timing` on the Mongo doc (A14): start/end per phase; every LLM call
  (feature, model, in/out tokens, cache read/write tokens, wall, retries, fallback used,
  429 count); every download (which, bytes, wall); first `tab_ready`; `complete`;
  `done`. Langfuse spans with `end_time` for non-LLM phases; the description call joins
  the trace. Fix the DONE log line: `tabs=` logs the planned count (A23) → log
  planned/assembled/emitted.
- Replay harness (A13 smallest build): fake at the `acompletion` seam keyed by
  `llm_feature_var` (+ ordinal) replaying recorded outputs with recorded `latencyMs`;
  stubs for yt-dlp/transcript, frames + S3, Whisper, embeddings/Qdrant, description, with
  configurable sleeps; driver over `stream_summarization`; cassettes from the
  Langfuse + Mongo dumps for the three benchmark videos; `scripts/replay.py --video <id>
  [--speed 0]`. Test fixture, not product code. Must reproduce the 240 s run within 10%.
- Registry in `domains.json` as NEW top-level maps only (A6: no new keys inside existing
  entries; rebuild `packages/shared/dist`, stale since July). Shape in Appendix A.
- Plan-time dataSource validation against the registry (`plan.py:181` copies it
  unvalidated — 3 of 7 retries, A25). Sibling with the same component, else drop the tab.
- Fix the single-tag validator bug (A25, OuNKBjuV7A4: whole wrapped response validated
  against TechData).
- Eval: `run_eval` sets `bypassCache`; schedule every two weeks; list the GitHub secrets
  for Kfir; add 4 golden videos with per-video assertions (expected format, forbidden
  components, quiz absent-or-last, min items, no timestamp beyond duration): a
  food-travel vlog → no recipe components; a recipe with a long story intro → recipe
  components; uC45_4nnEAI → moment frames without scene frames; Jru5B044HOs (do-along)
  → no quiz. Keep quality score, faithfulness, duplicate rate. Run the live set twice →
  per-metric noise. `gate.py` exits non-zero when a primary metric drops more than its
  noise. Baseline stored as CI artifact + Langfuse dataset run.
- Tier-probe A/B, offline, no pipeline spend: gpt-4o-mini vs Haiku 4.5, temperature 0,
  the new input (Appendix B.1), over the golden set; report agreement with expected
  domain/format and `has_visual_demo`. Recommend one.
- Baseline on prod: 3 benchmark videos × 2 runs → phase walls, first tab, total, cost.

**Tests**
- Registry: every `valid_datasources` path resolves to a schema field; every
  `defaultTabs.dataSource` exists (catches `tech.setup` vs `tech.setup.commands`); every
  component named in `domainRequirements`/`playbooks`/`demoteTo` exists; evidence keys ∈
  the vocabulary; caps are ints; `tsc` passes on `@vie/shared`.
- Harness: replay of T1dQhQAm8Tc reproduces phase walls within 10%; zero external calls
  (assert no network).
- Timing: record present and complete on a replay run; schema test.
- Eval: `gate.py` unit test with a synthetic drop; noise file produced.

**Gate 0**: harness reproduces 240 s; baseline + noise recorded; registry tests green;
A/B recommendation given.

### Phase 1 — orchestration + prompts (same call shape, PIPELINE_VERSION unchanged)

Goal: remove the serial waits and fix what every call is told and sees. Extraction is
still one call per (batch) with the planned domains' schemas, so the golden gate measures
the prompt effect before the split. Outputs change for new runs; cached v8 results keep
being served; the version bump happens once, at phase 3.

**1a — inputs and media**
- Remove: the second and third proxied downloads; the 720p prefetch cancellation on the
  zero-frame early return (A23); the stream-URL pass (already skipped under proxy).
- Add: at t=0 metadata ∥ low-res download ∥ 720p download ∥ description analysis
  (A9 — third member of the parallel group; `validate_duration` first). One yt-dlp
  `extract_info` shared by metadata, subtitles and format URLs if the code allows.
  Target metadata ≤ 6 s.
- Change: zero-candidate scene path reaches the static-camera fallback (threshold ladder
  0.3 → 0.15 → uniform sampling); ffmpeg rc checked; moment fill seeks the kept 720p
  file; hi-res frames come from the kept file.
- Time markers: `render_transcript(segments, every=20s)` returns the transcript with
  `[m:ss]` at segment starts crossing each 20 s boundary. `clean_text` on the context,
  Qdrant and S3 stay as they are — markers exist only in prompts. Chunked slices keep
  absolute times.
- Filler removal moves into basic cleaning; spaCy pass stays behind
  `TRANSCRIPT_CLEANING_ENABLED=false`.

**1b — tier probe, plan, memory**
- `classify.txt` → `tier_probe.txt` (Appendix B.1): inputs title, channel, description
  (≤ 500 chars), YouTube category, tags, duration, three clean transcript windows
  (start/mid/end ≈ 700 chars each; never annotation text); output `{domain, format,
  has_visual_demo, confidence}`; temperature 0; max_tokens 80; model per the phase-0
  A/B. Remove `reasoning` and the eight traits. Consumers: `derive_tier(probe, title)`
  (replaces `derive_tier(category, title, tags)`); playbook selection as today; one
  `Hint:` line in the plan prompt. Runs at transcript-ready; the frames branch awaits it
  just before Step 6b with a 3 s cap, then falls back to the metadata rule (A21). Delete
  `FRAME_TIER_EARLY_CLASSIFIER` (dead) and the unread `category_confidence`.
- `plan.txt` (Appendix B.2): the full transcript with markers replaces the 3,000-char
  preview; `<transcript_preview>` removed. Output: drop `reasoning`, `outboundLinks`,
  `identity.audience`, `itemCounts`; add `brief` per tab (`what`, `where` time ranges,
  `expect`), `evidence` (Appendix C), `terms` (≤ 12 canonical names). Keep contentTags,
  modifiers, primaryTag, confidence, userGoal, corePromise, uniqueAngle,
  identity.creatorType/tone, extractionGuidance, tabs {id,label,emoji,component,
  dataSource,goal}. Remove `<outbound_links_instructions>`, the `enrich` hint in
  `<attachments>`, and the rule "Always include learning alongside domain tags". "3–6 tabs,
  only tabs the content supports". `<extraction_caps>` rendered from the registry (one cap
  per dataSource). `<format_routing>` keyed on the plan's own `evidence`, not classifier
  traits. The six examples rewritten to the new schema (briefs, no reasoning/outbound).
  Timeout 45 s, 1 retry, no `cache_control`. `_build_fallback_plan` unchanged (fallback
  tabs get empty briefs).
- `memory.txt` (new, Appendix B.3): Haiku (`LLM_EXTRACTION_MODEL`), full transcript with
  markers, temperature 0, max_tokens 1,200, timeout 25 s, 1 retry. Output: `outline`
  (4–12 sections with boundaries, chapter_detect's rules), `evidence`, `tldr`,
  `takeaways` (3–5). Runs in parallel with the plan from probe-done. On failure: no
  outline (chapter_detect fallback only when batching needs it), evidence = plan's only,
  tldr/takeaways from synthesis as today.
- `video_memory` block (Appendix B.4) rendered by code from plan + memory and placed in
  the existing `{video_context}` slot of extraction, enrichment, synthesis.
- `synthesis_complete` emitted at memory-done with `{tldr, keyTakeaways}`; the web hero
  already reads this event (A8). Verify the reducer merges, doesn't replace. Full dict
  re-emitted at synthesis-done.
- `chapter_detect` is called only when batching will produce > 1 batch and no outline
  exists (A24). The description it receives is no longer empty (A9 side finding).

**1c — extraction prompt (still one call)**
- `base_extraction.txt` (Appendix B.5): remove `<critical_rule>` ("NEVER return empty
  arrays… at least 3 items") and "Fill all fields — empty fields mean dead UI"; replace
  `<completeness>` with caps from the registry; `<tabs_to_serve>` carries the briefs;
  `{video_context}` = video_memory; `<visual_context_guide>` present only when an
  annotations block is present; "an empty array is correct when the video has no such
  content"; density line in `<extraction_example>` → "match this specificity, not its
  count".
- Visual annotations are no longer injected into `clean_text` (fixes A17/A20 at the
  root: the `startMs`/`start` bug and the preview pollution). `frame_descriptions` +
  OCR render as `<visual_annotations>` (chronological `[m:ss] caption | on-screen text`)
  appended after the transcript in the extraction call, plus the ≤ 12-line key_frames
  block. Plan, memory and probe never see annotations. Remove `inject_visual_context`
  and Phase 2.5 from the runner.
- Layout and cache (A26): system = rules with no per-video text; user = `[transcript
  with markers + video_memory]` ← the one cache breakpoint (5-min TTL), then
  `[schema(s) + briefs + caps + example + annotations]`. Move `{duration_minutes}` and
  every other per-video value out of the static block. Haiku's floor is 4,096 tokens per
  cached block; short videos simply don't cache.
- Schemas (16 files, Appendix D): strip SCALING blocks and invention floors; keep JSON
  skeleton, `> UI` lines (already stripped for the LLM), RULES, COMMON MISTAKES; fix
  narrative ("merge INTO" → own key) and finance wording; `{duration_minutes}` removed.
- Examples: header line changed; domains without an example get no example block (not
  learning's).
- Remove the synthesis-fed retry (`_attempt_synthesis_fed_retry`,
  `decide_extraction_retry`, count-validation retry gate): 7 firings, 0 observed
  improvements, 5 of 7 bug-triggered (A25). Coverage metric stays as a metric.
- Remove `EXTRACTION_USE_FAST_FIRST` and the fast-first path in `llm_retry.py` (A10: it
  would send the transcript without the schema).

**1d — tail**
- Enrichment → quiz only (Appendix B.6): one `enrich_quiz.txt` with a per-domain flavor
  line from `domains.json`; demand-driven (A7): runs only when the plan has a
  `quiz_arena` tab or an allowed quick_quiz host (in phase 3: after reconcile); input =
  extraction data + video_memory + tab goals (not the first 8,000 chars); output `quiz`
  2–8 items; strict schema, salvage valid items on partial failure; timeout 30 s; never
  blocks tabs. Remove flashcards/scenarios generation, their caps, `enrich_recall.txt`
  and the other 10 enrich files, the ENRICHMENT_MAP entries for quiz-forbidden domains,
  `enrichment.flashcards`/`enrichment.scenarios` from the toolkit's valid list, and the
  plan.txt claim that quiz_arena merges scenarios.
- `quick_quiz` attachment: hosts exclude `step_player`, `step_flow_canvas`, `checklist`,
  `workout_room`, `packing_mission`; quiz tab forced last in assembly order
  (`quizPolicy`).
- `synthesis.txt`: `masterSummary` + `seoDescription` only; input = video_memory +
  compact final extraction + tab labels; runs in parallel with moment fill after
  extraction; `tldr`/`keyTakeaways` in `meta` come from memory (same `meta` shape).
- Vision: N = 8 frames per call, ≤ 5 in parallel under a semaphore, max_tokens scaled
  per batch, 1 retry, Sonnet; prompt gains an index→frame mapping (A18's 7 steps). No
  1024 upscale. Measure 360p-native vs 720p-after-hires on two videos
  (description/OCR quality, wall, cost), pick one, report. `FRAME_VISION_PARALLEL=false`
  = today's single call. `LLM_VISION_MODEL` default = primary (the Haiku default is dead,
  A3).
- Heartbeats around plan and extraction (reuse `run_parallel_phases`' heartbeat).
  Embeddings preloaded at worker start. `drive_pipeline` checks status before running
  (duplicate run, A4). LLM errors: honour `retry-after`, one same-provider retry before
  the cross-provider fallback, fallback model tagged correctly in Langfuse (A27).
  `EXTRACTION_PARALLEL_BATCHES` default → 6 (used by chunked batches now, groups later).

**Tests (phase 1)**
- Unit: `render_transcript` markers (spacing, absolute times on chunks); annotations
  block renderer (chronological, OCR joined); probe/plan/memory output validators
  (new schemas, fallback paths); video_memory renderer (byte-identical across calls of a
  run); cache layout assertion (no per-video text before the breakpoint — a test that
  hashes the static block across two different videos); schema files parse after
  stripping; enrichment salvage; quick_quiz host exclusion; quiz-last ordering; tier
  resolver cap + fallback; zero-candidate ladder; vision batching index mapping.
- Replay: the three cassettes run with the new orchestration; assert plan starts before
  frames-done, metadata ≤ 6 s (stubbed), one 720p download, heartbeats present.
- Golden: full run; gate within noise; new assertions pass.
- Prod: benchmark 3 × 2; regression set × 1.

**Expected**: T1dQhQAm8Tc ≈ 130 s (plan no longer waits for frames; one extraction
call still waits for annotations); HIGH vision 46–63 s → 15–20 s; hero ≈ 25 s.

**Gate 1**: golden within noise (quality, faithfulness, duplicates); new assertions
pass; benchmark table; cost ≤ baseline; no 429/fallback events in timing.

### Phase 2 — progressive output

- Backend: `tab_ready` per tab as it becomes final; provisional Moments tab at
  plan-done from the memory outline (fallback: YouTube chapters / description
  timestamps), replaced by the final one; `synthesis_complete` early (done in 1b);
  final re-emit of all tabs with `final: true` and positions; Redis stream key reset
  per run + TTL; leaked no-TTL streams cleaned (A12).
- Frontend (A8, A12): `meta` must not wipe tabs; `position` is a sort key, not a splice
  index; `initialTab` sticky once chosen; replace-by-id honours new position and
  `final`; DB-vs-stream resolution by `final`/version, not tab count; timeline, pending
  pills and Cancel visible while tabs exist; a late overview cannot steal focus;
  provisional tabs render with a "provisional" state.
- Tests: reducer unit tests for each rule; SSE replay test with the real broker on a
  scratch id (duplicates, order, final flag); Playwright smoke on dev: first tab appears
  before `complete`, no flicker on re-emit.
- **Gate 2**: first visible tab ≤ 35 s on phase-1 code (benchmark); no replay
  duplicates; golden unchanged.

### Phase 3 — the split (one PIPELINE_VERSION bump → v9)

- Field-aware renderer: for a set of dataSources → JSON skeleton with only those keys
  (+ the domain's top-level scalars when the group is the domain's first), the RULES /
  COMMON MISTAKES lines that name those fields, the example sliced to those keys, caps
  per field. Round-trip test: slice parses, contains only requested keys.
- Reconcile at plan-done (Appendix C): dataSource validity; evidence requirements →
  demote via `demoteTo` before extraction; conditional domain requirements; quiz policy;
  dedupe; 3–6 tabs; decisions recorded in `pipeline.reconcile`.
- Groups from the registry (Appendix A): requested = ∪ over reconciled tabs of
  dataSource + fallbacks + upgrades, + scalars per requested domain. Partition
  `waitsForVisual` vs text; text grouped by domain; split a domain by field when its
  estimated output (`outputWeight × expect`) exceeds ~1.5k tokens; ≤ 5 text groups + 1
  visual. Group 1 = the group of the first planned tab.
- Firing: text groups at plan-done; fire group 1, wait for its first streamed token (or
  1.5 s), then the rest (a cache entry exists only after the first response begins; also
  avoids burst 429s). Visual group at frames-done. All under `EXTRACTION_PARALLEL_BATCHES`.
  `EXTRACTION_PARALLEL=false` → one call with the full requested field set (still no
  unplanned fields).
- Per-call layout = phase 1c layout; the tail is per group (Appendix B.5). Visual group
  tail adds `<visual_annotations>` + key_frames.
- Validate per group against the same Pydantic item models without materializing
  defaults (A5); filter each group's output to its own keys; deep-union merge,
  chunk-major, scalars first-wins (`extraction_merger`); `extraction_data` shape unchanged.
  Count/coverage metrics computed on requested fields only.
- Per-group assembly and emission: a tab assembles when all its requested fields have
  landed; `tab_ready{final:true}`; attachments (quick_quiz etc.) added at the final
  re-emit; overview/meta last; `resolve_cross_tab_links` at the end.
- Enrichment (quiz) after the group owning its subject lands; synthesis ∥ moment fill ∥
  faithfulness after the last group.
- Long videos: chunks from the memory outline merged to ≤ 50k tokens / 40 min; calls =
  chunks × groups under the semaphore; annotations sliced per chunk for the visual
  group; merge chunk-major then group-union. `chapter_detect` only if no outline.
- Failure: one group fails after one retry → its tabs demote/drop, the rest ship,
  recorded in `droppedTabs`; all groups fail → error as today. Plan failure → fallback
  plan + fallback tabs' dataSources as the requested set.
- Bump `PIPELINE_VERSION`; re-register prompts; demo library re-run script (runs on
  Kfir's go).
- Tests: renderer round-trips for all 16 domains; grouping (partition, split, caps,
  group-1 rule); reconcile table-driven tests (every evidence combination, every
  domain's requirements, quiz policy); stagger (first token before the rest); merge
  (keys filtered, scalars first-wins, chunk order); per-group assembly (tab waits for all
  its fields); failure paths; cache layout hash test across groups (identical block);
  replay: three cassettes with group timings synthesized from recorded output sizes;
  golden full run; prod benchmark 3 × 2 + regression set; cost per video from the ledger
  (requests tagged by group).
- **Expected**: HIGH cooking ≈ 55–60 s (≤ 65), text-only ≤ 55 s, hero ≤ 30 s; cost below
  baseline (no retry, ~30% fewer output tokens, no upscale, enrichment skipped for ~60%
  of videos, real cache reads).
- **Gate 3**: golden within noise on quality and faithfulness; new assertions pass;
  duplicate items ≤ baseline; benchmark table; cost; 429 count 0.

### Phase 4 — sweep, docs, report

- Sweep: dead code and settings left by phases 1–3 (list them: `inject_visual_context`,
  retry paths, chapter_detect on the single path, flashcards/scenarios, fast-first,
  `FRAME_TIER_EARLY_CLASSIFIER`, `to_video_context_full`/`video_dna_text`,
  `outboundLinks` readers, itemCounts readers, plan cache_control, old enrich files,
  `category_confidence`), `.env.example`/`.env.production.example`,
  `docs/INFRASTRUCTURE.md` flags table (A16: 50 of 112 settings weren't passed through —
  document what is).
- Docs: summarizer docs' stale lines (S3/region defaults, "SHORT < 30 min" vs 900 s, "3–6
  LLM calls", pass-2 stream seek, fast model = gpt-4o-mini on prod, vision model, chunked
  semantics, the new phases and the v9 flow); README: only numbers that changed (cost,
  timing); eval sentence → "twice a month" after the first scheduled run.
- Report: before/after per phase (phase walls, first tab, total, cost) for the benchmark
  + regression sets; what each phase bought; levers not taken with projected gain
  (outline from the plan instead of memory; slicing long videos by brief ranges; 720p
  vision; `WORKER_CONCURRENCY=2`; 1 h cache TTL at traffic; Haiku vision; m6i).

## Appendix A — registry (new top-level maps in domains.json)

```jsonc
"dataSources": {
  "food.ingredients": { "domain": "food", "field": "ingredients", "kind": "list",
    "components": ["checklist", "info_grid"], "siblings": ["project.materials"],
    "requiresEvidence": "has_ingredients", "waitsForVisual": false,
    "cap": 30, "outputWeight": 30 },
  "food.steps": { "domain": "food", "field": "steps", "kind": "list",
    "components": ["step_player", "step_flow_canvas", "moment_track"],
    "siblings": ["project.steps"], "requiresEvidence": "has_steps",
    "waitsForVisual": false, "cap": 25, "outputWeight": 60 },
  "tech.snippets": { "domain": "tech", "field": "snippets", "kind": "list",
    "components": ["code_playground", "step_player"], "requiresEvidence": "has_code",
    "waitsForVisual": true, "cap": 12, "outputWeight": 150 },
  "gaming.highlights": { "...": "...", "waitsForVisual": true },
  "enrichment.quiz": { "domain": "enrichment", "field": "quiz", "kind": "list",
    "components": ["quiz_arena"], "requiresEvidence": "is_learnable", "cap": 8 }
  // one entry per path in valid_datasources; scalars are derived from each schema's
  // JSON skeleton (top-level non-list keys) and need no entries
},
"demoteTo": {
  "step_player": ["step_flow_canvas", "moment_track", "info_grid"],
  "checklist": ["info_grid"], "code_playground": ["info_grid"],
  "tier_list": ["info_grid"], "comparison": ["info_grid"], "quiz_arena": []
},
"requirementEvidence": {
  "food": { "step_player": "has_steps", "checklist": "has_ingredients" },
  "project": { "step_player": "has_steps", "checklist": "has_materials" },
  "tech": { "code_playground": "has_code" }, "review": { "comparison": "has_comparison" },
  "fitness": { "workout_room": "has_drills" }, "travel": { "spot_explorer": "has_itinerary" },
  "gaming": { "tier_list": "has_ranking" }, "sport": { "formation_diagram": "has_lineup" },
  "news": { "claims_tracker": "has_claims" }
},
"quizPolicy": { "position": "last", "requiresEvidence": "is_learnable",
  "attachmentHostsExclude": ["step_player", "step_flow_canvas", "checklist",
                             "workout_room", "packing_mission"] },
"enrichment": { "quizDomains": ["learning", "language", "tech", "science"],
  "flavor": { "tech": "Think like a senior developer onboarding a teammate…", "...": "..." } },
"grouping": { "maxTextGroups": 5, "groupOutputBudget": 1500 }
```

Also fix: default tab `tech.setup` → `tech.setup.commands`; language `vocabulary` default
tab → `flash_deck`; `outputWeight` initial values estimated from the 42 stored v8 docs
(tokens per item per field), tuned later.

## Appendix B — prompt layouts

**B.1 tier_probe.txt** — in: title, channel, description ≤ 500, YouTube category, tags,
duration, three clean windows. Out: `{"domain","format","has_visual_demo","confidence"}`.
Keep the domain/format lists and `<classification_guidance>`; drop traits and reasoning.

**B.2 plan.txt output**
```json
{ "contentTags": ["food"], "modifiers": ["finance"], "primaryTag": "food", "confidence": 0.93,
  "userGoal": "...", "corePromise": "...", "uniqueAngle": "...",
  "identity": { "creatorType": "...", "tone": "..." },
  "extractionGuidance": { "primaryFocus": "...", "watchOutFor": "..." },
  "terms": ["guanciale (not pancetta)", "consommé = the braising liquid", "Marco = the chef"],
  "evidence": { "has_steps": true, "has_ingredients": true, "has_code": false, "...": false, "is_learnable": false },
  "tabs": [ { "id": "ingredients", "label": "🛒 14 Ingredients", "emoji": "🛒",
              "component": "checklist", "dataSource": "food.ingredients",
              "goal": "Everything you need — check off as you gather.",
              "brief": { "what": "every ingredient with amount/unit as the chef says it; two groups: consommé, tacos",
                         "where": ["1:10-2:40", "14:00-14:30"], "expect": 14 } } ] }
```
Dynamic block after `<video>`: title, channel, duration, hint from the probe, playbook,
description, then the full transcript with markers. Static block unchanged in spirit
(toolkit, routing, requirements, examples, rules) with the edits in 1b.

**B.3 memory.txt output**
```json
{ "outline": [ { "start": "0:00", "end": "1:10", "title": "intro — what makes this birria different" } ],
  "evidence": { "...": "same keys as the plan" },
  "tldr": "≤ 150 chars, the video's angle, not its topic",
  "takeaways": ["3–5, each with a specific number, name or measurement"] }
```
Outline rules = chapter_detect's: first at 0:00, last ends at the duration, non-overlapping,
≥ 1 min each, 4–12 sections.

**B.4 video_memory block** (rendered, ≈ 250–400 tokens, identical for every call of a run)
```
<video_memory>
domains: food (+finance) · goal: Cook birria tacos for 6 this weekend
promise: … · angle: … · creator: professional chef · tone: calm-educational
focus: every temperature and timing · watch out: visual cues said, not measured
outline:
  0:00–1:10 intro — what makes this birria different
  1:10–2:40 ingredients, two groups: consommé, tacos
  2:40–13:00 cooking: toast chiles → blend → braise → assemble
  13:00–19:00 plating, dipping, tips
terms: guanciale (not pancetta); consommé = braising liquid; Marco = the chef
evidence: has_ingredients ✓ 1:10–2:40 · has_steps ✓ 2:40–13:00 · is_learnable ✗
</video_memory>
```

**B.5 extraction call**
```
system   <role> + <voice> + <quality_rules> + <output_rules>         (no per-video text)
user     <transcript> [0:00] … [0:20] … </transcript>
         <video_memory> … </video_memory>                              ← cache breakpoint
         <your_job>
           Emit ONLY: food.ingredients, food.steps, food.meta
           Tabs you serve:
             🛒 Ingredients — checklist ← food.ingredients — where 1:10–2:40, 14:00–14:30 — expect ~14, cap 30
             👨‍🍳 Steps — step_player ← food.steps — where 2:40–13:00 — expect ~8, cap 25
           Schema: <skeleton for these keys>   Rules: <lines naming these fields>
           Example: <sliced; match its specificity, not its count>
           An empty array is correct when the video has no such content.
         </your_job>
         visual group only: <visual_annotations> [m:ss] caption | on-screen text … </visual_annotations> + <key_frames>
```
Phase 1 uses the same layout with "Emit ONLY" = all fields of the planned domains.

**B.6 enrich_quiz.txt** — role + `{flavor}` line, `{video_memory}`, `{extraction_data}`
(the owning group's output in phase 3), `{tab_goals}`, quiz rules from `enrich_study.txt`
(spread across topics, one NOT question, 30/50/20 difficulty, explanations teach the why,
cite the timestamp when known), 2–8 items, JSON `{ "quiz": [...] }` only.

## Appendix C — evidence and reconcile

Evidence keys (both plan and memory emit all of them, booleans):
`has_steps, has_ingredients, has_materials, has_code, has_drills, has_comparison,
has_ranking, has_lineup, has_claims, has_lyrics, has_itinerary, has_packing, is_learnable`.

Reconcile (code, at plan-done, before any extraction):
1. dataSource not in the registry → sibling with the same component, else drop the tab.
2. A tab whose dataSource `requiresEvidence` K: demote through `demoteTo` only when
   **both** plan and memory say K = false (memory missing → no demotion); record it.
3. Domain `required` components are backfilled only when `requirementEvidence` for them
   is not doubly-false.
4. Quiz: only domains in `quizDomains`, only if `is_learnable` by either reader; the tab is
   moved last; `quick_quiz` never on excluded hosts.
5. Dedupe tabs by (dataSource, component); enforce 3–6; overview is added at assembly as
   today.
6. Write `pipeline.reconcile = [{tab, action, reason}]` to the Mongo doc.

## Appendix D — per-file prompt cleanup

- `base_extraction.txt`: remove `<critical_rule>`, "Fill all fields", `<completeness>`,
  the `<visual_context_guide>` unconditional; `<tabs_to_serve>` = briefs;
  `<extraction_example>` density line → specificity.
- Schemas: `food` (SCALING; keep ingredient amount rules), `fitness` (SCALING), `tech`
  (SCALING incl. "Include all code shown" → keep as a rule, drop counts), `learning`
  (SCALING; keep COMPLETENESS as "cover the full video chronologically"), `science`,
  `language` (SCALING floors), `music`, `travel` (SCALING + "Minimum: 1 day with spots, 5
  packing items" + "estimate weight when not stated" → "omit weight when not stated"),
  `review`, `project`, `gaming` (SCALING counts; keep the unboxing note), `news`, `sport`,
  `podcast` (SCALING), `narrative` ("Merge this INTO the main domain data" → own key),
  `finance` (keep; CC confirms its consumer).
- `plan.txt`: as 1b. `classify.txt` → `tier_probe.txt`. `chapter_detect.txt`: kept,
  fallback only. `synthesis.txt`: trimmed. `enrich/*`: one file. `description_analysis.txt`:
  unchanged. `component_toolkit.txt`: `<valid_datasources>` rendered from the registry;
  `enrichment.flashcards/scenarios` removed; "quiz_arena absorbs scenarios" removed.
- Examples: header line → "Match this specificity and field completeness; counts come
  from your brief." Missing-example domains (gaming, language, science, podcast, news,
  sport) get no example rather than learning's.

## Appendix E — tests and checks summary

| Phase | Unit | Replay | Golden | Prod |
|---|---|---|---|---|
| 0 | registry, timing schema, gate.py | reproduces 240 s | baseline ×2 (noise) | baseline 3×2 |
| 1 | markers, renderers, validators, cache hash, salvage, policy, tier, ladder, batching | new orchestration, no waits | gate | 3×2 + regression |
| 2 | reducer rules, SSE replay | first tab before complete | unchanged | first-tab time |
| 3 | renderer ×16, grouping, reconcile table, stagger, merge, assembly, failures, cache hash | synthesized group timings | gate + assertions | 3×2 + regression + cost |
| 4 | — | — | scheduled run | demo library re-run |

## Appendix F — Kfir's side (you list, he does)

`.env.production` changes (expect: `EXTRACTION_PARALLEL_BATCHES=6`; the two booleans
default on, no entry needed); GitHub secrets for the eval; budget alert; approvals at each
gate; tier-probe model decision after the A/B; demo library re-run go.
