# Observability

LLM observability is the project's debugging story for "this video came out wrong, why?". Aggregate cost is tracked locally in MongoDB via `usage_tracker.py`; per-call traces, prompt versions, datasets and scores live in [Langfuse](https://langfuse.com).

Everything is **best-effort**: every Langfuse call is wrapped in try/except, all helpers no-op when keys are unset (the SDK is not even imported then), and the current span lives in a contextvar so concurrent pipeline runs don't contaminate each other's traces. A failed observability layer must not bring down the pipeline.

The services use the Langfuse **Python SDK v4** (`langfuse>=4.7,<5`, OpenTelemetry underneath) through one shared client, `packages/llm-common/src/llm_common/observability/`; each service's `src/services/observability` re-exports it bound to its settings. Compose sets `LANGFUSE_TRACING_ENVIRONMENT` (`development` in dev, `production` in prod) and prod's `ENVIRONMENT=production` is passed to the SDK explicitly too, so dev and prod traces filter apart even inside one project.

## The id model: run, session, request

Three ids tie every surface together. Start from whichever one you have:

| Id | One of | Minted by | Langfuse | Admin | Logs | Sentry | Grafana |
|---|---|---|---|---|---|---|---|
| `runId` | pipeline attempt (first run, bypass, regenerate, retry) | vie-api per dispatch; the summarizer for attempts the api did not start | the trace (id = `trace_id_for_run(runId)`), tag `run:<id>`; session `pipeline-<videoSummaryId>` groups a row's attempts | Usage → Pipeline Runs (one row per run, link to its trace and session); video detail → its runs | `runId` (pino), `run_id` (structlog) | tag `runId` (Python) | `pipeline_run_started` / `pipeline_run_finished` lines (Pipeline dashboard) |
| `sessionId` | chat surface (one video chat or the library chat) | the web (`vie-chat` store), sent as `X-Session-Id` | session `chat-<id>`: one trace per turn (`turn:<n>`), tool spans inside; a video the chat generated carries `chatSession:<id>` | Usage → Chat Sessions (turns, tool LLM calls, cost, link) | `sessionId` / `session_id` | tag `sessionId` | `chat_turn_finished` lines (Chat dashboard) |
| `requestId` | HTTP request | vie-api (`X-Request-ID`), forwarded to every service | tag `requestId:<id>` | the run row's request id | `requestId` / `request_id` | tag `requestId` | the raw log lines |

