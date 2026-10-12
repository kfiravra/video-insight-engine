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
| `model`                   | `kwargs["model"]`                                   | LiteLLM model string, e.g. `anthropic/claude-sonnet-4-6` |
| `provider`                | derived from `model`                                | `anthropic` / `openai` / `gemini` / `unknown` |
| `tokens_in`               | `response.usage.prompt_tokens`                      | Includes uncached + cache-create + cache-read |
| `tokens_out`              | `response.usage.completion_tokens`                  | |
| `cost_usd`                | `litellm.completion_cost(response)`                 | Provider-billed cost, already net of cache discount |
| `cache_creation_tokens`   | `usage.cache_creation_input_tokens`                 | Tokens that wrote into the cache this call |
| `cache_read_tokens`       | `usage.cache_read_input_tokens`                     | Tokens served from the cache this call |
| `cache_hit`               | `_hidden_params.cache_hit` or `cache_read > 0`      | Either signal triggers True |
| `cache_savings_usd`       | `compute_cache_savings_usd(model, cache_read)`      | See below |
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
  `:whisper_translate`.
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

The rate map lives in `_CACHE_RATES_USD_PER_M`. When a model lacks a mapping,
savings are reported as `0.0` and the missing model is logged once
(`cache.rate_missing`) — under-reporting is preferred over inventing numbers.

`cost_usd` is the provider-billed cost (already net of the cache discount).
`cache_savings_usd` is what we *would* have paid had the read tokens been
billed at the full input rate, so total list-price spend ≈ `cost_usd + cache_savings_usd`.

## Where cache_control is set

Two calls carry a breakpoint, both on a user content block (Anthropic models
only; `services/summarizer/src/services/llm_messages.py` strips every marker for
other providers). `text_block(text, cache=True)` is the 5-minute default (no
`ttl` key on the wire); `text_block(text, cache=True, ttl="1h")` asks for one
hour. In one request every 1h breakpoint must come before any 5m one.

**Plan — 1h (pipeline-1min 2.0).** `plan_prompt.render_plan_parts` cuts
`plan.txt` at its `<video>` line:

- **user block 1** = the English output directive + everything above
  `<video>` (rules, component toolkit, registry caps and requirements,
  examples) — rendered from config alone, so byte-identical for every video;
  ~16.4k tokens on Sonnet 4.6. `cache_control {type: ephemeral, ttl: 1h}`
  (`plan.PLAN_CACHE_TTL`, `plan.plan_user_content`);
- **user block 2** = `<video>` (title, channel, duration, probe hint,
  playbook, description), the full marked transcript, the closing line —
  uncached.

A run makes one plan call, so a 5-minute entry would be written and never read
(A26); a 1-hour entry is read by every plan call — any video — that starts
within an hour of the previous one. Economics (Sonnet 4.6, $3/MTok input): a
1h write is 2x input ($6/MTok, ≈ $0.098 per miss vs $0.049 uncached), a read
0.1x ($0.30/MTok, ≈ $0.005 per hit), so the 1h cache costs less than no cache
once more than ~53 % of plan calls hit (a 5m cache breaks even at ~22 %).
Gate rule (Kfir 2026-10-12): if the plan's 1h hit rate is below 50 % after two
weeks of prod traffic, the plan switches to 5m — read it with
`scripts/cache_hit_rate.py` (below).

**Extraction — 5m (1c.3).**

- **system** = the rules section of `base_extraction.txt` (everything before
  `<video>`) — byte-identical for every video, **no** breakpoint (`system_prompt`
  never carries one);
- **user block 1** = `[<video> + {transcript} + <video_memory>]` —
  `text_block(..., cache=True)`, the one breakpoint
  (`ExtractionPrompt.user_blocks` in `pipeline/extraction_prompt.py`);
- **user block 2** = the job (planned tabs' briefs + caps, schemas, example,
  visual annotations, key frames, per-batch context) — uncached, bound per call.

What the extraction breakpoint buys in phases 1–2: little. A short video makes
one extraction call; a chunked one sends each batch's own transcript slice in
block 1, so no two calls share the prefix. Haiku also caches nothing under
4,096 tokens per block. Reads start in phase 3, when several extraction groups
share one `[video + transcript + memory]` block.

The old `cache_static` path (static template half as a cached system message)
is gone. The plan reads the **full** `[m:ss]`-marked transcript (1b.2), so its
uncached block — and cost — grows with the video; memory reads the same string.

