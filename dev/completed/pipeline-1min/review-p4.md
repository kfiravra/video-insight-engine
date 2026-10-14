# Phase-4 review — recipe for review agents (read-only)

You review ONE phase-4 task's UNCOMMITTED diff (or, at the end, the whole phase as one unit) in
/home/kfir/projects/video-insight-engine (branch feat/pipeline-1min). READ-ONLY: no file edits, no
git mutations, no pipeline runs, no LLM spend, no container commands. Targeted pytest on a few files
is allowed (summarizer: `cd services/summarizer && .venv/bin/python -m pytest <paths> -q`); nothing
heavy (no full suites, no pyright on > 10 files) — other agents use the box's memory.

1. Read the project's review recipe `.claude/commands/review.md` fully and apply it (labels,
   severities, compliance checks; `.claude/skills/backend-python/SKILL.md` for services/ and
   packages/, `backend-node` for api/, `react-vite` for apps/web/).
2. Context: your task's brief (named in your group line), `pipeline-1min-tasks.md` Phase 4 rows
   (incl. the "Checked against the code 2026-10-13" facts), `pipeline-1min-plan.md` §Phase 4,
   `pipeline-1min-context.md` (D1–D29 + the "4.0c docs facts" block + phase-4 run state),
   `agent-rules.md` phase-4 section. The brief + Kfir's task rows are the spec.
3. Review the diff: `git diff -- <paths>` + the new files the coordinator lists; the agent's report
   summary is in your group line. Judge your task's paths only; report an interaction with committed
   work when you see one.
4. Focus: correctness bugs; dependency/lock consistency (pins vs requirement floors, `# via` lines,
   the same pin in CI + Dockerfiles + prod); async mistakes; error handling; behaviour changes the
   report does not declare (model input, emitted events, stored docs, S3 object keys / metadata /
   content types); **`EXTRACTION_PARALLEL=false` byte-identity**; LLM-call contract per model
   (params a model rejects, refusal paths, cost tracking); test gaps for the acceptance criteria;
   dead code; project rules (functions < 50 lines, files < 500 lines — only NEW violations or growth
   caused by the diff). Anything outside the phase-4 tasks → a "g4 question" line, not a finding.

OUTPUT (≤ 60 lines, no preamble): one line per finding, Critical first, then at most 10 Warnings,
no Info:
`[C|W] <label> | <file>:<line> | <issue> | <why it matters> | <concrete fix> | output-changing: y/n`
Then optional `g4 question:` lines. End with one line:
`files reviewed: N; findings: C=<n> W=<n>`.

## Groups (id → task → paths → focus)
- R4a 4.0a → uncommitted: `packages/llm-common/pyproject.toml`, `services/summarizer/requirements.txt`,
  `services/summarizer/requirements.lock`, `services/assistant/requirements.txt`,
  `services/assistant/requirements.lock`, `services/summarizer/src/services/media/s3_client.py`,
  `services/summarizer/src/services/transcription/transcript_store.py`,
  `services/summarizer/tests/test_s3_client.py`, `services/summarizer/tests/test_phase_assembly_transcript_store.py`,
  `scripts/migrate-s3-keys.py`, `scripts/backfill-transcripts.py`,
  `services/summarizer/scripts/backfill-transcripts.py`, `docs/SERVICE-SUMMARIZER.md` → brief
  `brief-4.0a.md`. Agent's evidence: `<scratch>/p40a/` (lock-diff.txt, code-diff.txt, capture-diff.txt
  empty, wire-191/105.jsonl identical, replay3x3.txt, suite-*.txt, pyright-*.txt, smoke-out.txt).
  Check: the locks resolve (every pin satisfies every dependent's range — litellm 1.105.0's floors,
  aiobotocore 3.9.2's botocore range vs botocore 1.43.106 in the summarizer, boto3 ↔ botocore ↔
  s3transfer versions in step), `# via` lines correct, nothing else moved; aiobotocore 3.x client
  semantics vs aioboto3 at each of the 8 call sites (context-manager use, `get_object` body
  read/close, paginators, `delete_objects`, `head_object` errors / `ClientError` codes, presigned URLs
  still on the sync boto3 client, credentials/endpoint/region config passed the same way, no session
  created per call that leaks connectors); tests still exercise the real paths (mocks target the new
  import); the scripts' swap; docs line accurate.