`llm_usage` rows carry all of them (`run_id`, `trace_id`, `session_id`, `request_id`, plus `attempt`, `pipeline_version`, `bypass`); `scripts/find-request.sh <any of the three>` greps the logs of vie-api, vie-summarizer, vie-summarizer-worker, vie-assistant and vie-admin. Details: [Ids](#ids-run-trace-session-request), [Grafana Cloud](#grafana-cloud-logs-metrics-dashboards-alerts), [Request tracing & Sentry](#request-tracing--sentry).

## Quick start

1. Sign up at https://cloud.langfuse.com (free tier: 50K observations/month — enough for ~500 videos/month).
2. Create two projects (`vie-dev`, `vie-prod`) and grab keys for each.
3. Add to `.env`:

   ```
   LANGFUSE_PUBLIC_KEY=pk_…
   LANGFUSE_SECRET_KEY=sk_…
   LANGFUSE_BASE_URL=https://cloud.langfuse.com
   LANGFUSE_FAITHFULNESS_SAMPLE_RATE=0.2
   ```

4. Restart the stack with `docker compose up -d`. The `vie-langfuse-init` service runs once on boot, syncs every local prompt to the configured project under `label="production"`, then exits. Downstream services do **not** block on it — the sync runs in parallel with summarizer/assistant startup, and if the race loses (Langfuse 404 during boot), the runtime falls back to the bundled local `.txt` prompts. Late uploads take effect on the next video run.
5. Submit a video. The trace appears under **Traces** in the Langfuse UI within seconds.

To run **without** Langfuse: leave the keys blank. The pipeline behaves identically.

## Trace structure

One attempt = one `runId` = one trace named `pipeline:{youtubeId}`, whose id is seeded from the run id (`trace_id_for_run(runId)` = `sha256(runId)` hex, first 32 chars, so vie-api, the admin and the eval link to it without asking Langfuse), in the session `pipeline-{videoSummaryId}` (so a row's first run, bypass, regenerate and retries group together), tagged with `youtubeId`, `videoSummaryId`, `path:worker|sse|dev` (the entry path), `run:<runId>`, `attempt:<n>`, `pipelineVersion:<v>`, `bypass:true|false`, `retryOf:<runId>` (a retry the summarizer minted) and `chatSession:<id>` (P1) when set, and `requestId` (when present); see [Ids](#ids-run-trace-session-request) for who mints which id; the user id follows `LANGFUSE_USER_ID_MODE` and is never copied into metadata. A Redis cache hit is no run and opens no trace. Every `timed_step` (each `run_parallel_phases` member and the dotted sub-steps such as `transcript`, `hero_call`, `quiz_call`) and the five orchestrator phases (`metadata`, `transcript_frames`, `visual_inject`, `assembly`, `translation`) is a `phase:<name>` span with real start/end; each LLM call is a generation opened just before the request and nested under the phase it ran in (tasks spawned inside a phase inherit it, so the t=0 background calls sit under `phase:metadata`). A generation carries `usage` (input/output/total plus `cache_read_input_tokens` / `cache_creation_input_tokens`, and the cost), `modelParameters` (what the request actually sent after the model's request rules — `temperature`, `reasoning_effort`, the `thinking` type, `effort`; `llm_call.model_parameters`) and `metadata` (attempt, useFastModel, modelOverride, latencyMs). Models below are the `config.py` / compose defaults (4.0d, pipeline-1min gate g4-model-ab); "fast tier" = gpt-4o-mini with `LLM_FAST_PROVIDER=openai` (both `.env` templates and prod), Haiku 4.5 by default. Whisper/Gemini transcription bypasses LiteLLM, so it emits its own `transcription:<provider>` generation (and `llm_usage` row) via `services/summarizer/src/services/transcription/usage.py` — Whisper carries `audioSeconds` in metadata (duration-priced), Gemini carries token counts; see [llm-cost-model.md](./llm-cost-model.md#transcription-cost-tracking).

```
Trace: pipeline:{youtubeId}   (id = trace_id_for_run(runId); session pipeline-{videoSummaryId})
├── generation: transcription:openai  model=whisper-1         audioSeconds cost  (audio-fallback only)
├── generation: transcription:google  model=gemini-…          tokens   cost      (audio-fallback only)
├── generation: description_analysis  model=gpt-6-luna        (LLM_DESCRIPTION_MODEL; reasoning off)
├── generation: tier_probe            model=haiku-4.5         (LLM_CLASSIFIER_MODEL)
├── generation: plan                  model=sonnet-5.5        (the primary model, effort medium) tokens  cost  latency
├── generation: memory                model=haiku-5.5         (LLM_MEMORY_MODEL; ∥ plan)
├── generation: frame_vision          model=sonnet-5.5        (LLM_VISION_MODEL; 1-5 batches; none on LOW tier / manifest hit)
├── generation: chapter_detect        model=fast tier         (long videos past one batch, without listed chapters, description timestamps or a usable memory outline)
├── generation: extraction_text1      model=sonnet-5.5        (LLM_EXTRACTION_MODEL) the split path's warm-up group call: streamed, `ttftMs` in pipeline.timing
├── generation: extraction_text2…N    (released on text1's first chunk; ≤ 5 text groups)
├── generation: extraction_visual     (fired at frames-done; absent when no tab needs it)
│   └── (long videos: the same names + `_c<k>` for chunk k ≥ 1, e.g. `extraction_text1_c1`;
│        ledger feature `summarize:extraction:<group>`. EXTRACTION_PARALLEL=false: one `extraction`
│        generation, or one `extraction_batchN` per chapter batch + `_seq` for the rate-limited second pass)
├── generation: hero_rewrite          model=sonnet-5.5       (LLM_EXTRACTION_MODEL; HERO_REWRITE_ENABLED; one call fired at text-groups-done that reads the field groups' cached blocks, ledger feature `summarize:hero_rewrite`; a retry — only when the hero would be None or a D18 line would drop — is a second generation under the same name)
├── generation: merge_rank            model=haiku-5.5         (LLM_MEMORY_MODEL; MERGE_RANK_ENABLED; chunked runs only: one call fired at text-groups-done beside the hero — dedupe + rank, ids in, ids out (code sections the key ideas by their stamps and words, and keeps every Q&A question); ledger feature `summarize:merge_rank`, timing step `merge_rank_call`; a re-ask after a reply that is no JSON object is a second generation under the same name; outcome `pipeline.extraction._mergeRank`)
├── generation: synthesis             model=fast tier         (only when the hero call is off or gave no hero — HERO_REWRITE_ENABLED=false, or `None` back; ledger feature `summarize:synthesis`)
├── generation: quiz_v2               model=sonnet-5.5        (LLM_EXTRACTION_MODEL, reads the field groups' cached blocks; QUIZ_V2_ENABLED; one call per part; only on a quizPolicy.formats format with a quiz host; ledger feature `summarize:enrichment`)
├── generation: enrichment            model=gpt-4o-mini       (LLM_ENRICHMENT_MODEL; the quiz with QUIZ_V2_ENABLED=false; only for a plan with a quiz host)
├── generation: faithfulness          model=gpt-6-luna        (LLM_JUDGE_MODEL; judge calls, background: the extraction sample's ≤ 6 claims, plus after assembly one hero call (only when the hero rewrite wrote the hero) and one quiz call (only with `QUIZ_V2_ENABLED`) under the same name — told apart by their scores)
├── generation: translation_full      model=gpt-6-luna        (LLM_TRANSLATION_MODEL; non-English video: the output, in batches) + translation_title
├── generation: rag_translation       model=gpt-6-luna        (LLM_TRANSLATION_MODEL; non-English caption / S3 / Gemini transcript → English for Qdrant)
├── generation: <span> (refused)      a declined answer: metadata refusal=true, refusalCategory; the previous model's
│                                     answer follows under the same span with refusedModel + refusalCategory
├── span: phase:<name>                every `timed_step` and orchestrator phase; the generations above nest under the phase that ran them (the tree is drawn flat here)
├── score: faithfulness               0.0–1.0  (sampled, see below)
├── score: faithfulness_hero          0.0–1.0  (the hero's takeaways against their [m:ss] stamps, see below)
├── score: faithfulness_quiz          0.0–1.0  (2 sampled quiz questions against their answer stamps, see below)
└── (assembly itself makes no LLM call — pure code; the hero rewrite's wait (`phase:synthesis`), the quiz and moment fill run inside its `phase:assembly` wall)
```

For the assistant, one chat turn = one trace named `chat:{videoSummaryId}` (`chat:library` for library chat), tagged `videoSummaryId:<id>` and `youtubeId:<id>` (video chat), `requestId:<id>` and `turn:<n>` (the user messages in the history sent with the turn, plus one; the history is shared across surfaces, so a session that starts after the user chatted elsewhere does not start at `turn:1`). The web mints one session id per chat surface (one per video chat, one for the library chat; `sessionIds` in the `vie-chat` store, dropped with the transcript on "New chat" and logout) and sends it as `X-Session-Id` on every chat and action request; vie-api forwards it, so every turn of one surface — and every `/action` it sends — lands in the session `chat-<sessionId>`. The user id follows `LANGFUSE_USER_ID_MODE` and is never a tag. The agentic loop runs up to `MAX_TOOL_ITERS` rounds: each round is a `library_agent` span (the tool-selection generation; when the model calls no tool, its content is the answer), followed by one `tool:<name>` observation (type `tool`) per tool the model asked for, opened inside `execute_tool` as a sibling of the `library_agent` span under the turn's root (the LLM calls a tool makes, e.g. `organize_library`, nest under it; a tool that returns an error ends `WARNING` with the error as `statusMessage`). A `library_generation` span streams the answer only when tools are off (no vie-api client or no user) or the tool budget ran out — the same names for single-video RAG chat and library chat, since both run `run_agentic_loop`. Query translation logs as `query_translate`. `/action` dispatch opens its own trace (tags `action`, `action:{action}`, plus `videoSummaryId:` and `youtubeId:` for a video action and `requestId:`; no `turn:`) with an `action:{action}` span, in the chat session when the header is sent. A video the agent generates (`generate_video`) is dispatched with the chat's `X-Request-ID` and `X-Session-Id`, so its pipeline trace carries `chatSession:<sessionId>`. Each named LLM call is a generation opened before the request; a provider failure ends it `ERROR`, a stream the client abandons ends it `WARNING` (`client disconnected`). The tool router owns the per-tool `llm_feature_var` (`assistant:tool:<name>`, set/reset around dispatch so siblings aren't mislabelled); endpoints set `assistant:rag_chat` / `assistant:library_chat` / `assistant:action:<action>` plus the ledger ids below so assistant `llm_usage` rows are attributable.

```
Trace: chat:{videoSummaryId} | chat:library   (session chat-{sessionId}; tags videoSummaryId, youtubeId, requestId, turn:<n>)
├── generation: query_translate      (non-English questions)
├── span: library_agent              (round 1: tool selection; with no tool call its content is the answer)
│   └── generation: library_agent
├── tool: tool:<name>                (one per tool the round asked for; WARNING when the tool returned an error)
│   └── generation: …                (a tool's own LLM call, e.g. organize_library)
├── span: library_agent              (round 2 …)
│   └── generation: library_agent
└── span: library_generation         (only without tools, or after the tool budget ran out)
    └── generation: library_generation
```

### Ids: run, trace, session, request

| Id | Where it comes from | Where it shows |
|---|---|---|
| `runId` | vie-api mints one per dispatch (`dispatchPipeline`) and pushes a `dispatched` entry on `videoSummaryCache.runs[]`; the summarizer takes it over when the attempt starts (`running`). An attempt the api did not dispatch (worker retry, DLQ replay, `POST /regenerate`, a legacy message) or whose id already ran gets a fresh uuid with `retryOf` (`services/pipeline/run_registry.py`). The terminal status callback carries it and closes the entry. | tag `run:`, root metadata/input `runId`, `runs[]`, the `POST /api/videos` / `GET /api/videos/:id` responses |
| trace id | `trace_id_for_run(runId)` (Python, llm-common) = `traceIdForRun(runId)` (TS, api) = `Langfuse.create_trace_id(seed=runId)` | the Langfuse trace id; `runs[].traceId`; responses next to `runId` |
| session | `pipeline-{videoSummaryId}` (pipeline) / `chat-{sessionId}` (chat) | every attempt of one row / every turn and action of one chat surface |
| `sessionId` | the web, one uuid per chat surface (`X-Session-Id`); vie-api validates it with the request-id pattern (a bad value is dropped) and forwards it to the assistant, and into `dispatchPipeline` → `runs[].chatSessionId` → queue/HTTP `chatSessionId` for a video the agent generates | Langfuse session `chat-<id>`, pipeline tag `chatSession:<id>`, `llm_usage.session_id`, log field `sessionId` (pino) / `session_id` (structlog), Sentry tag `sessionId` (api) |
| `requestId` | the api request that dispatched | tag `requestId:`, `runs[].requestId`, `llm_usage.request_id` (billing settles by it, unchanged) |

Every `llm_usage` row also carries the ids of its attempt or chat (D9): `run_id`, `trace_id`, `attempt`, `pipeline_version`, `bypass` (pipeline rows) and `session_id` (chat rows, and pipeline rows started from chat), next to `request_id`, `video_id` (always the YouTube id) and `video_summary_id`. They come from `llm_common.context` (`USAGE_ID_VARS`): the queue worker sets all of them per job in `usage_scope(...)` and restores them when the job ends (a worker process runs many jobs, so a job's ids must never reach the next one's rows), the runner re-binds the attempt's ids once it resolved the run, `run_trace` binds its trace id, and the assistant's middleware binds the session id. The admin and vie-api build `(run_id, timestamp)` and `(session_id, timestamp)` indexes; the 90-day scrub removes the three new ids with `user_id`, `request_id` and `prompt_preview`.

### Admin "Open in Langfuse" deep-link

The vie-admin **backend** builds every link from stored ids, with no Langfuse API call (P-6, D10). `GET /usage/by-run` groups the summarizer's ledger rows by `run_id` (legacy rows by `request_id`), reads the run's `videoSummaryCache` doc (the same batched lookup as the degraded badge), takes the `runs[]` entry with the run's id (legacy rows: the newest entry with their `request_id`) and returns `{LANGFUSE_BASE_URL}/project/{LANGFUSE_PROJECT_ID}/traces/{traceId}` as `langfuse_url` (falling back to the rows' own `trace_id`) and `…/sessions/pipeline-{videoSummaryId}` as `session_url`. `GET /usage/by-session` returns `…/sessions/chat-{sessionId}` per chat session (`services/admin/src/services/langfuse.py`). **`LANGFUSE_PROJECT_ID` is required**: blank → `null` links, never an error. Runs dispatched before P0b (no `runs[]`, no `trace_id`) and GDPR-stripped entries get no trace link. Responses cached 30 s. The old tag lookup (`GET /api/public/traces?tags=requestId:…`, deprecated by Langfuse) and the `/api/public/projects` resolver are gone.

The admin UI renders only the links the backend returns (`RunsTable` / `SessionsPanel`: "Trace ↗", "Session ↗"); a `null` link renders nothing. The old build-time fallback (`lib/langfuse.ts`, `VITE_LANGFUSE_*`) is gone.

### Reading a trace

In the Langfuse UI:

- **Top of trace**: total cost, total latency, model mix, tags — open this first when triaging. The root span's metadata carries `transcriptSource`, `transcriptType`, `transcriptAttempted`, and `transcriptOutcome` (set by the runner right after the transcript phase, for failed runs too), so traces can be filtered by which transcript layer won and why paid ASR ran.
- **Generations panel**: the per-stage breakdown. Sort by latency or cost to find the bottleneck.
- **Input / Output panels** of a generation: the actual prompt and response, after redaction (emails, phone-shaped runs, plus a short allowlist of token formats — see below) and truncation (50KB cap with `[TRUNCATED]` marker; original length is not encoded, on purpose).
- **Metadata**: `attempt`, `useFastModel`, `modelOverride`, `finishReason`, `latencyMs`. `attempt>1` means a retry fired; `fallbackFrom` marks the cross-provider fallback; `refusal: true` marks a declined answer and `refusedModel` the previous model's answer for it (below). `promptVersions` maps prompt name → registry version for every prompt that was loaded inside the trace.
- **Scores**: faithfulness score plus comment "N/M grounded". Below 0.7 = quality regression suspect. From output-quality phase 2 the page's hero and quiz carry their own scores, `faithfulness_hero` and `faithfulness_quiz` ([below](#hero-and-quiz-sample-output-quality-24)).

### Prompt cache

- **Langfuse**: an Anthropic generation that wrote or read the prompt cache carries `cacheCreationTokens` / `cacheReadTokens` (and `cacheHit=true` on a read) in its metadata.
- **`pipeline.timing.llmCalls[]`** (Mongo, timing schema version 4): per call `cacheReadTokens` (Anthropic reads, or OpenAI's automatically cached input), `cacheWriteTokens`, and its lifetime split `cacheWrite1hTokens` / `cacheWrite5mTokens`; `inputTokens` is the whole prompt, cached part included; `ttftMs` = ms to the first streamed chunk (only the split extraction's warm-up call, `extraction_text1`, is streamed; null otherwise).
- **Per call type**: `python scripts/cache_hit_rate.py [--days N | --ids …]` aggregates those rows by span and provider — and, from timing version 4, the refused calls' `llmFailures` rows (billed, cache tokens included) — hit rate = reads ÷ (reads + writes) for Anthropic, cached share of input for OpenAI. Gate reports carry it from gate 2 on; the plan's static half is cached for 1 hour (pipeline-1min 2.0). Layout, prices and the 1h-vs-5m rule: [llm-cost-model.md](./llm-cost-model.md#where-cache_control-is-set).

### Refusals (pipeline-1min 4.0b)

A refusal is an HTTP 200 whose answer the model declined (`llm_common.refusal.refusal_of`: Anthropic `stop_reason: "refusal"` arrives as `finish_reason="content_filter"`; OpenAI's non-streamed `message.refusal`; any provider's `content_filter`). The call is asked once more on the refused model's previous model (`config.PREVIOUS_MODEL`) and never again on the refused one ([ERROR-HANDLING.md](./ERROR-HANDLING.md#refusals)). Where it shows:

- **Langfuse**: the declined answer's generation (`refusal: true`, `refusalCategory`), then the previous model's under the same span (`refusedModel`, `refusalCategory`); a warning log `<model> refused (span=…, category=…)`.
- **`pipeline.timing`** (version 4): the declined answer is an `llmFailures` row `error: "Refusal"` with its tokens, cache tokens and `costUsd` (counted in the run's `costUsd`); every row of the re-send carries `refusedModel` (but see the stage caps below); `counts.refusals` / `refusalFallbacks` / `refusalsUnanswered` ([DATA-MODELS.md](./DATA-MODELS.md)).
- **`llm_usage`**: `refusal: true` (+ `refusal_category`) on the declined answer's row, `fallback_from` = the refused model on the re-send's row ([llm-cost-model.md](./llm-cost-model.md#schema)). Both rows are `success: true` and priced.

`refusalCategory` / `refusal_category` stay null on LiteLLM 1.105.0: its chat path drops Anthropic's `stop_details`, and OpenAI sends no category. A streamed OpenAI refusal cannot be told from an empty answer through LiteLLM (its refusal deltas are dropped). Under the default config no pipeline OpenAI call streams — only the split extraction's warm-up streams, on Anthropic; with `LLM_FALLBACK_PROVIDER=openai` its cross-provider fallback attempt streams on gpt-4o (`on_first_chunk` streams every attempt).

The re-send rows carry `refusedModel` only under `call_deadline` (`LLMService.call_llm` / `call_llm_fast`). Frame vision's `asyncio.wait_for` and the description analysis' 30 s cap sit outside it: a re-send they cancel is recorded as a plain `TimeoutError` under the refused model, without `refusedModel`.

## Prompt registry

Local `.txt` files in `services/summarizer/src/prompts/**` are the source of truth at build time. `scripts/register_prompts.py` walks the tree and uploads each as a Langfuse prompt with this naming convention:

| Local path | Langfuse name |
| --- | --- |
| `prompts/base_extraction.txt` | `summarizer:base_extraction` |
| `prompts/extraction_group_job.txt` | `summarizer:extraction_group_job` |
| `prompts/extraction_group_rules.txt` | `summarizer:extraction_group_rules` |
| `prompts/schemas/food.txt` | `summarizer:schema:food` |
| `prompts/enrich_quiz.txt` | `summarizer:enrich_quiz` |
| `prompts/quiz_v2.txt` | `summarizer:quiz_v2` |
| `prompts/hero_rewrite.txt` | `summarizer:hero_rewrite` |
| `prompts/merge_rank.txt` | `summarizer:merge_rank` |
| `prompts/vision.txt` | `summarizer:vision` |
| `prompts/examples/learning.txt` | `summarizer:example:learning` |
| `services/assistant/src/utils/prompt_templates.py::RAG_HEADER` | `assistant:rag_header` |

Every pipeline phase loads its prompt through `load_prompt_text` (in `services/summarizer/src/services/pipeline/prompt_builder.py`), which is registry-first with file fallback. A successful registry fetch records the prompt's version in the active trace's `promptVersions` map — every subsequent generation span carries the link in its metadata so a "this batch was wrong" investigation can pin the exact prompt that ran. Paths outside `PROMPTS_DIR` bypass the registry, so ad-hoc files (tmp tests, dev fixtures) never get looked up. The SDK caches hits (60 s, background refresh) but never a miss, so `fetch_prompt_with_obj` remembers a miss (unregistered name, unreachable server) for `PROMPT_MISS_TTL_S` = 60 s per name: a prompt not yet registered (e.g. a deploy ahead of `vie-langfuse-init`) costs one synchronous GET + its backoff per minute, not one per load.

**Dev switch — `PROMPT_SOURCE=disk`.** Setting `PROMPT_SOURCE=disk` (default `registry`) makes `load_prompt_with_fallback` skip the registry entirely and read the local `.txt` fresh on every call (no process cache, chapter-detect prompt included), so a prompt edit applies to the next run without re-registering or restarting the worker. It is honoured only when `ENVIRONMENT` is a dev name (`""`, `development`, `dev`, `test`, `local`) — any other value (`production`, `prd`, `demo`, …) logs a warning once and keeps the registry. In disk mode no Prompt object is fetched, so traces carry **no `promptVersions`** and generations have no prompt link; never compare disk-mode traces against registry-mode ones by prompt version.

The uploader (`scripts/register_prompts.py`) refuses to ship any prompt that exceeds 200 KB, is empty/whitespace-only, or matches one of the secret-token patterns it knows about (`LANGFUSE_SECRET_KEY=...`, AWS, Anthropic, OpenAI, GitHub, JWT). This is a guardrail against accidental upload of a renamed `.env` or dump file.

Updating prompts:

```bash
# Edit the .txt file locally, then sync (uses the same .env as the runtime
# containers — guarantees the upload lands in the same project the pipeline
# reads from):
docker compose run --rm vie-langfuse-init

# Dry run from the host (the default mode; uploads nothing). With Langfuse keys
# in the environment it READS the labelled versions to report
# unchanged / would-upload / unknown; without keys every prompt is would-upload:
python3 scripts/register_prompts.py --dry-run [--label staging]

# Stage, then promote: upload under a non-production label (the runtime only
# fetches `production`, so nothing served changes), check it, then re-run with
# the default label to promote the same text:
python3 scripts/register_prompts.py --commit --label staging
python3 scripts/register_prompts.py --commit
```

`--label NAME` (default `production`) is the label uploads receive and the one the idempotency check compares against: a new version is created only when the server version carrying that label differs from the local file. If a prompt exists but lacks the label, the script re-uploads to apply it — so partial earlier runs heal on the next sync. A lookup that fails for any reason but a 404 reports `unknown`; the exit code is **1** when any prompt is `unknown` or failed to upload (so `scripts/activate_langfuse.sh`, which runs under `set -e`, stops at step 5's dry run on a bad key or an unreachable host).

**Registration is a deploy step.** The loader's registry guard (`pipeline/prompt_registry.py`) only catches placeholder drift: a wording-only edit to a shipped `.txt` (stage prompt, schema, example, toolkit) keeps the same slots, so the registry's `production` version — the old wording — is served until the file is registered. `vie-langfuse-init` runs `register_prompts.py --commit` on every `docker compose up` (both compose files); a deploy that skips it must run the script itself.

## Faithfulness judge

After extraction completes, a fire-and-forget task samples 20% of extracted items (capped at 6 per video, deterministic by `youtubeId`) and asks the judge model (`LLM_JUDGE_MODEL` = `openai/gpt-6-luna`, reasoning off; blank = the fast tier — gpt-4o-mini with `LLM_FAST_PROVIDER=openai`, else Haiku 4.5) "is this claim supported by the transcript?" — the excerpt may end with the rendered `<visual_annotations>` block, so on-screen facts count as support (frame text no longer sits in the transcript). The aggregate `grounded / total` ratio is logged as a Langfuse score named `faithfulness`.

The check is **informational only** — it never blocks the pipeline or fails a video. Use the Langfuse dashboards to track drift over time. A run below 0.7 is logged at warning level but otherwise ignored. The judge model is part of the score, and gpt-6-luna is the stricter judge: in the 4.0d model A/B it graded 0.44 of the same 18 claims grounded vs gpt-4o-mini's 0.89 (8 flipped, never the reverse — pipeline-1min gate g4-model-ab); re-sent the golden baseline's recorded judge requests, it flipped 26 of 108 verdicts to ungrounded and 2 the other way (mostly claims that add unstated detail to a grounded core). So the move (pipeline-1min close-out) came with a re-baselined golden: `dev/golden-dataset/noise.json`'s faithfulness parts are the luna re-score (its `faithfulnessJudge` block; `gates/g4.md` §10). Rollback: `LLM_JUDGE_MODEL=openai/gpt-4o-mini` together with the previous `noise.json` (git history).

**Claim-localized context (not a head slice).** Transcripts within the 80K-char judged-context budget pass through whole. Longer transcripts are split into ~4K-char windows; per claim, the top BM25-style (IDF-weighted lexical overlap) windows are packed into the budget in document order with gaps marked, plus immediate neighbors of scoring windows. A claim from hour 3 of a long video is therefore judged against hour-3 text — the old fixed 80K-char head slice mis-verdicted tail claims of 3h+ videos as hallucinations. Falls back to the head slice only when a claim shares zero vocabulary with the transcript. See `_select_claim_context` in `faithfulness_context.py` (split out of `faithfulness.py` by output-quality 2.4); regression tests in `tests/test_faithfulness.py` include a synthetic tail-claim case past the 80K boundary.

**Judge accuracy (validated 2026-07-12).** The judge (`_judge_one` in `faithfulness.py` + `_select_claim_context`, Haiku fast tier) was run live over the 20-claim labeled fixture `services/summarizer/tests/fixtures/faithfulness_labeled_claims.json` (12 supported / 8 hallucinated, synthetic transcript): **20/20 = 100% accuracy, 0 judge errors**. The dataset is deliberately easy (explicit statements vs. flat contradictions/absences); the regression floor asserted by the eval is 80%. Re-run any time with:

```bash
cd services/summarizer && LIVE_EVAL=1 .venv/bin/python -m pytest -q tests/test_faithfulness_live_eval.py -s
```

(~20 calls on the configured judge model — ≈ $0.003 on gpt-6-luna, which scored 20/20 on 2026-10-14; ≈ $0.02 on Haiku 4.5 in 2026-07; the fixture's structural checks run in the normal suite for free.)

### Hero and quiz sample (output-quality 2.4)

`faithfulness` scores only the extraction sample above. From phase 2 the judge also reads what a viewer meets first on the page, in **up to two more background calls** on the same judge model (`LLM_JUDGE_MODEL`, stage `faithfulness`, ledger feature `summarize:faithfulness`; `pipeline/faithfulness_hero_quiz.py`). `pipeline_orchestration` launches them once `run_phase_assembly` returns — the late group has written the hero and the quiz — through `routes/pipeline_faithfulness._launch_hero_quiz_check` (`pipeline_orchestration._launch_judges`, stage `assembly`), and drains them with the extraction sample (≤ 25 s before the trace flushes). Each half is judged only when its late call wrote it: the hero sample only when the hero rewrite wrote the hero (`wrote_hero(ctx.synthesis_dict)`; else the page shows memory's takeaways), the quiz sample only when `QUIZ_V2_ENABLED` (`judge_hero` / `judge_quiz` of `hero_quiz_samples`), so a run with both flags off makes no extra judge call:

| Score | What is judged | Window |
|-------|----------------|--------|
| `faithfulness_hero` | every takeaway of `meta.keyTakeaways` that closes with a `[m:ss]` marker (at most 7; `hero_sample`) against the transcript around that marker and what the screen shows there. The stamp is part of the claim: a point the video states only elsewhere reads as ungrounded | 30 s before to 90 s after the marker (`WINDOW_BEFORE_S`, `WINDOW_AFTER_S`) |
| `faithfulness_quiz` | `QUIZ_SAMPLE` = 2 stamped questions of the shipped `quiz_arena` tab, drawn by `youtube_id` and kept in page order (`quiz_sample`): does the evidence at the question's `timestamp` give the keyed option as the answer? | the same window around the question's `timestamp` |

- Each call logs `grounded ÷ judged` with a comment ("N/M … grounded", the judge errors and the entries that could not be judged — a takeaway without a marker, a question without a stamp or a valid key). A sample with no usable verdict logs no score. Below 0.7 logs a warning, like the extraction sample.
- One attempt per call (`max_retries=0`, 20 s); the score is informational and never blocks the run. A doc without a stamped takeaway or a stamped question skips that call, and so does a half whose flag is off (above); `LANGFUSE_FAITHFULNESS_SAMPLE_RATE=0` skips both, any other rate leaves the two samples as they are (the rate scales only the extraction sample). A run with no timed transcript skips both.
- **Cost, bounded by construction:** every prompt piece is capped (`HERO_PROMPT_MAX_CHARS` = 22 000, `QUIZ_PROMPT_MAX_CHARS` = 10 000; output `HERO_MAX_TOKENS` 700, `QUIZ_MAX_TOKENS` 300), so the two calls cost at most ≈ $0.0021 at 2 characters a token (≈ $0.0013 at English's ~4); the module estimates ≈ $0.0006 for a 5-takeaway video, and the 2.4 lane measured ≈ $0.0004 per video over the 13 gate-1 docs (the output-quality task, 2026-10, step 2.4).
- The two generations are named `faithfulness` like the extraction sample's; read the score name, not the generation, to tell the samples apart.

Tuning:

- `LANGFUSE_FAITHFULNESS_SAMPLE_RATE=0` disables the judge entirely (the hero and quiz calls included).
- `LANGFUSE_FAITHFULNESS_SAMPLE_RATE=1.0` checks every claim (capped at 6/video). Useful when debugging a quality regression.
- Cost: ≈ $0.002–0.003 per video on gpt-6-luna at the default rate (golden set, 2026-10).
- `_TRANSCRIPT_BUDGET_CHARS` (80K) is coupled to the judge model's context window — see the warning comment in `faithfulness_context.py` before pointing the faithfulness stage at a smaller-context model.

## Golden dataset + eval

`dev/golden-dataset/videos.yaml` lists 20 hand-curated videos with expected tabs, components, and key content terms. Two scripts work with it. Run them **inside the summarizer container** so they pick up the same env (Langfuse keys, LLM provider keys) as the runtime, and so the in-cluster `vie-api` hostname is reachable:

```bash
# 1. Prompts: auto-synced on `docker compose up` by vie-langfuse-init.
#    To force a manual resync after editing a prompt file:
docker compose run --rm vie-langfuse-init

# 2. One-time: upload the dataset to Langfuse for visual diffing
docker compose exec vie-summarizer python scripts/build_golden_dataset.py --dataset-name vie-golden-v1

# 3. Per-release: run the pipeline against every entry and score the output.
#    --api-url override is required from inside the container — the script's
#    default of http://localhost:3000 would hit the summarizer itself instead
#    of the API. Real run: ~60-90 min, ~$3-5 in LLM cost.
docker compose exec vie-summarizer python scripts/run_eval.py \
  --api-url http://vie-api:3000 \
  --output reports/ --fail-under 0.7

# Smoke-test the wiring without spending any LLM budget:
docker compose exec vie-summarizer python scripts/run_eval.py --dry-run
```

Host-side invocation (`python3 scripts/run_eval.py ...`) also works against a locally running stack — `--api-url` then defaults correctly to `http://localhost:3000`.

Scoring (`scripts/run_eval.py`):

- **tab_count_score** (×0.2) — within ±1 of expected
- **component_coverage** (×0.4) — every required component appears
- **content_coverage** (×0.3) — ≥80% of expected terms appear in assembled tabs
- **empty tabs** (×0.1) — every assembled tab has populated required lists

The reports land under `reports/eval-{timestamp}.csv` + `.md`. The CI-friendly `--fail-under` flag exits non-zero when the average drops below the threshold.

Dry-run mode (`--dry-run`) substitutes a perfect-response stub for the live pipeline, so the scoring code path can be smoke-tested without making any network calls. Used by the test suite.

Langfuse I/O (`scripts/_eval_langfuse.py`, SDK v4 public-API client, `langfuse>=4.7,<5`): each entry's trace is the one `POST /api/videos` named (`traceId`, P0b; a `--resume` row takes it from `GET /api/videos/:id`); a report from before P0b falls back to the newest trace named `pipeline:<videoSummaryId>` (a v2 `traceName` filter, so SDK v2 and P0a traces alike). Signals come from `api.observations.get_many(trace_id=…)` (the probe format) and `api.scores_v3.get_many_v3(trace_id=…, name="faithfulness")`, never the deprecated `GET /api/public/traces`; every read goes through `ReadPacer` with the SDK's own retries off. `--publish-run` queues `golden.*` scores with `create_score(trace_id=…)` and links dataset run items with `api.dataset_run_items.create(trace_id=…)`.

### CI wiring + committed baselines

`.github/workflows/eval.yml` (project-score-9 3.1/3.2) is the eval gate:

- **PR** (paths: `src/prompts/**`, pipeline/vector code, `scripts/run_eval.py`, `dev/golden-dataset/**`): a zero-spend `--dry-run --fail-under` gate plus the retrieval recall@k/MRR floor against an ephemeral Qdrant service container (see docs/RAG.md §Retrieval eval).
- **Weekly** (Mondays 03:00 UTC) + `workflow_dispatch`: full live golden-dataset run against a composed stack, `--fail-under` gated, `--publish-run weekly-YYYYMMDD` to Langfuse. Skips cleanly when spend secrets (`ANTHROPIC_API_KEY`, `EVAL_USER_PASSWORD`, Langfuse keys) are not configured.

Committed metric baselines live in `dev/golden-dataset/baseline.json` — retrieval eval (recall@3 1.000 / MRR 0.865, 2026-07-12), faithfulness-judge accuracy (20/20 on gpt-6-luna, 2026-10-14), and the golden-eval average (currently `deferred-for-spend` with the exact production procedure recorded in the file; the workflow reads its `failUnder` floor either way).

## Operational watch-outs

- **Third-party data flow**: enabling Langfuse ships prompts and outputs to whatever host `LANGFUSE_BASE_URL` points at (cloud.langfuse.com by default). Treat that flow like any other external dependency.
- **Redaction model**: `llm_common.observability.redaction.redact_pii` strips a short fixed list of patterns — emails, long phone-shaped digit runs, JWTs (`eyJ...`), AWS access-key IDs (`AKIA…`/`ASIA…`/…), Anthropic keys (`sk-ant-…`), OpenAI keys (`sk-…`), GitHub PATs (`ghp_/ghu_/ghs_/gho_/ghr_…`), and `Bearer <token>` headers. Names, addresses, account numbers and free-form PII are **not** detected. If your compliance posture needs more, extend the pattern list in one place and re-run the test suite. The same redactor runs recursively over Anthropic structured content blocks (frame-vision OCR text included).
- **User-id propagation**: by default the internal user id is forwarded on the trace (helpful for support debugging). Set `LANGFUSE_USER_ID_MODE=hash` to forward a deterministic 16-char SHA-256 derivative instead (combine with `LANGFUSE_USER_ID_HASH_SALT` so a leaked trace export isn't trivially correlatable), or `LANGFUSE_USER_ID_MODE=omit` to strip the id entirely.
- **Payload size**: prompts/outputs are truncated at 50KB before upload (`truncate_payload`). The truncation marker is a fixed string — the original length is deliberately not encoded so a downstream viewer can't infer pre-redaction size from the trace.
- **Free-tier ceiling**: 50K observations/month covers ~500 pipeline runs/month at current span density. If you exceed it, either drop to per-stage tracing (skip per-batch extraction spans) or upgrade to a paid tier.
- **SDK absent**: `langfuse` is a hard dependency of llm-common, so every install has it; an `import langfuse` failure is still tolerated (the layer no-ops) and a process without keys never imports it.
- **Async lifecycle**: the SDK exports on its own thread; `flush()` (end of every run) and `shutdown()` (process exit) run inside `asyncio.to_thread` to avoid blocking the event loop. Don't replace this with a direct sync call.
- **Cache hits**: a Redis-served fast path opens no trace; the api's request logs and the admin count them.
- **`mark_step` sub-steps** (`frames.scene_detect`, `frames.upload`, …) are recorded after the fact, so they live in `pipeline.timing` only; v4 cannot backdate a span.
- **Eval URL allowlist**: `scripts/run_eval.py` rejects URLs whose host isn't on the YouTube allowlist — protects against the eval being repointed at an internal host via a PR.

## File layout

```
packages/llm-common/src/llm_common/observability/
├── client.py                # init/shutdown, run_trace, span, generation/finish_generation, score, prompts
├── redaction.py             # redact_pii, truncate_payload, structured-content redaction
├── prompts.py               # active prompt versions + the registry miss cache
└── ids.py                   # trace_id_for_run, the user-id policy

services/summarizer/src/services/observability/__init__.py   # re-exports, init_langfuse() bound to settings
services/summarizer/src/services/llm_telemetry.py            # open/finish a provider call's generation

services/assistant/src/services/observability/
├── __init__.py              # re-exports, init_langfuse() bound to settings
└── traces.py                # chat_trace: the trace of one chat turn

services/summarizer/src/services/pipeline/faithfulness.py   # LLM-as-Judge (the extraction sample)
services/summarizer/src/services/pipeline/faithfulness_context.py      # claim-localized transcript windows
services/summarizer/src/services/pipeline/faithfulness_hero_quiz.py    # the hero + quiz sample (2.4)
services/summarizer/src/routes/pipeline_faithfulness.py                # background spawn + drain of both
scripts/register_prompts.py        # sync .txt → Langfuse
scripts/cache_hit_rate.py          # prompt-cache hit rate per call type (pipeline.timing)
scripts/build_golden_dataset.py    # upload videos.yaml → Langfuse dataset
scripts/run_eval.py                # run pipeline on golden + score
dev/golden-dataset/videos.yaml     # 20 curated entries
```

## Request tracing & Sentry

Every HTTP request to the API gets a UUID v4 stamped on the `x-request-id` response header. That id flows end-to-end:

- **API → frontend**: header echoed on every response (set in `api/src/plugins/request-id.ts`).
- **API → RabbitMQ**: `requestId` field on the queue payload (`api/src/services/queue-topology.ts`).
- **API → summarizer HTTP fallback**: `X-Request-ID` header on `triggerSummarization` (`api/src/services/summarizer-client.ts`).
- **API → assistant**: `X-Request-ID` + `X-User-Id` headers on `/chat`, `/library/chat`, and `/action` (`api/src/services/assistant-client.ts`). The assistant binds them onto the LLM cost-tracking ctxvars so its `llm_usage` rows carry `request_id` + `user_id` (without `X-User-Id`, RAG-chat rows land unattributed).
- **Worker**: the runner binds `request_id`, `video_summary_id`, `youtube_id`, `user_id`, `attempt` to structlog contextvars before driving the pipeline. Every log line and Langfuse trace tag inside the pipeline carries them automatically.
- **Assistant**: `add_request_context_middleware` reads the header (or generates one) and binds it on contextvars.

### Log line shape

JSON logs in every service carry, at minimum:

```json
{
  "timestamp": "2026-05-20T10:30:00.000Z",
  "level": "info",
  "service": "vie-api | vie-summarizer | vie-summarizer-worker | vie-assistant | vie-admin",
  "requestId": "<uuid>",
  "userId": "<optional>",
  "videoSummaryId": "<optional>",
  "stage": "<optional>",
  "msg": "<event name>"
}
```

The `service` field is stamped by a structlog processor (or pino `base`) so log aggregators can filter without callers having to mention it. Other fields appear only when bound on contextvars (Python) or attached to `req.log` (Node): vie-api binds `sessionId` on every request logger that carries a valid `X-Session-Id`; the Python services bind `request_id`, `session_id` (middleware), `run_id` (worker + runner), `video_summary_id`, `youtube_id`, `user_id`, `attempt` (worker).

**Python: structlog through stdlib (D13).** Every Python service (summarizer, its worker, assistant, admin) configures logging through `llm_common.logging_setup.configure_logging` (the admin through its vendored copy `src/logging_bridge.py`): structlog events are handed to the standard library (`structlog.stdlib.LoggerFactory`), so they and every `logging.getLogger` record — the code's, uvicorn's, the libraries' — go to one root handler on stdout and render through one chain: `timestamp`, `level`, `service`, the bound contextvars (Python names: `event`, `request_id`, `run_id`, `session_id`, `video_summary_id`) and, on stdlib lines only, `logger` — JSON with `LOG_FORMAT=json` (UTF-8, not `\u`-escaped), a timestamped console line otherwise; console tracebacks are plain (no frame locals). The stdlib record's message is the event name and an exception rides on the record, so Sentry's logging integration sees structlog `ERROR` events, groups them by event and keeps their stack; the rendered line is the same as before. Not covered: LiteLLM's own stderr handler (its WARNING+ lines also print once more, unformatted) and uvicorn's `--reload` supervisor.

**Run and chat events** (one JSON line each; the Grafana dashboards and the failure-rate alert read them with LogQL):

| Event | Service | Fields |
|---|---|---|
| `pipeline_run_started` | vie-summarizer, vie-summarizer-worker | `run_id`, `video_summary_id`, `youtube_id`, `path` (`worker`/`sse`/`dev`), `attempt`, `bypass`, `retry_of` |
| `pipeline_run_finished` | vie-summarizer, vie-summarizer-worker | `run_id`, `video_summary_id`, `youtube_id`, `path`, `status` (`completed`/`failed`/`cache_hit`/`interrupted`), `cache_hit`, `duration_ms`, `error_code` |
| `chat_turn_finished` | vie-assistant | `session_id`, `surface` (`video`/`library`), `turn`, `status` (`completed`/`error`/`failed`/`interrupted`), `duration_ms`, `tool_calls` |

A cache hit writes only `pipeline_run_finished` (`status: cache_hit`). The stall sweeper's `pipeline_stalled video=…` error line stays a plain stdlib line.

### Cross-system lookup

Given a `request-id`:

| Where | How |
|-------|-----|
| API pino logs | `grep '"requestId":"<id>"'` in service stdout (or your log aggregator) |
| Summarizer / assistant structlog | same — the id is on every line via contextvars |
| Worker structlog | same — bound on entry, cleared on exit |
| Langfuse trace | search traces by tag `requestId:<id>` |
| Sentry event | search events by tag `requestId:<id>` |

Log lines and Langfuse also take a run id (`runId` on pino lines, `run_id` on structlog lines from the worker and the runner; Langfuse tag `run:<id>`) and a chat session id (`sessionId` / `session_id`; Langfuse session `chat-<id>`); Sentry tags them `runId` and `sessionId`. Or run `./scripts/find-request.sh <request-id | run-id | session-id>` from the repo root — it greps the docker logs of vie-api, vie-summarizer, vie-summarizer-worker, vie-assistant and vie-admin.

### Sentry init

`@sentry/node` is installed in the API; `sentry-sdk[fastapi]` in summarizer + assistant. All three services share the same env contract:

```
SENTRY_DSN=                       # empty -> SDK no-ops
SENTRY_ENVIRONMENT=production     # defaults to NODE_ENV / ENVIRONMENT
SENTRY_RELEASE=                   # usually the deploy SHA
SENTRY_TRACES_SAMPLE_RATE=0.1     # 0.0–1.0; 0 disables performance traces
```

The shared `llm_common.sentry_init` module hosts the Python init + `before_send` PII filter; the Node side has the same logic in `api/src/plugins/sentry.ts`. Both:

- Strip sensitive headers: `Authorization`, `Cookie`, `Set-Cookie`, `X-Internal-Secret`, `X-Admin-Key`, `X-CSRF-Token`, `X-API-Key`, `Proxy-Authorization`.
- Scrub URL query strings for credential params (`token`, `access_token`, `refresh_token`, `id_token`, `code`, `state`, `api_key`, `apikey`, `key`, `password`, `secret`) on `request.url`, `request.query_string`, and the `route` tag. Needed because the WebSocket upgrade path puts the JWT in `?token=`.
- Drop `user.email`, and drop `user.id` / `user.username` if either embeds an email.
- Recurse the email redactor over `request.data`, `extra`, `contexts`, `breadcrumbs[*].data`, and `exception.values[*].value` (so a logger that includes a body or stack message can't leak addresses).
- Promote the `request_id` contextvar / `req.id` to a Sentry tag named `requestId`; the Python scrub (and `capture_exception_with_context`) also promote `run_id` → `runId` and `session_id` → `sessionId`, and vie-api tags `sessionId` from a valid `X-Session-Id` header.

vie-admin keeps its own `sentry_sdk.init` (it does not install llm-common, plan A3) with `before_send` = a verbatim copy of the shared scrub (`services/admin/src/sentry_scrub.py`, the block between the `scrub` markers in `sentry_init.py`); `tests/test_vendored_copies.py` fails the admin suite when the two drift. Structlog `ERROR` events in every Python service reach Sentry through the default logging integration (see [Log line shape](#log-line-shape)).

The `X-Request-ID` header is validated on both sides — `^[A-Za-z0-9_-]{8,128}$`. Forged headers (newline injection, control chars, semicolons) fall through to a fresh UUID so they cannot pollute log lines or Sentry tag space.

Worker DLQ failures fire one Sentry event per dead-lettered job — retries don't (alert noise). Independently, an **unclassified** pipeline exception (the generic `except Exception` in `pipeline_runner.stream_summarization` — the branch where real bugs land) is captured at the source with the real traceback, tagged `outcome=pipeline_failed`, `errorRef=<ref shown to the user>`, `path=worker|sse-direct` (+ `attempt` under the worker). Before this the SSE-direct path (dev override, or an SSE client winning the producer lock) had no capture point at all. Classified failures (transcript, rate-limit, timeout, provider error) stay log-only.

Browser errors go to a **separate** Sentry project via `VITE_SENTRY_DSN` (+ `VITE_SENTRY_RELEASE`), baked in at build time (`apps/web/src/main.tsx`; compose build args). Empty DSN = no-op.

The API's `onError` hook only captures errors whose effective status is ≥500. Status is resolved via `effectiveStatusCode` across three shapes: Fastify-native `.statusCode` (rate limit, schema validation), project `AppError` subclasses (`NotFoundError`, `ValidationError`, etc.) via `.status`, and `ZodError` thrown by route-boundary `.parse()` calls (always 400). Without all three checks, expected client outcomes would land in Sentry as 500-class noise.

### 4xx logging + stdout redaction (API)

Every error funneled through the global handler is logged: 5xx at `error` with the stack, everything else at `warn` as `request rejected` with `statusCode`, `errorCode`, `errMessage` (the client-facing message, not the raw error), `method`, `route`, `userId` (when authenticated), and the request's `requestId`. Previously 4xx branches returned before any log call, so a client-reported 400/401/429 was undiagnosable without reproducing it. The status→envelope mapping is the pure `resolveErrorEnvelope()` in `api/src/utils/error-envelope.ts`.

pino `redact` (`LOG_REDACT_PATHS` in `api/src/app.ts`) is built from the **same header list the Sentry scrubber uses** — `authorization`, `cookie`, `set-cookie`, `x-internal-secret`, `x-admin-key`, `x-csrf-token`, `x-api-key`, `proxy-authorization` — matched under Fastify's `req.headers` serializer *and* a bare `headers` object logged by hand, plus `password` / `token` / `accessToken` / `refreshToken` / `apiKey` both top-level and one level deep (pino's `*` matches exactly one level). `defaultLoggerOptions()` is exported so the redaction test runs against the real production logger config rather than a copy.

## Grafana Cloud (logs, metrics, dashboards, alerts)

The whole box is watchable in a **Grafana Cloud Free** stack (D12; 10k active series, 50 GB logs, 14-day retention, alerting included). One container ships everything: `vie-alloy` (profile `monitoring`, `ops/alloy/config.alloy`; [INFRASTRUCTURE.md](./INFRASTRUCTURE.md#monitoring-grafana-alloy)).

- **Logs (Loki):** every `vie-*` container's stdout, labels `service`, `level` (`trace debug info warn error fatal`; pino numbers and structlog names mapped) and `environment`. Query with LogQL, e.g. `{service="vie-summarizer-worker", environment="production"} | json | event="pipeline_run_finished" | status="failed"`.
- **Metrics (Prometheus):** host (`node_*`: CPU, load, memory, filesystems), per container (`container_*`, label `name`: CPU, memory vs limit, disk, network — the box's traffic is their sum; restarts = `changes(container_start_time_seconds[…])`), RabbitMQ per queue (`rabbitmq_detailed_queue_messages`, `…_consumers`, label `queue`). A keep-list holds the stack to ≤ 3k active series; no Prometheus client library in app code — rates and durations come from LogQL over the run and chat events.
- **Dashboards** (`ops/grafana/dashboards/`, folder `vie`): **Box** (RAM, CPU, disk, network, per-container memory vs limit, restarts, error-log rate), **Pipeline** (runs/h, cache hits, p50/p95 duration, failures and rate by `error_code`, stalls, jobs/DLQ depth, worker consumers), **Chat** (turns/h by surface, errors, tool calls, p95 turn duration). Each has `env` (production/development) and datasource variables.
- **Alerts** (`ops/grafana/alerts/vie.json`, on `environment="production"` unless pushed with `--environment`): container restart loop (3+ restarts in 15 min), host RAM > 90 % for 10 min, container RAM > 90 % of its limit for 10 min, no consumer on `vie.pipeline.jobs` for 5 min, DLQ not empty for 5 min, pipeline failure rate > 30 % over the last hour (≥ 3 runs), and more than 8k of the free tier's 10k active series for 30 min (`grafanacloud_instance_active_series` from the stack's `grafanacloud-usage` datasource; left out when the stack has none). Missing data never fires (no page before prod ships metrics). All route to the Telegram contact point `vie-telegram`, built from `ALERT_WEBHOOK_URL` (`https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>`). The admin's own alert evaluator (Mongo `llm_alerts`, cost and failure alerts) keeps running unchanged beside them.
- **Push:** `ops/grafana/push.sh` upserts the folder, dashboards, contact point, root notification policy and the `vie` rule group by uid (`GRAFANA_CLOUD_URL`, `GRAFANA_CLOUD_API_TOKEN` = a service-account token with role Editor; `--dry-run` sends nothing). `--test-alert` pushes the DLQ rule with a threshold that fires whenever the queue metric exists (one forced Telegram message; with `--environment development` before prod ships data); run it again without the flags to restore. A failed call prints Grafana's validation message. On a new stack the first run creates the folder and waits up to 2 min for it: Grafana answers 403 (not 404) for a missing folder, and the token's cached permissions take about a minute to include a new one. Each call times out after 60 s and retries twice (every call is idempotent by uid).

LLM cost per day, calls by feature and p95 latency stay in the admin UsagePage (Grafana Cloud cannot read the box's Mongo; plan R2).

## Alerts & self-healing

| Signal | Where it runs | What it does |
|--------|---------------|--------------|
| `high_cost_call`, `daily_spend_spike`, `daily_global_cost`, `high_failure_rate`, `backup_stale` | llm-common callback (inline) / admin `alert_evaluator` (5 min) | Mongo `llm_alerts` + POST to `ALERT_WEBHOOK_URL`. `daily_global_cost` = Σ `cost_usd` since UTC midnight ≥ `ALERT_DAILY_COST_THRESHOLD_USD` (default $20, once per UTC day, alarm only — blocks nothing). Thresholds: [ERROR-HANDLING.md](./ERROR-HANDLING.md#alert-thresholds) |
| `pipeline_stalled` | summarizer stall sweeper (`src/services/stall_sweeper.py`, HTTP process, 5 min) | `processing` row idle > 30 min **and** producer lock gone → `failed` + status callback + alert. The flip is a compare-and-set (`mark_stalled_failed`: still `processing` and still stale), so a run revived between find and write is skipped with no side effects. Lock check fails open on Redis errors |
| worker retry backoff | `worker/runner.py` | 5 → 10 → 20 s (×2 per attempt, cap 120 s) before republish; message stays unacked during the wait |

**Nothing is delivered until `ALERT_WEBHOOK_URL` is set** — the evaluators run regardless, but alerts land only in Mongo. Any HTTP(S) JSON receiver works (Slack incoming webhook, Discord webhook, ntfy topic).

## Related docs

- [llm-cost-model.md](./llm-cost-model.md) — local cost ledger (complement, not replacement)
- [CROSS-CUTTING.md](./CROSS-CUTTING.md) — request-id propagation; Langfuse `traceId` aligns with the Sentry transaction id by design
- [SERVICE-SUMMARIZER.md](./SERVICE-SUMMARIZER.md) — pipeline phases that the trace structure mirrors
