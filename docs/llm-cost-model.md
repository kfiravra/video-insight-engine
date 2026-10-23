# LLM Cost Model

How VIE accounts for LLM token spend, including Anthropic prompt-caching credit.

## Recording

Every LiteLLM call writes one row to MongoDB `llm_usage` via
`MongoDBUsageCallback._build_record` (`packages/llm-common/src/llm_common/callback.py`).
The callback is registered as `litellm.callbacks = [callback]` on service startup
and is the single source of truth for cost — there is no manual tracking path
in the hot loop.

## Schema

`UsageRecord` (`packages/llm-common/src/llm_common/models.py`):

| Field                     | Source                                              | Notes |
|---------------------------|-----------------------------------------------------|-------|
| `model`                   | `kwargs["model"]`                                   | The model string LiteLLM hands the callback — on 1.105 the bare name (`claude-sonnet-5-5`), the provider kept apart in `custom_llm_provider` |
| `provider`                | derived from `model`'s `provider/` prefix           | `anthropic` / `openai` / `gemini` / `unknown` — a bare name has no prefix, so LiteLLM 1.105 rows read `unknown` (the cache-rate lookup below was fixed for the same cause; this field was not) |
| `tokens_in`               | `response.usage.prompt_tokens`                      | Includes uncached + cache-create + cache-read |
| `tokens_out`              | `response.usage.completion_tokens`                  | |
| `cost_usd`                | `litellm.completion_cost(response)`                 | Provider-billed cost, already net of cache discount |
| `cache_creation_tokens`   | `usage.cache_creation_input_tokens`                 | Tokens that wrote into the cache this call |
| `cache_read_tokens`       | `usage.cache_read_input_tokens`, else `usage.prompt_tokens_details.cached_tokens` | Tokens served from the cache this call — Anthropic reads, or OpenAI's automatically cached input (read since p4.0d) |
| `cache_hit`               | `_hidden_params.cache_hit` or `cache_read > 0`      | Either signal triggers True |
| `cache_savings_usd`       | `compute_cache_savings_usd(<provider>/<model>, cache_read)` | See below |
| `refusal`                 | `llm_common.refusal.refusal_of(response)`           | True on a declined answer (HTTP 200: Anthropic `stop_reason: "refusal"` → `finish_reason="content_filter"`, OpenAI `message.refusal`). The row stays `success: true` and priced (p4.0b) |
| `refusal_category`        | the refusal's `stop_details.category`               | Always `None` on LiteLLM 1.105 (its chat path drops `stop_details`) |
| `fallback_from`           | `llm_refused_model_var`                             | The refused model's id, on the row of its one re-send on the previous model (`config.PREVIOUS_MODEL`); `None` otherwise — the cross-provider fallback (D4) is not tagged here (`pipeline.timing` `fallbackUsed` covers both) |
| `feature` / `video_id`    | `llm_feature_var` / `llm_video_id_var`              | Set by call sites via context vars |
| `user_id`                 | `llm_user_id_var`                                   | Owner of the run; the key per-user reconciliation matches on. `None` on legacy rows |
| `video_summary_id`        | `llm_video_summary_id_var`                          | The Mongo `videoSummaryCache` id for this run |
| `request_id`              | `llm_request_id_var`                                | Per-run grouping key — one POST = one `request_id`; a regeneration gets a fresh one. `None` on legacy rows |
| `service`                 | callback ctor arg                                   | `summarizer` / `assistant` / etc. |
| `is_stream`               | `kwargs["stream"]`                                  | True when LiteLLM was streamed |
| `unit`                    | emitter                                             | `tokens` (default) or `audio_seconds` for transcription rows |
| `audio_seconds`           | emitter                                             | Billed audio duration for Whisper rows; `0.0` for token-priced rows |

## Transcription cost tracking

Whisper and Gemini transcription call provider SDKs directly (OpenAI /
`google.genai`), bypassing LiteLLM — so `MongoDBUsageCallback` never fires for
them. Without explicit tracking those calls are silently `$0`, which is material:
Whisper-1 bills `$0.006/min`, so a 1-hour audio-fallback video is ~`$0.36`.

The transcribers emit their own rows via `llm_common.record_manual_usage`, which
writes through the **same active buffer** the callback owns
(`register_active_buffer`, set in `MongoDBUsageCallback.__init__`) and fills
run-attribution (`user_id` / `video_id` / `video_summary_id` / `request_id`) from
the same context vars the pipeline already set — so transcription rows inherit
the run's attribution for free. The shared emit helper
(`services/summarizer/src/services/transcription/usage.py`) also logs a
`transcription:<provider>` generation under the pipeline's Langfuse trace.