- R4.3 4.3 → uncommitted: `dev/completed/pipeline-1min/gates/g4.md` (new, report skeleton) → brief `brief-4.3.md`.
  Check EVERY number against its cited source (`gates/g0-prod-baseline.md/.json`, `g1.md`, `g1-prod-benchmark.json`,
  `g2.md`, `g3.md` incl. §Prod as corrected in commit "correct gate 3 §Prod", `g3-prod-benchmark.json`,
  `evidence/A-DIGEST.md`): wrong numbers, numbers with no source, dev numbers presented as prod, medians that mix the
  two no-frames runs (T1dQ r1, jMq8 r1) with with-frames runs without saying so, lever arithmetic errors, projections
  not marked "rough" when they rest on 1–2 runs, missing levers from the brief's list. Use python3 -I on the JSON.
  Findings format as above; "output-changing" = n always.
- R4.2 4.2 part 1 → uncommitted: `docs/summarizer-workflow.md` (rewritten), `docs/SERVICE-SUMMARIZER.md`,
  `docs/ARCHITECTURE.md`, `docs/API-REFERENCE.md`, `docs/OBSERVABILITY.md`, `docs/llm-cost-model.md`,
  `docs/DATA-MODELS.md`, `docs/ERROR-HANDLING.md`, `README.md` → brief `brief-4.2.md`. Check every claim against the
  CODE ON HEAD (call order in `routes/pipeline_runner.py` / `pipeline_orchestration.py` / `phases/*`, the field-group
  modules, settings in `src/config.py`, SSE events in `models/sse_events.py` + `packages/types`): wrong order, wrong
  file/function anchors, settings that don't exist or have other defaults, invented numbers (every number must cite a
  gate file), model names stated as fact (they wait for part 2), stale claims from the brief's list still present,
  broken links / anchors. 4.1 is removing dead code in parallel (uncommitted) — a doc that names a symbol 4.1's diff
  removes is a finding. Findings format as above; "output-changing" = n.