**OpenAI calls (automatic caching).** OpenAI caches a prompt prefix by itself
once it is identical to an earlier request's and at least 1,024 tokens long; it
reports reads (`prompt_tokens_details.cached_tokens`) but no writes, and there
is nothing to mark. Synthesis, the quiz (`enrich_quiz.txt`) and
`description_analysis.txt` (all `openai/gpt-4o-mini` in prod and dev) are laid
out static-first — instructions, schema, example, then the per-video content —
so two videos share their prefix up to the first per-video byte. Today those
static prefixes are 355 / 471 / 311 tokens, below the 1,024 minimum, so they do
not cache across videos yet (a quiz prefix is per quiz domain: its flavor line
sits in `<role>`).

## How 1h writes are priced

Anthropic returns `usage.cache_creation.{ephemeral_5m_input_tokens,
ephemeral_1h_input_tokens}`; LiteLLM (1.91) keeps the split in
`usage.prompt_tokens_details.cache_creation_token_details`, and
`completion_cost` prices each part at its own rate
(`cache_creation_input_token_cost` = 1.25x input,
`cache_creation_input_token_cost_above_1hr` = 2x). So `cost_usd` in the ledger,
`costUsd` in `pipeline.timing` and the Langfuse generation cost are right for 1h
writes with no code of ours: `tests/test_cache_usage_1h.py` pins it against the
published Sonnet 4.6 prices using usage objects recorded from a live call. A
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

Exit codes:
- `0` cache hits observed on Anthropic calls
- `1` no records in window
- `2` Anthropic calls present but zero cache hits — investigate. Since 2.0 the
  plan's 1h block is read whenever two plan calls start within an hour of each
  other; a window whose plan calls are all more than an hour apart (and phases
  1–2 extraction, see above) still legitimately shows no reads.

Per call type, with the 1h / 5m split — the number gate reports carry:

```bash
python scripts/cache_hit_rate.py                      # runs started in the last 14 days
python scripts/cache_hit_rate.py --days 1             # the last 24 hours
python scripts/cache_hit_rate.py --ids T1dQhQAm8Tc <summaryId> ...
python scripts/cache_hit_rate.py --json
```

It reads `pipeline.timing.llmCalls` (not `llm_usage`) and prints, per `span`
and provider: calls, input tokens, reads, writes (1h / 5m), hit rate =
reads ÷ (reads + writes) for Anthropic, and the cached share of input tokens
(the OpenAI number). Runs timed before `pipeline.timing` version 2 have no
split and no OpenAI reads; the header counts runs per version.

## Common failure modes

1. **Zero hits on Sonnet calls** (from phase 3 on) — usually means the cached
   `[video + transcript + memory]` block is not byte-identical across the calls
   that share it (e.g. a per-call value slipped in before the breakpoint).
   Everything a call varies must be bound in the job block. For the plan, a
   per-video value above `<video>` in `plan.txt` (or in a registry block it
   renders) splits the cache per video — `tests/test_plan_cache_ttl.py`
   guards it; a registered `plan.txt` without a `<video>` line is sent
   uncached and logged.
2. **`cache.rate_missing` log** — a model alias was added in `config.MODEL_MAP`
   without a corresponding entry in `_CACHE_RATES_USD_PER_M`. Add the
   per-1M-token rates for that model.
3. **`cost_usd = 0`** — LiteLLM doesn't know the price for a custom-named
   model (e.g. a date-stamped Anthropic preview). Map the alias to a known
   model in LiteLLM's pricing table or accept zero cost reporting until the
   model goes GA.

## Status of P1 work

- ✅ Cache fields on `UsageRecord` (creation/read tokens, hit flag, savings)
- ✅ Per-model cache rate map with miss-logging fallback
- ✅ `cache_control: ephemeral` on the extraction `[video + transcript + memory]` user block
- ✅ `cache_control: ephemeral, ttl 1h` on the plan's static half (2.0); 1h / 5m
      write split per call in `pipeline.timing`; 1h writes priced at 2x (verified)
- ✅ Audit script (`scripts/audit_cache_credits.py`) and per-call-type gate tool
      (`scripts/cache_hit_rate.py`)
- 📋 Extraction reads from phase 3 (grouped extraction); the plan's 1h hit rate
      decides 1h vs 5m after two weeks of prod traffic.

## Per-user daily cap

`llm_usage` rows (Python schema) are aggregated per-user into the `userCosts`
collection by `CostMonitorService.reconcileUserDay` /
`reconcileAllUsersForDay`. The aggregate feeds two gates:

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