- **Whisper** (`whisper_transcriber.py`) — `unit="audio_seconds"`,
  `audio_seconds=response.duration`, cost via `compute_transcription_cost_usd`
  (`$0.006/min`). Features: `summarize:transcript:whisper` /
  `:whisper_translate` — the latter only for the English text of a non-English
  run that transcribed its own audio with Whisper (its Qdrant points). Every
  other non-English transcript gets that English text from a text translation
  on `LLM_TRANSLATION_MODEL` (gpt-6-luna): a LiteLLM call, so an ordinary callback row with feature
  `summarize:rag_translation` (span `rag_translation`) — see
  [RAG.md](./RAG.md#non-english-transcripts).
- **Gemini** (`gemini_transcriber.py`) — `unit="tokens"`, token counts from
  `response.usage_metadata`, cost via the per-token Gemini rate. Feature:
  `summarize:transcript:gemini`.
- A failed transcription emits `success=False`, `cost_usd=0` (failed provider
  calls bill nothing). Tracking is best-effort and never breaks transcription.

Rates live in `_TRANSCRIPTION_RATES_USD` (`models.py`); an unmapped model logs
`transcription.rate_missing` once and costs `$0` — same drift-visibility pattern
as `compute_cache_savings_usd`.

**Billing policy:** transcription cost is **counted against the user daily cap**
— it is real spend. Because the rows carry `cost_usd` + `user_id`, they flow into
`reconcileUserDay` automatically with no extra wiring; no exclusion filter is
applied. (NB: only processes that register a `MongoDBUsageCallback` emit these
rows — the FastAPI summarizer does; the standalone RabbitMQ worker does not
register a callback, so transcription run through the queue-only path is
untracked, same as all other `llm_usage` on that path.)

The summarizer sets `user_id`, `video_summary_id`, `video_id`, and `request_id`
in `pipeline_runner.py` **before** the cache lookup, so every row a run writes is
attributable. `user_id` and `request_id` come from the **structlog contextvars
the worker binds from the queue payload** — *not* from the cache row, which is the
cross-user `videoSummaryCache` doc and carries no per-run owner. The assistant sets
`feature` (`assistant:rag_chat` / `assistant:library_chat` /
`assistant:action:<action>` / `assistant:tool:<name>`), `video_id`, `user_id`, and
`request_id` at each endpoint (from `X-User-Id` / `X-Request-ID` headers the API
gateway forwards) and in the tool router.
Rows written before this (no `user_id` / `request_id`) are treated as
"unattributed (legacy)" by admin — fix-forward only, no historical backfill.

## Cache savings calculation

`compute_cache_savings_usd` multiplies cache-read tokens by the per-1M-token
delta between the input rate and the cached-read rate. Anthropic publishes
cached reads at ~10% of the normal input rate; for Sonnet 4.6 that's
$0.30/MTok vs $3.00/MTok.

```python
delta = rates["input"] - rates["cache_read"]
savings = (cache_read_tokens / 1_000_000) * delta
```

The rate map lives in `_CACHE_RATES_USD_PER_M` (`models.py`), keyed by the
provider-prefixed model. Input / cached-read rates per MTok:

| Model | Input | Cached read | Source |
|-------|-------|-------------|--------|
| `anthropic/claude-sonnet-4-6`, `anthropic/claude-sonnet-4-5` | $3.00 | $0.30 | published prices |
| `anthropic/claude-haiku-4-5-20251001`, `anthropic/claude-haiku-4-5` | $1.00 | $0.10 | published prices |
| `anthropic/claude-sonnet-5-5` | $2.00 | $0.20 | docs prices (pipeline-1min 4.0c), = LiteLLM 1.105.0's bundled map |
| `anthropic/claude-haiku-5-5` | $0.10 | $0.01 | docs prices, the ≤ 100K-token prompt tier where every pipeline call sits ($0.50 / $0.05 above it; only the ≤ 100K rate is in this map) |
| `openai/gpt-6-luna` | $0.10 | $0.01 | LiteLLM 1.105.0's bundled map |

gpt-4o-mini and gpt-4o are not in the map: their OpenAI cache reads are counted
in `cache_read_tokens` but save `0.0`. When a model lacks a mapping, savings are
reported as `0.0`, and an `anthropic/` model is logged once (`cache.rate_missing`)
— under-reporting is preferred over inventing numbers.

**The lookup key (fixed in p4.0d).** LiteLLM hands the callback the bare model
name (`claude-sonnet-5-5`, provider in `custom_llm_provider`), while the map is
keyed `anthropic/claude-sonnet-5-5`; `callback._rate_key` now re-adds the
provider prefix before the lookup. Before it a bare name missed the map, so rows
written before p4.0d under-report `cache_savings_usd` (0).

`cost_usd` is the provider-billed cost (already net of the cache discount).
`cache_savings_usd` is what we *would* have paid had the read tokens been
billed at the full input rate, so total list-price spend ≈ `cost_usd + cache_savings_usd`.

`cost_usd` (and `pipeline.timing` `costUsd`, and the Langfuse generation cost)
is priced from LiteLLM's model map: the remote map LiteLLM fetches at import,
else its bundled copy (the project does not set `LITELLM_LOCAL_MODEL_COST_MAP`,
so the source can differ between processes), plus the Claude Haiku 5.5 entry the
summarizer registers when the loaded map lacks it (`llm_model_rules`, docs prices
incl. the above-100K tier; the assistant process does not register it).

## Where cache_control is set

Five call types carry breakpoints — the plan, the extraction field groups, the
`EXTRACTION_PARALLEL=false` single extraction call, (output-quality 2.1) the hero
rewrite and (4.6e) quiz v2, which re-send the field groups' blocks to read them — always on user content
blocks (Anthropic models only; `services/summarizer/src/services/llm_messages.py`
strips every marker for other providers). `text_block(text, cache=True)` is the 5-minute default (no
`ttl` key on the wire); `text_block(text, cache=True, ttl="1h")` asks for one
hour. In one request every 1h breakpoint must come before any 5m one.

**Plan — 1h (pipeline-1min 2.0).** `plan_prompt.render_plan_parts` cuts
`plan.txt` at its `<video>` line:

- **user block 1** = the English output directive + everything above
  `<video>` (rules, component toolkit, registry caps and requirements,
  examples) — rendered from config alone, so byte-identical for every video;
  ≈ 16.3k tokens on Sonnet 4.6 (16,290 written on prod, `dev/completed/pipeline-1min/gates/g3.md`
  §Prod), ≈ 22.1k on Sonnet 5.5 (its tokenizer; 22,070 written in the 4.0d A/B,
  `dev/completed/pipeline-1min/gates/g4-model-ab.md`).
  `cache_control {type: ephemeral, ttl: 1h}`
  (`plan.PLAN_CACHE_TTL`, `plan.plan_user_content`);
- **user block 2** = `<video>` (title, channel, duration, probe hint,
  playbook, description), the full marked transcript, the closing line —
  uncached.

A run makes one plan call, so a 5-minute entry is never read by its own run —
only by another video's plan call that starts within 5 minutes (A26); a 1-hour
entry is read by every plan call — any video — that starts within an hour of
the previous one. Economics (Sonnet 4.6, $3/MTok input): a 1h write is 2x input
($6/MTok, ≈ $0.098 per miss vs $0.049 uncached), a read 0.1x ($0.30/MTok,
≈ $0.005 per hit), so the 1h breakpoint costs **more** than no breakpoint below
a ~53 % hit rate. A 5m write (1.25x) breaks even at ~22 % — of the 5-minute hit
rate, which is never above the 1h one (every 5-minute hit is also a 1-hour
hit). On Sonnet 5.5, the plan's model since 4.0d ($2/MTok input, 1h write $4,
read $0.20), the ≈ 22.1k-token block costs ≈ $0.088 per miss vs $0.044
uncached and ≈ $0.004 per hit — the same multiples, so the same break-even
rates. Gate item (Kfir 2026-10-12): read the plan's 1h hit rate with
`scripts/cache_hit_rate.py` (below) after two weeks of prod traffic. Gate-2
decision (Kfir 2026-10-12): the plan stays on 1h and there is no switching code —
the "below 50 %" rule is a reporting rule applied by hand. After a week of prod
data, none / 5m / 1h are simulated from the `pipeline.timing` arrival times (when
each plan call started) and the cheapest is picked; `cache_hit_rate.py` alone
cannot answer it, since the 5m hit rate depends on the gaps between plan calls.