- R4.1 4.1 part 1 → uncommitted (summarizer): `src/services/pipeline/phases/extraction.py`, `extraction_chunks.py`,
  `extractor.py`, `prompt_builder.py`, `phases/__init__.py`, `phases/triage.py`, `phases/translation.py`,
  `src/routes/pipeline_runner.py`, `tests/test_extraction_chunks.py`, `tests/test_removed_symbols.py` (new);
  `scripts/register_prompts.py`, `scripts/activate_langfuse.sh`; `packages/shared/src/config/domains.json`;
  `packages/types/src/output-types.ts`, `packages/types/src/video.ts`; web
  `apps/web/src/features/video-output/**/stream-event-processor.ts` + `__tests__/stream-event-processor-v2.test.ts`;
  `.env.example`, `.env.production.example`, `docs/INFRASTRUCTURE.md` → brief `brief-4.1.md` (NOT `pipeline_timing.py` /
  `llm*` / `llm_common` — 4.0b's). Check: each removal has no live reader (grep api/, apps/web/, services/, packages/,
  scripts/ incl. stored-doc readers for v8 docs still served to users — D25/v9: old versions keep serving their owners),
  the shared `extraction_video_info(ctx)` gives both paths the same dict as before (false path byte-identity — the
  agent's capture: 12 files identical), the web reducer still handles DB-cached v8 docs, the guard test is meaningful,
  env examples vs the compose anchors (every documented passthrough exists; defaults match), the INFRASTRUCTURE table
  vs `config.py` + both compose files (spot-check ≥ 15 rows), domains.json change is note-only. Findings format above.
- R4b 4.0b → uncommitted: NEW `services/summarizer/src/services/llm_model_rules.py`, `src/services/llm_call.py`,
  `packages/llm-common/src/llm_common/refusal.py`, tests `services/summarizer/tests/{llm_wire.py,
  test_llm_model_rules.py, test_llm_wire_body.py, test_llm_refusal.py, test_pipeline_timing_refusals.py}`,
  `packages/llm-common/tests/test_refusal.py`; MODIFIED `services/summarizer/src/{config.py, services/llm.py,
  services/llm_provider.py, utils/llm_retry.py, services/pipeline/pipeline_timing.py}`,
  `packages/llm-common/src/llm_common/{callback,context,models}.py`, tests `test_replay_extraction_identity.py`,
  `test_config.py`, `test_llm_retry.py`, `test_llm_service.py` → brief `brief-4.0b.md`. Agent's evidence:
  `<scratch>/p40b/` (wire-proof.txt, probe.json, suite-summarizer-1.txt, capture results 12/12 identical).
  Declared output changes: `pipeline.timing.version` 3 → 4 (`counts.refusals`, Refusal `llmFailures` rows with
  tokens + cost, `refusedModel`/`refusalCategory`, `costUsd` includes refused attempts); `llm_usage` rows gain
  `refusal`, `refusal_category`, `fallback_from` (both services); Langfuse modelParameters; any `content_filter`
  finish = refusal on every model. **Lens A (correctness):** guard logic per model (sampling / thinking / effort /
  prefill) vs the 4.0c docs facts and LiteLLM 1.105's real transform; refusal path (detection non-streamed +
  streamed, one re-send on the previous model with that model's params, never the same model, LLMRefusalError →
  None in `call_llm_with_retry`, composition with D4's cross-provider fallback and with the split path's lost-answer
  re-ask, streamed partial discarded, first-chunk callbacks / warm-first release on a refused streamed text1), Haiku
  5.5 map registration (only when absent; prices; flags), async/cancellation, file sizes (llm_provider 492,
  llm_retry 495 — growth near the cap). **Lens B (contracts):** every reader of `pipeline.timing` (api, admin
  service + its React UI, scripts/cache_hit_rate.py, scripts g3data/run_eval/gate, web) vs version 4 + the new
  fields + `costUsd` semantics; every reader/writer of `llm_usage` (api cost monitor / daily cost cap, admin, llm-common
  aggregations) vs the new fields + refused-attempt rows (does a refused attempt count toward a user's daily cost cap?
  should it?); `EXTRACTION_PARALLEL=false` identity (canonical_prompt keeps the fixtures' meaning); `PREVIOUS_MODEL`
  ids vs config's model ids/prefixes; docs facts (context.md "4.0c") vs code.
- R4d 4.0d → uncommitted: A/B tool `scripts/model_refresh_ab.py` + `scripts/_model_ab_*.py` (types, capture, inputs,
  frames, cost, scores, scores_items, runner, report, wire), tests `services/summarizer/tests/test_model_refresh_ab_scores.py`,
  `test_model_refresh_ab_cost.py`; config: `services/summarizer/src/config.py` + `tests/test_config.py`,
  `docker-compose.yml`, `docker-compose.prod.yml`, `src/utils/llm_retry.py` (MODEL_CHAR_LIMITS) + test,
  `packages/llm-common` cache rates + test, `src/services/transcription/gemini_transcriber.py` (comment) +
  `tests/test_gemini_transcriber.py` (test removed), `dev/completed/pipeline-1min/env-changes.md`; report
  `dev/completed/pipeline-1min/gates/g4-model-ab.md/.json` (198 calls, $1.084) → brief `brief-4.0d.md` + the PICKS in
  `gates/g4-notes.md` (plan + vision → Sonnet 5.5 low; extraction + memory + tier probe stay Haiku 4.5;
  description / quiz / output + RAG translation → gpt-6-luna; judge / synthesis / chapter_detect unchanged; the primary
  = Sonnet 5.5 with LLM_EXTRACTION_MODEL defaulting to Haiku 4.5 so only the plan moves).
  **Lens A (config + contracts):** every changed default in config vs both compose anchors (literal `${X:-…}` values
  equal, blank .env lines fall back), the primary change moves ONLY the plan (grep every `llm_model` / default-provider
  use), dead settings truly unread (grep src, tests, scripts, docs readers), MODEL_CHAR_LIMITS values, cache-rate
  values vs the docs facts, env-changes.md prod lines correct vs today's prod pins (context.md "Confirmed by Kfir"),
  `EXTRACTION_PARALLEL=false` identity unaffected, CI (ci.yml) / Dockerfiles need nothing.
  **Lens B (A/B tool + picks):** the tool measures what it claims (same request bytes per arm except model-implied
  params; scorer correctness — e.g. required-tab equivalents, items, cards, echo; cost from completion_cost incl. cache;
  resumability; no Langfuse/Mongo writes; files < 500 / functions < 50); and whether each pick follows from
  `g4-model-ab.md` under "quality within noise first, then latency, then cost" — flag any pick the numbers don't support.
- R-final → the end-of-phase two-lens review (added then).
