# Pipeline Idempotency Keys

Request-level dedup for `POST /api/videos`. Same inputs within the TTL window return the cached `videoSummaryId` instead of running a fresh pipeline.

## Why

Three failure modes that cost the user real money:

1. **Double-click** — user clicks Generate twice; two pipelines start.
2. **Browser retry** — network blip auto-resends; user is charged twice.
3. **Refresh-while-submitting** — user reloads before redirect; new submit fires.

Idempotency keys make these silent successes — the second submission attaches to the first.

## How it works

```
POST /api/videos
  ↓ Auth + Tier
  ↓ Validate Idempotency-Key header (optional, ≤255 printable ASCII) — HTTP 400 if malformed
  ↓ Extract youtubeId
  ↓ Compute hash = SHA-256(userId : youtubeId : PIPELINE_VERSION : providers [: client : headerValue])
  ↓ reserveHash (atomic insert of a `pending` placeholder on unique-hash index)
  ↓
  ├─ created=true (we won the race):
  │    minutes-ledger quote + hold → videoService.createVideo → completeHash
  │    Return result with { cached: bool } (HTTP 201)
  │
  └─ created=false (someone got there first):
       ├─ existing.status === 'completed' + userVideo still exists
       │    → a blocked account: HTTP 403 ACCOUNT_BLOCKED (D-BLOCK)
       │    → return it with { duplicate: true, cached: true [, updateAvailable: true] }  (HTTP 200)
       │
       ├─ existing.status === 'completed' + userVideo deleted
       │    → invalidateStaleCompleted(hash, observed videoSummaryId)
       │    → retry reserveHash:
       │        ├─ created=true → fall through to normal creation flow
       │        └─ created=false → HTTP 409 IDEMPOTENCY_IN_FLIGHT (lost the retry race)
       │
       └─ existing.status === 'pending' (original still processing)
            → HTTP 409 IDEMPOTENCY_IN_FLIGHT (403 ACCOUNT_BLOCKED for a blocked account)
```