**Extraction field groups — 1h + 5m (pipeline-1min 3.4; the default,
`EXTRACTION_PARALLEL=true`).** `extraction_group_prompt.build_group_messages`
sends three user blocks and no system prompt, cut from the same
`build_extraction_prompt` output the single call below uses:

- **block 1** = the rules + `<schema>` (every requested domain's full schema)
  + `<extraction_example>` (the primary domain's) — the same bytes for every
  group and chunk of a run, and for any run with the same domain set;
  `cache_control {type: ephemeral, ttl: 1h}` (`extraction_group_prompt.PREFIX_TTL`);
- **block 2** = `<video>` + the chunk's transcript + `<video_memory>` — the
  same bytes for every group of a chunk; the 5-minute breakpoint;
- **block 3** = the group's job (`extraction_group_job.txt`: the emit-only keys
  and their output shape, the tabs it serves, the primary's emphasis; the
  visual group adds the annotation guide, the chunk's annotations and key
  frames) — uncached.

The 1h breakpoint precedes the 5m one: two of the four Anthropic allows.
**Warm-first** (`extraction_groups.fire_groups`): an entry becomes readable only
once the response writing it has begun, so per chunk text1 (the group holding
the first planned tab) is the one streamed call, and its first chunk releases
the chunk's other groups, which then read blocks 1 + 2 instead of writing them
again. `GROUP_RELEASE_FALLBACK_S` (5 s; 1.5 s raced Sonnet 5.5's slower first
tokens — jMq8 r1 on prod wrote the prefix three times, +$0.065) releases them
without that chunk; `pipeline.timing` `counts.cacheRewrites` / `cacheRewriteTokens`
count such writes (`pipeline/cache_rewrites.py`). A
text1 that fails before its first chunk releases them at once. Measured
(`dev/completed/pipeline-1min/gates/g3.md` §Cache hit rate, dev end runs and prod):
block 1 measures ≈ 2.6–3.4k cl100k tokens on the benchmark domain sets (offline,
`pipeline-1min-tasks.md` 3.4 / 4.0); g3: 1h write 0 (< Haiku 4.5's 4,096-token minimum),
so no 1h entry is written (`cacheWrite1hTokens` 0) and the 5m entry text1 writes holds blocks 1 + 2;
text2 / text3 / visual read it at a 100 % hit rate. The 1h marker on block 1
stays in the request, so a prefix at or above the model's minimum caches for an hour.
Since the 4.0 picks extraction runs on Sonnet 5.5, whose minimum is 512 tokens
(pipeline-1min context.md, "4.0c docs facts"): block 1 (≈ 5.3k tokens on the new
tokenizer) now gets its own 1h entry, written by the first text1 of a domain set in
the hour. Dev v10 T1dQhQAm8Tc: text1 read 5,349 (block 1) and wrote 6,713 for 5m
(block 2); text2 read 12,062 (blocks 1 + 2) (`dev/completed/pipeline-1min/gates/g4.md` §3.7, L5).

**Extraction, `EXTRACTION_PARALLEL=false` — 5m (1c.3).** The single call (one
per chapter batch on a long video) the groups replaced — byte-identical to the
pre-split request for the same plan (`tests/replay/test_replay_extraction_identity.py`):

- **system** = the rules section of `base_extraction.txt` (everything before
  `<video>`) — byte-identical for every video, **no** breakpoint (`system_prompt`
  never carries one);
- **user block 1** = `[<video> + {transcript} + <video_memory>]` —
  `text_block(..., cache=True)`, the one breakpoint
  (`ExtractionPrompt.user_blocks` in `pipeline/extraction_prompt.py`);
- **user block 2** = the job (planned tabs' briefs + caps, schemas, example,
  visual annotations, key frames, per-batch context) — uncached, bound per call.

What that breakpoint buys: little — the reason phases 1–2 saw no extraction
reads. A short video makes one extraction call; a chunked one sends each batch's
own transcript slice in block 1, so no two calls share the prefix. Haiku also
caches nothing under 4,096 tokens per block. Reads started in phase 3, when
several field groups share one `[video + transcript + memory]` block.

The old `cache_static` path (static template half as a cached system message)
is gone. The plan reads the **full** `[m:ss]`-marked transcript (1b.2), so its
uncached block — and cost — grows with the video; memory reads the same string.

**Hero rewrite — reads the field groups' blocks (output-quality 2.1,
`HERO_REWRITE_ENABLED`).** `hero_job.cached_blocks` sends the call as
`[block 1, block 2?, block 3]` on `LLM_EXTRACTION_MODEL` (caches are per model, so
it runs on the groups' model, with their JSON mode and effort and no system prompt):

- **block 1** = `extraction_group_prompt.prefix_block` of the run's ruled prompt —
  the same bytes the group calls sent (1h entry), read at 0.1x;
- **block 2** = `head_block` (`<video>` + the transcript + `<video_memory>`, the 5m
  entry) — only when the run is ONE chunk (`HeroRequest.reads_transcript`). A chunked
  run has one head per chunk and a request can read one prefix, so it reads block 1
  alone and block 3 carries `<video>` + `<video_memory>`;
- **block 3** = the hero job (`prompts/hero_rewrite.txt` + the compacted merged
  extraction, at most `HERO_EXTRACTION_MAX_TOKENS` = 5 000 tokens —
  `HERO_EXTRACTION_PARTED_MAX_TOKENS` = 5 500 when a chunked run's scalars show part by
  part — + the tab labels),
  uncached. A retry (one, with the failed checks appended; made only when the hero would
  be None or a D18 line would drop) changes block 3 only, so it reads the cache too.

Without group blocks (`EXTRACTION_PARALLEL=false`) the request is block 3 alone: no
group call wrote blocks 1 / 2, so sending them would write them (block 1 at 2x for an
hour it never serves). Output cap `HERO_MAX_TOKENS` = 2 000, timeout `HERO_TIMEOUT_S`
= 25 s. Started at text-groups-done, after the groups' own writes, so the entries are
already readable.

**Quiz v2 — reads the field groups' blocks (output-quality 4.6e, `QUIZ_V2_ENABLED`).**
The quiz runs on `LLM_EXTRACTION_MODEL` with the groups' JSON mode and effort and no
system prompt; `quiz_v2_blocks.cached_prefix` sends each call as `[block 1, block 2, job]`:
block 1 = `prefix_block` (the 1h entry), block 2 = `head_block` of the call's chunk — part
k's own chunk on a chunked run, the one head on a one-chunk run (each third reads it) —
both byte-identical to the group calls' and read at 0.1x; the job (`<task>`, `<hero>`,
`<key_ideas>`, the rules — no transcript) is uncached. The re-ask after a reply with no
question sends the same blocks, so it reads the cache too. Without group blocks
(`EXTRACTION_PARALLEL=false`, the late group's own call, a whole-video call on a chunked
run) the call is one uncached message with the transcript inline. The task starts when its
owning groups landed — after their writes, so the entries are readable. Offline (the stored
runs' cache reads, their quiz jobs rendered, the Haiku runs' answer lengths; Sonnet 5.5
$2 in / $0.20 read / $10 out per MTok): S03 (57 min, two chunk calls) 33.5 K read + 4.0 K
job + 2.7 K out ≈ $0.041; S05 (13 min, one call) 12.7 K read + 3.3 K job + 1.0 K out
≈ $0.019 — the answer is about two thirds of it (Haiku 5.5 inline: $0.0037 / $0.0013).
A one-chunk run's thirds read the one head three times (≈ $0.003 a call).

**OpenAI calls (automatic caching).** OpenAI caches a prompt prefix by itself
once it is identical to an earlier request's and at least 1,024 tokens long; it
reports reads (`prompt_tokens_details.cached_tokens`) but no writes, and there
is nothing to mark. Synthesis (the fast tier — `openai/gpt-4o-mini` with
`LLM_FAST_PROVIDER=openai`, as both templates and prod set; with
`HERO_REWRITE_ENABLED` it runs only when the hero call gives no hero), the
`enrich_quiz.txt` quiz (`openai/gpt-4o-mini`; with `QUIZ_V2_ENABLED` it does not run)
and `description_analysis.txt`
(`openai/gpt-6-luna` since 4.0d) are laid out static-first — instructions, schema, example, then the per-video content —
so two videos share their prefix up to the first per-video byte. Today those
static prefixes are 355 / 471 / 311 tokens, below the 1,024 minimum, so they do
not cache across videos yet (a quiz prefix is per quiz domain: its flavor line
sits in `<role>`).

## Output-quality phase 2: the calls that changed

Three ledger-visible changes since `PIPELINE_VERSION` v10 shipped; each has a flag
(`HERO_REWRITE_ENABLED`, `QUIZ_V2_ENABLED`, both default `true`; [INFRASTRUCTURE.md](./INFRASTRUCTURE.md)) that
restores the previous call. The `feature` column is `llm_usage.feature`
(`llm_feature_var`); the span is the Langfuse generation and `pipeline.timing`
`llmCalls[].span` name ([OBSERVABILITY.md](./OBSERVABILITY.md#trace-structure)).

| Call | Span / stage | Model | `feature` | When it bills |
|------|--------------|-------|-----------|---------------|
| Hero rewrite | `hero_rewrite` | `LLM_EXTRACTION_MODEL` (Sonnet 5.5) | `summarize:hero_rewrite` | `HERO_REWRITE_ENABLED`: once per run (a retry is a second row), fired at text-groups-done; reads the groups' cached blocks (above). **Replaces** the synthesis call |
| Synthesis | `synthesis` | the fast tier | `summarize:synthesis` | the flag off, or the hero call gave no hero (`phases/synthesis._synthesis_dict`) — then exactly as before |
| Quiz v2 | `quiz_v2` | `LLM_EXTRACTION_MODEL` (Sonnet 5.5 since 4.6e, reading the groups' cache; no model key of its own) | `summarize:enrichment` | `QUIZ_V2_ENABLED`, an instructional format (`quizPolicy.formats`, 4.6e), a plan with a quiz host: one call per part holding a target key idea — each extraction chunk of a chunked run (S01 / S03 / S14 / S15 2 rows, S02 3), else each third (up to 3 rows) — in parallel; one call when the length or the targets' stamps are missing, or a one-chunk quiz has 3 questions — ≤ 15 min, 30 s per attempt, 1 retry, every call inside one 40 s stage deadline; a call whose reply parses no question, even item by item, is made once more (a row of its own; output-quality 3.0, G2-1 / G2-4). **Replaces** `enrich_quiz` |
| Quiz v1 | `enrichment` | `LLM_ENRICHMENT_MODEL` (gpt-4o-mini) | `summarize:enrichment` | `QUIZ_V2_ENABLED=false` |
| Judge: hero + quiz | `faithfulness` (up to two calls) | `LLM_JUDGE_MODEL` (gpt-6-luna) | `summarize:faithfulness` | after assembly, when the doc has a stamped takeaway / quiz question, the sample rate is above 0, and — per call — the hero rewrite wrote the hero / `QUIZ_V2_ENABLED` is on (flag-off runs make no extra judge call) |

- **Quiz v2 reads the extraction's cache (4.6e).** Until 4.6e it read none (Haiku 5.5, the
  transcript inline: S01 ≈ 21 K in + ≈ 2 K out ≈ $0.003). Each call now reads block 1 + its
  chunk's head at 0.1x ([above](#where-cache_control-is-set)); the inline path (no group
  blocks) pays Sonnet's full input rate for its transcript.
- **The hero's bill is the job block plus the extraction's reads.** Blocks 1 (+ 2) are
  priced as cache reads when the group calls wrote them (the rates in the table
  above), block 3 is plain input, the answer is capped at 2 000 tokens. The synthesis
  call it replaces (≤ 1 200 tokens out, fast tier) is no longer made on a run that gets a hero.
- **The judge's two extra calls** are bounded by prompt caps: at most ≈ $0.0021 per run
  (≈ $0.0013 at English's ~4 characters a token — `faithfulness_hero_quiz` docstring);
  ≈ $0.0004 per video was measured over the 13 gate-1 docs
  (`dev/active/output-quality/output-quality-tasks.md` 2.4). The extraction sample's
  ≈ $0.002–0.003 per video is unchanged.
- No `PIPELINE_VERSION` bump: the stored shapes the new calls write are additive
  ([DATA-MODELS.md](./DATA-MODELS.md#output-quality-hero-and-quiz-2026-10)).

## Output-quality phase 3: merge_rank

One added call, on chunked runs only (> 40 min; `MERGE_RANK_ENABLED`, default `true` —
`false` keeps the code merge alone):

| Call | Span / stage | Model | `feature` | When it bills |
|------|--------------|-------|-----------|---------------|
| merge_rank | `merge_rank` | `LLM_MEMORY_MODEL` (Haiku 5.5; no model key of its own — a blank setting follows memory's) | `summarize:merge_rank` | a split run of more than one chunk: once, at text-groups-done beside the hero (a wrapper retry or the one re-ask after a reply that is no JSON object is a second row); 12 s per attempt, the stage inside 25 s |

- **No cache.** The input is code-rendered lists, not a prefix any other call sends.
  Offline renders of the stored extractions: S02 (83 min, 113 items) ≈ 27.8 K characters
  ≈ 9.3 K tokens with the rules, S13 (42 min, 52 items) ≈ 13.6 K ≈ 4.5 K — billed 5 408
  in / 324 out on the p3i3 run (ids and clocks tokenize denser than prose); the answer is
  ids only. ≈ $0.001–0.002 a long video on Haiku 5.5 ($0.10 / $0.50 per
  MTok); ≤ 40-min runs never make the call.

## Output-quality phase 4: the English beneath verbatim quotes

| Call | Span / stage | Model | `feature` | When it bills |
|------|--------------|-------|-----------|---------------|
| Verbatim lines → English | `translation_full` (source → en) | `LLM_TRANSLATION_MODEL` (gpt-6-luna) | `summarize:translation` | a non-English run with a verbatim quote or verified question: one call per ≤ 2,500-char / ≤ 100-string batch of distinct lines, in parallel with the output translation; English runs never |

S15: 25 lines, ≈ 1.6 K chars → 1 call, ≈ 1 K in / 0.5 K out ≈ $0.0004 ($0.10 / $0.50 per MTok); the non-English extraction block 1 is 69 bytes shorter.

## How 1h writes are priced

Anthropic returns `usage.cache_creation.{ephemeral_5m_input_tokens,
ephemeral_1h_input_tokens}`; LiteLLM (1.91, and 1.105.0 since p4.0a) keeps the split in
`usage.prompt_tokens_details.cache_creation_token_details`, and
`completion_cost` prices each part at its own rate
(`cache_creation_input_token_cost` = 1.25x input,
`cache_creation_input_token_cost_above_1hr` = 2x). So `cost_usd` in the ledger,
`costUsd` in `pipeline.timing` and the Langfuse generation cost are right for 1h
writes with no code of ours: `tests/test_cache_usage_1h.py` pins it against the
published Sonnet 4.6 prices using usage objects recorded from a live call.
Sonnet 5.5's entry in LiteLLM 1.105.0's bundled map carries both write rates
($2.50 5m, $4.00 1h per MTok), and the Haiku 5.5 entry the summarizer registers
carries 1.25x / 2x of its input rate. A
response without the split (not seen from Anthropic) would be priced entirely at
the 5m rate. The ledger keeps only the total (`cache_creation_tokens`); the
per-call 1h / 5m split lives in `pipeline.timing.llmCalls[]`
(`cacheWrite1hTokens`, `cacheWrite5mTokens`). `cache_savings_usd` counts reads
only — the write premium is already inside `cost_usd`.

## How to audit

Run the audit script:

```bash
python scripts/audit_cache_credits.py                  # last 24h
python scripts/audit_cache_credits.py --hours 1        # last hour
python scripts/audit_cache_credits.py --video-id <id>  # one video
python scripts/audit_cache_credits.py --json           # machine-readable
```

**Caveat (LiteLLM 1.105):** the script groups `llm_usage` rows by model and keeps
the ones whose stored `provider` is `anthropic` (`derive_verdict`). Callback rows
now store `provider: "unknown"` (see §Schema), so a window of such rows ends "No
Anthropic calls in window." (exit 1) until that field is fixed. Use
`scripts/cache_hit_rate.py` (below) meanwhile — it reads `pipeline.timing`, which
records the provider-prefixed model.

Exit codes:
- `0` cache hits observed on Anthropic calls
- `1` no records in window
- `2` Anthropic calls present but zero cache hits — investigate. Since 2.0 the
  plan's 1h block is read whenever two plan calls start within an hour of each
  other, and since phase 3 every field group after a chunk's text1 reads the
  shared prefix; a window of single-group runs whose plan calls are all more
  than an hour apart (or `EXTRACTION_PARALLEL=false` extraction, see above)
  still legitimately shows no reads.

Per call type, with the 1h / 5m split — the number gate reports carry:

```bash
python scripts/cache_hit_rate.py                      # runs started in the last 14 days
python scripts/cache_hit_rate.py --days 1             # the last 24 hours
python scripts/cache_hit_rate.py --days 1 --include-eval   # ... with eval-user runs
python scripts/cache_hit_rate.py --ids T1dQhQAm8Tc <summaryId> ...
python scripts/cache_hit_rate.py --json
```

It reads `pipeline.timing.llmCalls` (not `llm_usage`) — plus, from timing
version 4, the refused calls' `llmFailures` rows (`error: "Refusal"`, billed and
cache-reading like any call) — and prints, per `span`
and provider: calls, input tokens, reads, writes (1h / 5m), hit rate =
reads ÷ (reads + writes) for Anthropic, and the cached share of input tokens
(the OpenAI number). Runs timed before `pipeline.timing` version 2 have no
split and no OpenAI reads; the header counts runs per version. The window
leaves out eval-user rows (`evalRun: true` — golden passes and dev runs, bursts
of plan calls minutes apart that would lift the prod hit rate) unless
`--include-eval`; `--ids` reads the named runs whoever ran them.

## Common failure modes

1. **Zero hits on text2 / text3 / visual group calls** (phase 3 on) — usually
   means block 1 or 2 is not byte-identical across the calls that share it
   (e.g. a per-group value slipped in before the 5m breakpoint) or the warm-up
   did not release them (`ttftMs` on `extraction_text1`). Everything a call
   varies must be bound in the job block — `tests/test_extraction_group_prompt.py`
   and `tests/test_extraction_cache_layout.py` hash the shared blocks. For the plan, a
   per-video value above `<video>` in `plan.txt` (or in a registry block it
   renders) splits the cache per video — `tests/test_plan_cache_ttl.py`
   guards it; a registered `plan.txt` without a `<video>` line is sent
   uncached and logged.
2. **`cache.rate_missing` log** — a model alias was added in `config.MODEL_MAP`
   (or a stage setting) without a corresponding entry in `_CACHE_RATES_USD_PER_M`.
   Add the per-1M-token rates for that model, keyed `provider/model`.
3. **`cost_usd = 0`** — LiteLLM doesn't know the price for a custom-named
   model (e.g. a date-stamped Anthropic preview). Map the alias to a known
   model in LiteLLM's pricing table or accept zero cost reporting until the
   model goes GA.

## Status of P1 work

- ✅ Cache fields on `UsageRecord` (creation/read tokens, hit flag, savings)
- ✅ Per-model cache rate map with miss-logging fallback (provider-prefixed lookup
      and the 4.0 models since p4.0d)
- ✅ `cache_control: ephemeral` on the extraction `[video + transcript + memory]` user block
- ✅ `cache_control: ephemeral, ttl 1h` on the plan's static half (2.0); 1h / 5m
      write split per call in `pipeline.timing`; 1h writes priced at 2x (verified)
- ✅ Audit script (`scripts/audit_cache_credits.py`) and per-call-type gate tool
      (`scripts/cache_hit_rate.py`)
- ✅ Extraction reads from phase 3: field groups share blocks 1 + 2, warm-first
      (3.4; 100 % hit rate after text1 — `gates/g3.md`)
- 📋 After two weeks of prod traffic the plan's 1h hit rate decides whether the
      1h breakpoint stays — what replaces it below 50 % is Kfir's gate-2 decision
      (see the plan above).

## Per-user daily cap

`llm_usage` rows (Python schema) are aggregated per-user into the `userCosts`
collection by `CostMonitorService.reconcileUserDay` /
`reconcileAllUsersForDay` — every row's `cost_usd` for the day, a refused
answer's row and its previous-model re-send included. The aggregate feeds two gates:

1. **POST /api/videos** — `reserveUserCost(userId, tier)` atomically increments
   the day's `totalCostUsd` by an over-estimate, then refunds-and-throws
   `DailyLimitReachedError` (HTTP 429) if the user is already past
   `COST_LIMITS_PER_TIER[tier]`. The increment-then-check pattern serializes
   concurrent submissions on the per-user/day doc — see the regression test
   `should serialize concurrent reservations on the same user/day`.
2. **GET /api/users/me/usage** — returns today's effective spend, headroom,
   and the next UTC midnight reset for the in-app usage tile.

Reconciliation runs on every terminal video status (`completed` / `failed`)
via the internal status callback, and nightly via `POST /internal/reconcile-costs`.
The terminal-status path is the canonical refund-on-failure — do not also
call `refundReservation` on failure, or the day will be double-credited.

The Node `CostMonitorService.recordUsage` writes a **different** schema
(`model`, `cost`, `createdAt`) that reconciliation does **not** read; use it
only for global cost telemetry, not for per-user attribution.

Admin grants and manual charges go through `userCostAdjustments` (signed
storage: negative = credit) — see [DATA-MODELS.md](./DATA-MODELS.md#usercostadjustments)
and [SERVICE-ADMIN.md](./SERVICE-ADMIN.md#per-user-costs-users).

## Ledger Retention (decided 2026-07-06)

`llm_usage` is the financial ledger. **Rows are retained indefinitely** — the
former 90-day TTL index silently erased billing history and was removed
(`api/src/plugins/mongodb.ts` now drops the legacy TTL index at startup and
recreates `createdAt` as a plain index). Rationale: personal-scale volume
(thousands of rows/month at most) makes rollups unnecessary, and cost audits /
cache-credit reconciliation need the raw rows. Revisit only if the collection
exceeds ~1M rows; the fallback design is a monthly rollup collection populated
before any re-introduced expiry.

The vie-admin lifespan **also** drops any legacy 90-day TTL on `llm_usage`
(`timestamp_1` — it used to recreate one on every boot) and recreates
`timestamp` as a plain index, so either service booting first heals the ledger.

## Alerting (implemented 2026-07-12)

Three thresholds, all real:

| Alert type | Evaluated by | Trigger |
|---|---|---|
| `high_cost_call` | `llm_common.callback.MongoDBUsageCallback` (inline, per LLM call) | single call cost > `cost_threshold` (constructor default $0.50) |
| `daily_spend_spike` | vie-admin `alert_evaluator` loop (every 5 min) | today's spend > `daily_spike_multiplier` × trailing 7-day daily average (baseline must be ≥ $0.50/day) |
| `high_failure_rate` | vie-admin `alert_evaluator` loop (every 5 min) | failure rate over the trailing 60 min > `failure_rate_threshold`, with ≥ 10 calls in the window |

The spike/failure thresholds are read live from `llm_alert_config` (managed by
`POST /alerts/config` in vie-admin), which was previously decorative. Per-type
cooldowns (6 h spike, 1 h failure) stop a sustained condition from re-alerting
every cycle.

**Delivery:** every alert is written to `llm_alerts` (rendered by the admin UI)
and POSTed as JSON to `ALERT_WEBHOOK_URL` when set (Slack-compatible generic
webhook / ntfy / any HTTP catcher). The URL must be `http(s)://` — any other
scheme is rejected (logged, no send). Empty URL → Mongo-only. Delivery is
best-effort with a 3 s timeout and never breaks the LLM call or the evaluator;
the webhook fires even when the Mongo write fails. Senders:
`packages/llm-common/src/llm_common/alerts.py` (stdlib urllib, sync services)
and `services/admin/src/services/alert_evaluator.py` (httpx, async).