A replay serves nothing new, but a blocked account submits nothing of any kind, so both replay answers check `caps.blocked` first (`LedgerEnforcement.refuseIfBlocked`). `bypassCache`, `cold` and `updateToLatest` skip the gate (see [Bypass for power users](#bypass-for-power-users)).

The stale-hit cleanup uses `invalidateStaleCompleted(hash, videoSummaryId)` rather than a plain `deleteOne({ hash })` so a concurrent fresh reservation cannot be accidentally deleted: the delete is scoped to the exact `(hash, status: 'completed', videoSummaryId)` triple we observed.

On `status: 'failed'` from the summarizer, `idempotencyService.invalidateByVideoSummaryId(videoSummaryId)` drops the key so the user can retry immediately rather than wait out the TTL. `completeHash` is also status-scoped (`{ hash, status: 'pending' }`) so a late completion cannot stomp a row that was already cleaned up and re-reserved.

## Hash inputs

The system uses **two key shapes** in different layers:

### Per-user idempotency hash (request layer)

SHA-256 of `userId:youtubeId:PIPELINE_VERSION:providersHash`. Owned by `idempotencyService.computeKey()`; gates `POST /api/videos` against the same user double-submitting.

- **`userId`** — namespaces per user so cross-user collisions are impossible.
- **`youtubeId`** — the canonical 11-char ID (URL params stripped upstream).
- **`PIPELINE_VERSION`** — canonical version string, single-sourced from `packages/shared/src/config/pipeline-version.json` (NOT an env var; both the api and the summarizer read the same file). **Bump the JSON on every meaningful pipeline change** — all stale keys auto-miss.
- **`providersHash`** — stable JSON of the provider override (`default`/`fast`/`fallback`). Empty when not supplied. Two different provider configs produce two different hashes.

When a client supplies an `Idempotency-Key` header (Stripe convention), the header value is **mixed INTO** the payload (it does not replace it). The hash becomes `SHA-256(userId:youtubeId:PIPELINE_VERSION:providersHash:client:headerValue)`. This is stricter than Stripe's "same key + different params is an error" semantic — different params with the same header simply produce a different hash, so the work runs fresh. We trade the ability to dedup arbitrary payloads for the guarantee that reusing a key by mistake never returns the wrong video.

The header is validated to 1-255 printable ASCII characters (`/^[\x21-\x7E]{1,255}$/`); other values return HTTP 400 `INVALID_IDEMPOTENCY_KEY`.

### Cross-user content-addressed key (cache layer)

SHA-256 of `youtubeId:PIPELINE_VERSION:providersHash:v<version>`. **No `userId`** — that's the point: two different users submitting the same video collapse onto a single `videoSummaryCache` row instead of running the pipeline twice.

Owned by `computeContentKey()` (free function in `api/src/services/idempotency.service.ts`, also exposed as `IdempotencyService.computeContentKey()`). Stored as `videoSummaryCache.dedupKey` with a partial unique index on `{$exists: true}` so legacy pre-backfill rows coexist.

Entry point: `VideoRepository.upsertCacheByDedupKey(...) → { doc, wasInsert }` — atomic `findOneAndUpdate` with `$setOnInsert`. Exactly one concurrent caller sees `wasInsert: true` and is responsible for dispatching the pipeline; the rest attach to the existing row.

`version` is included so a `bypassCache` version-bump (which deliberately starts a fresh pipeline run) produces a distinct key from the existing row, while two concurrent first-time submits both targeting `v1` collapse correctly.

### Dispatch guard (publish layer)

Belt-and-suspenders Redis lock above the content-addressed upsert. The upsert collapses concurrent submits at the cache row; the dispatch guard catches the narrower window where two API replicas race outside Mongo's serialization, or where `dispatchPipeline` is called twice for the same row (e.g. a failed-retry firing while a prior dispatch is still in-flight).

- **Files**: `api/src/services/dispatch-guard.service.ts` + `api/src/plugins/redis.ts`.
- **Key**: `vie:api:dispatched:<videoSummaryId>`.
- **Acquire**: `SET NX EX <DISPATCH_GUARD_TTL_SECONDS>` — atomic. On win, the caller gets a token (`randomUUID`) it must pass to `release()`.
- **Release**: Lua compare-and-delete using the token (safe even if a fresh attempt re-acquired after TTL expiry). With `token: null`, blind `DEL` — used by `internal.routes.ts` on terminal FAILED status from the summarizer.
- **Release on throw**: `dispatchPipeline()` wraps the entire publish path in try/catch and releases the guard if any publish call throws, so a transient broker error doesn't strand the guard for the full TTL.
- **Fail-open contract**: any Redis error returns `acquired: true` so the caller proceeds. Rationale — the summarizer's per-`video_summary_id` lock at `services/summarizer/src/services/cache/pipeline_event_stream.py` is the last line of defense and dedups even if we double-publish; a Redis outage silently dropping user submissions would be far worse.
- **TTL**: `DISPATCH_GUARD_TTL_SECONDS` defaults to 900s. **Must exceed** the summarizer's `PIPELINE_LOCK_TTL_SECONDS` (default 600s) so the guard doesn't expire while the pipeline lock is still held — 50% headroom by default.
- **REDIS_URL**: Both the API (dispatch guard) and the summarizer (pipeline lock + response cache) must point at the **same** Redis instance for operational coherence.

## Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `PIPELINE_VERSION` | (from `packages/shared/src/config/pipeline-version.json`) | Canonical version string baked into both the per-user idempotency hash AND the cross-user `dedupKey`, namespacing the summarizer's Redis response cache, and stamped as `pipelineVersion` on every persisted Mongo summary doc. **Not an env var** — single-sourced from the shared JSON so the api and summarizer can never diverge. Bump the JSON on prompt/schema/pipeline changes. |
| `IDEMPOTENCY_TTL_SECONDS` | `86400` (24h) | Per-key TTL for `idempotencyKeys`. Long enough for accidental double-submits; short enough that "I want to retry tomorrow" works. |
| `REDIS_URL` | `redis://vie-redis:6379` | Redis connection for the dispatch guard. Must be the same instance the summarizer uses for its per-video pipeline lock. |
| `DISPATCH_GUARD_TTL_SECONDS` | `900` | TTL on `vie:api:dispatched:<videoSummaryId>`. Must exceed the summarizer's `PIPELINE_LOCK_TTL_SECONDS` (default 600s). |

## When to bump `PIPELINE_VERSION`

Bump on **any** change that would change pipeline OUTPUT for the same input. Examples:

- Prompt rewrites (extraction, synthesis, enrichment, plan)
- Domain schema changes (new fields, renamed fields, type changes)
- Model version changes (e.g. switching from Sonnet 4.5 → 4.6 by default)
- Assembly rule changes that affect tab shape
- Cross-tab link rule additions

Do **NOT** bump for:

- Logging changes, observability, instrumentation
- Performance optimizations that don't change output
- Bug fixes that don't change the happy-path output
- Frontend-only changes

**Rule of thumb**: if a user re-running the same video would get a different result, bump.

> **Note**: bumping `PIPELINE_VERSION` invalidates **both** the per-user idempotency hash AND the cross-user content-addressed `dedupKey` — same input, different version = different keys in both layers, so a new submitter of a video gets a fresh row at the new version. It also shifts the summarizer's Redis response-cache namespace (stale entries TTL out). A copy the user **already has** is never regenerated by the bump (D-LIB): a completed doc **stamped** with an older `pipelineVersion` is served free, as it is, when the user opens or resubmits it, with `updateAvailable: true` on the `POST /api/videos` answer and on `GET /api/videos/:id`. The update is the user's explicit choice, `POST /api/videos` with `updateToLatest: true`, and it is a new video at the current version: the normal flow at the **current** `dedupKey` (upsert, then created, attached or cached), with the user's existing entry re-pointed at the resulting row (same entry id; `videoSummaryId`, `status` and the denormalised fields) rather than a second entry. A completed current-version row makes it a free re-point (the hold goes back), a running one a free attach, no row a run that is held and settled as usual; a refused account is served a completed or running current version and gets the ledger's 402/422/429 only when a run would happen. The old stale row is never touched, so its other owners keep their copies and a failed update cannot flip them. While the row the entry moved to can still fail (created or attached, not a completed re-point), the entry remembers the copy it left (`userVideos.previousVideoSummaryId`); the summarizer's terminal status relay hands it back to that copy on FAILED (status and denormalised fields from the old row, so `updateAvailable` shows again and the update can be retried) and unsets the field on COMPLETED (`LibraryUpdateRepository`, idempotent; an entry whose previous copy is gone keeps the failed state and is logged). Filing a video the user already has into another folder (`folderId`, a playlist import) never forces a run either: the current version's row wins when it is completed or running, and otherwise the new entry points at the user's own completed copy, whatever its version (free, `cached`, `updateAvailable` when stale). A user who never had the video still gets a stale shared row at the current `dedupKey` (an api / summarizer version split) regenerated on that row, charged, because to them it is a new video; that is the one same-row regeneration left, and the row reads `pending` once it is dispatched. Docs **without** a `pipelineVersion` field predate stamping and are always current-legacy (no update offered) — a bump never mass-invalidates them, so no out-of-band DB flush is needed or wanted.

### Procedure

1. Pick a new version string. Convention: `v{N}` (e.g. `v8` → `v9`). Date-suffixed (`v9-2026-08-25`) is also fine for traceability.
2. Edit `version` in `packages/shared/src/config/pipeline-version.json` — the single source for the api AND the summarizer.
3. Recreate the three containers that mount it — `docker compose up -d --force-recreate vie-api vie-summarizer vie-summarizer-worker` (a single-file bind mount: a `git pull` replaces the file's inode, so a restart keeps the old content). Check all three: the api and summarizer boot logs print `pipeline_version` (the two lines must match); for the worker run `docker compose exec vie-summarizer-worker cat /app/shared/pipeline-version.json`.
4. Old keys remain in `idempotencyKeys` until their TTL expires; they just no longer match new submissions. Old cache rows keep serving, free, to users who already own them, with the update on offer; an owner who asks for it (`updateToLatest`) moves to the current version's row, as a new video would, and the old row stays as it is.
5. Re-run the demo library: `scripts/demo_rerun.py` lists the videos still on the old version and the cost (dry run); `--apply` resubmits them as the demo user, on the owner's go.

**Current version: `v11`** (2026-08: v6→v7 consistency overhaul — playbooks/forbidden, adaptive vision tiers; v7→v8 moment redesign + exact-timestamp frame fill + hi-res frame pipeline; 2026-10: v8→v9 pipeline-1min — split extraction (field groups fired from plan-done and frames-done, tabs emitted per group), plan-done reconcile with code-evidence gating; the task's phases 1–2 shipped under `v8`; v9→v10 the 4.0 model refresh — extraction on Sonnet 5.5 with the completeness rule, memory on Haiku 5.5, plan + vision on Sonnet 5.5, description + translations on gpt-6-luna; v10→v11 output-quality — the hero rewrite call, quiz v2 (lectures and tutorials only), split-extraction stamp / chunk fixes, merge_rank, assembly hygiene, interview / quote / review output, every stored row went stale once).

**`v10` → `v11` (output-quality, 2026-10, C.1).** The task bumped once, at close-out: phases 0b–4 changed stored output without a version change on purpose (dev re-runs stayed cold and bypassed: `tools/devruns.sh` → `devrun.py --cold`, `bypassCache` on), so every stored row went stale once, at this bump. (Under the billing release's D-LIB rule a stale row is no longer regenerated by a plain resubmit: its owners are served it and offered the update.)

### Unification note (2026-07-08)

Before single-sourcing, the api hardcoded `v2` while the summarizer hardcoded `v6`. The canonical file adopted **`v6`** (the summarizer's lineage — it names the output schema, and keeps the Redis response cache warm). Adopting `v6` api-side changes every idempotency hash and `dedupKey` computed under `v2`: in-flight `idempotencyKeys` rows (≤24h TTL) orphan harmlessly, and pre-unification `videoSummaryCache.dedupKey` values become unreachable for **new** cross-user submissions — the first re-submission of such a video runs the pipeline once more, after which the new row dedups normally. Same-user resubmissions are unaffected (matched via `userVideos`, not `dedupKey`). At unification time the live DB had 0 `idempotencyKeys` rows and 0 `dedupKey`-carrying cache rows, so the switch was a no-op in practice.

## Bypass for power users

`POST /api/videos?bypassCache=true` skips the idempotency gate entirely. Use when a user explicitly wants a fresh run (admin tool, paid feature). The route's existing `bypassCache` flag handles this.

`updateToLatest: true` skips the gate too: it is the user's explicit move to the current version, so a plain submit of the same video moments earlier must not answer it as a `duplicate`. Unlike `bypassCache` it inserts no version row: it meets the current version's shared row through the normal upsert, so a double-click attaches the second click to the first click's run (its hold given back). Both flags are refused for the shared demo account (`requireNotDemo`).

Beyond the gate, `bypassCache` also has to defeat the summarizer's **Redis response cache** (keyed by youtubeId + PIPELINE_VERSION, so a version bump alone doesn't help a same-version refresh). Two channels carry the signal, because either side can win the producer race:

1. The api stamps `forceRefresh: true` on the fresh `videoSummaryCache` version row; `pipeline_runner.stream_summarization` reads it (`force_refresh or entry.forceRefresh`) and the repository `$unset`s it on completion.
2. Queue submissions pass `force_refresh` in the worker payload (`drive_pipeline` → `produce_to_broker(force_refresh=…)`).

Either one makes the summarizer skip the Redis response cache and run the pipeline fresh.

## Version rows (bypassCache and eval runs)

A `bypassCache` submission on a video that already has a user version, and **every**
submission by the eval user (`users.isEvalUser`, D25), inserts a new
`videoSummaryCache` row instead of attaching to the served one
(`VideoService.insertVersionRow` / `nextVersionNumber`).

- **Numbering is shared** by user and eval rows, so every row keeps a distinct
  versioned `dedupKey`. A user run takes `max(served version, highest row) + 1`; with
  no user version at all it falls through to the normal flow. An eval run takes
  `max(highest row, 1) + 1` — never version 1, whose `dedupKey` is the shared row new
  submitters attach to.
- **Collisions:** two concurrent user re-runs that compute the same number collide on
  the partial unique `dedupKey` index (E11000 → `VersionCreationError` "Version
  conflict - please retry"). An eval run is its own version by definition, so it
  renumbers and retries (up to 3 attempts) instead.
- **Two pools:** the user pool (`evalRun ≠ true`) and the eval pool (`evalRun: true`).
  Pruning keeps the 5 newest versions **per pool**, ranked by `version` (a pool's
  numbers have gaps), never deletes the served `isLatest` row, and never touches the
  other pool. Version lists show eval rows to the eval user only.
- **Served version = newest completed user row.** A new row is inserted
  `isLatest: false`; the summarizer's `completed` status (`/internal/status`) promotes
  it (`promoteCompletedVersion`: set self, demote older latest rows, yield to a newer
  one — concurrent completions converge on the newest). A `failed` re-run re-points the
  requester's library entries to the completed row still served
  (`restoreServedVersion`). Eval rows are never promoted.
- **Eval runs stay private:** no Redis response-cache write and no Qdrant points; the
  summarizer may still *answer* an eval run from the Redis response cache unless it
  was submitted with `bypassCache`.
- **Every (re-)dispatch** (new version, retry, stale-shared-row regen, stall recovery)
  `$unset`s the row's `synthesis` first, so the previous run's `masterSummary` can't
  block this run's merge (see `synthesis_complete` in API-REFERENCE).

### Worker skip (completed rows)

`drive_pipeline` (`services/summarizer/src/worker/pipeline.py`) acks a queued job
without running it when the row is already `completed` **by the current
`PIPELINE_VERSION`** — checked before taking the per-video lock and again once it
holds it (an SSE-started producer may have finished meanwhile; on prod 2026-10-05 a
duplicate run cost $0.16). A completed row stamped with an **older**
`pipelineVersion` still runs: the API's regen of a stale shared row for a first-time
submitter publishes before it sets the row back to `pending`, so a worker that picks
the job up first still finds it `completed` by the older version. The `pending` reset
matters on the HTTP path: the summarizer's stream replays a `completed` row instead of
running it. Unstamped rows count as current (same rule as the API's
`isStaleVersion`). A Mongo error on the post-lock re-check runs the job, and any
exception before the producer starts releases the lock first, so a retry is never
acked as `skip_locked`.

## Collection schema

`idempotencyKeys` collection, indexes:

| Index | Purpose |
|-------|---------|
| `{ hash: 1 } unique` | Hash lookup + race-safe insertion. |
| `{ expiresAt: 1 } expireAfterSeconds: 0` | TTL eviction (~60s precision). |
| `{ videoSummaryId: 1 } sparse` | Failure cascade — drop all keys for a failed summary. Sparse because pending placeholders carry no videoSummaryId. |
| `{ userId: 1, createdAt: -1 }` | Audit/debug; not on the hot path. |

## Cost protection

The gate runs **before** billing (the minutes ledger's quote + hold). A duplicate hit:

- Does NOT hold minutes.
- Does NOT enqueue a job (`queuePublisher.publishVideoJob` not called).
- Does NOT trigger HTTP summarization (`summarizerClient.triggerSummarization` not called).
- Returns instantly from a single Mongo read.

This is enforced by integration tests in `api/src/routes/videos.routes.test.ts`.

## Minutes-ledger holds

The minutes ledger (`api/src/services/billing/`) is idempotent on its own
keys, so a submission takes at most one hold however it is retried:

- **The run id is the server's.** `VideoSubmissionService` generates a fresh
  `billingRunId` (UUID) per submission that passes the request gate above —
  never the client's `X-Request-ID`, which a client may reuse. The hold, the
  dispatch stamp (`videoSummaryCache.billingRunId`, beside `requestId`) and the
  settle all use it; `requestId` stays the client / Fastify id for logs and is
  what the summarizer writes on `llm_usage.request_id`, so a settle sums the
  run's `cost_usd` by the stamped `requestId`. Each playlist item gets its own
  run id (used as its dispatch `requestId` too).
- **An Idempotency-Key replay or an HTTP retry holds nothing:** it returns
  `duplicate` / `in_flight` from the request gate before billing runs.
- **Ledger keys** (unique `ledgerEntries.idempotencyKey`):
  `hold:<runId>:free|plan|lot:<lotId>`, `debit:<runId>:…` (a second
  "completed" — translation — is a no-op), `release:<runId>:…`,
  `release-expire:<runId>:…`. A hold for a run id that already settled or
  released is refused (`HOLD_FINALIZED`), and an open hold is only ever
  returned for the same user and video (`HOLD_CONFLICT` otherwise) — both
  unreachable from a client now that the id is the server's.
- **Worker retries** re-run the same dispatch, so the same run id; one hold,
  one debit.
- **No run, no debit.** A dispatch the guard refused (another live run already
  owns the row) returns `attached`, so the hold goes back at once (review I1).
  A run the summarizer answered from its Redis response cache reports
  `completed` with `cacheHit: true` on `POST /internal/status`, and the hold is
  released as cached instead of settled (I3). A stalled row the API
  re-dispatches is set back to `pending` with a fresh `updatedAt`, so the
  summarizer's stall sweeper cannot fail the new run as the dead one (I2).

## DoS bound

An authenticated client can produce unique hashes by varying the `Idempotency-Key` header while keeping the YouTube URL constant. Each unique hash inserts one row in `idempotencyKeys` that lives until TTL (24h default). The blast radius is bounded by:

- The submit burst limit (`SUBMIT_BURST_LIMIT_PER_HOUR`, default 20 per user per hour, counted before the gate) — the per-user POST budget.
- The TTL index — old rows are reaped lazily (~60s precision) after `expiresAt`.
- Per-user authentication — every insert is tied to a real account.

If you need a tighter cap, add a `count({ userId, status: 'pending' })` guard in the route before `reserveHash`. The current design treats the hourly burst limit as the gating control.

## Frontend behaviour

On `result.duplicate === true`, the frontend shows a subtle sonner toast — *"Already processed — opening existing result"* — and navigates to the existing VideoDetail page. The toast helper lives at `apps/web/src/features/video-output/lib/duplicate-toast.ts` and is invoked from `VideoIntakeForm` and `BoardInlinePaste`.
