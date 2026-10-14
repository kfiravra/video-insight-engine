# Error Handling

Error codes, recovery strategies, and user messages.

---

## Error Response Format

All errors follow this structure (resolved by `resolveErrorEnvelope` in
`api/src/utils/error-envelope.ts`, emitted by the global error handler in
`api/src/app.ts`, and enforced by `api/src/utils/error-envelope.test.ts` plus
the envelope tests in `api/src/app.test.ts`):

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable message",
  "statusCode": 400,
  "details": {}
}
```

- `error`, `message`, `statusCode` are always present.
- `details` is **optional** — currently only `VALIDATION_ERROR` carries it
  (`details.issues: [{ path, message }]` from the Zod issue list).
- `DAILY_LIMIT_REACHED` additionally carries top-level `resetAt` + `limitUsd`
  (consumed by the UI countdown).

---

## Error Codes by Category

### Authentication Errors (401)

Emitted by the `authenticate` decorator (`api/src/plugins/jwt.ts`) and
`POST /api/auth/refresh` (`api/src/routes/auth.routes.ts`).

| Code              | Message                       | When                                                                    | Client reaction               |
| ----------------- | ----------------------------- | ----------------------------------------------------------------------- | ----------------------------- |
| `UNAUTHORIZED`    | Authentication required       | No token provided                                                        | Log in                        |
| `TOKEN_EXPIRED`   | Token has expired             | Access token expired                                                     | Call `/api/auth/refresh`      |
| `TOKEN_INVALID`   | Invalid token                 | Token malformed/tampered, or a refresh token presented as a bearer token | Log out (token is unusable)   |
| `REFRESH_EXPIRED` | Session expired, please login | `/api/auth/refresh` cookie missing/expired/malformed/type-confused (deliberately one code for all failure modes — no cookie-validity oracle) | Re-login |

### Authorization Errors (403)

| Code                       | Message                                                          | When                                                    |
| -------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------- |
| `ACCOUNT_DELETION_PENDING` | This account is scheduled for deletion. Contact support to cancel. | Valid JWT for a soft-deleted account (GDPR grace window) |

> There is deliberately **no** generic `FORBIDDEN` code: ownership checks
> return `404 NOT_FOUND` instead of 403 so the API never confirms that a
> resource exists but belongs to someone else (anti-enumeration). Rate
> limiting is `429 RATE_LIMITED`, not 403 (see next section).

### Rate Limit Errors

See [docs/SECURITY.md](./SECURITY.md#rate-limiting) for implementation.

| Code                  | Status | Message                                       | When                                                                            |
| --------------------- | ------ | --------------------------------------------- | ------------------------------------------------------------------------------- |
| `RATE_LIMITED`        | 429    | Too many requests                             | Rate limit exceeded (global plugin or per-route quota)                          |
| `DAILY_LIMIT_REACHED` | 429    | You've reached your daily cost limit ($X.XX)  | Per-user LLM cost cap reached. Response includes `resetAt` + `limitUsd` fields. |

### Validation Errors (400)

| Code               | Message              | When                       |
| ------------------ | -------------------- | -------------------------- |
| `INVALID_URL`      | Invalid YouTube URL  | URL doesn't match patterns |
| `INVALID_EMAIL`    | Invalid email format | Email validation failed    |
| `WEAK_PASSWORD`    | Password too weak    | Doesn't meet requirements  |
| `VALIDATION_ERROR` | Validation failed    | Generic Zod error          |

### Video Processing Errors (400/422)

| Code                | Message                                  | When               |
| ------------------- | ---------------------------------------- | ------------------ |
| `NO_TRANSCRIPT`     | No transcript available for this video   | Captions disabled  |
| `VIDEO_TOO_LONG`    | Video exceeds maximum duration           | > `MAX_VIDEO_DURATION_MINUTES` (summarizer; config default 600 min, the `.env` templates set 30) |
| `VIDEO_TOO_SHORT`   | Video too short to summarize             | < 60 seconds       |
| `VIDEO_UNAVAILABLE` | Video is private or unavailable          | Can't access       |
| `VIDEO_RESTRICTED`  | Age-restricted video not supported       | Requires auth      |
| `LIVE_STREAM`       | Live streams not supported               | Is a live stream   |
| `PROCESSING_FAILED` | Failed to process video                  | LLM/internal error |
| `RATE_LIMITED`      | YouTube rate limited transcript fetch    | 429 from YouTube captions (only surfaces if audio fallback is unavailable — see SERVICE-SUMMARIZER) |

### Resource Errors (404)

| Code               | Message            | When                        |
| ------------------ | ------------------ | --------------------------- |
| `NOT_FOUND`        | Resource not found | Generic 404                 |
| `VIDEO_NOT_FOUND`  | Video not found    | userVideo doesn't exist     |
| `FOLDER_NOT_FOUND` | Folder not found   | folder doesn't exist        |

### Server Errors (500)

| Code                  | Message                         | When                  |
| --------------------- | ------------------------------- | --------------------- |
| `INTERNAL_ERROR`      | Something went wrong            | Unexpected error      |
| `SERVICE_UNAVAILABLE` | Service temporarily unavailable | Dependency down       |
| `SUMMARIZER_PURGE_FAILED` | Video purge failed | Summarizer could not purge Qdrant/S3/Redis; nothing deleted in Mongo — retry the admin delete |
| `LLM_ERROR`           | AI service error                | Claude API failed     |
| `DATABASE_ERROR`      | Database error                  | MongoDB failed        |
| `SUMMARIZER_ERROR`    | Summarizer service error        | Summarizer unreachable |

---

## Video Processing Edge Cases

### Processing Flow

```
URL submitted
      │
      ▼
┌─────────────────┐
│ Validate URL    │──── Invalid ────► INVALID_URL
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Check cache     │──── Exists ────► Return cached (Redis → MongoDB)
└────────┬────────┘
         │ Miss
         ▼
┌─────────────────┐
│ Fetch metadata  │──── Failed ────► VIDEO_UNAVAILABLE
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Check duration  │──── > MAX_VIDEO_DURATION_MINUTES ► VIDEO_TOO_LONG
└────────┬────────┘     < MIN_VIDEO_DURATION_SECONDS ► VIDEO_TOO_SHORT
         │
         ▼
┌─────────────────┐
│ Check type      │──── Live ──────► LIVE_STREAM
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Fetch transcript│──── None ──────► NO_TRANSCRIPT
└────────┬────────┘     Restricted ► VIDEO_RESTRICTED
         │
         ▼
   HTTP POST to summarizer
         │
         ▼
┌─────────────────┐
│ Tier probe      │──── LLM guesses domain + format (+ transcript_usable)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Plan ∥ memory   │──── LLM designs tab layout + content tags; reconcile at plan-done
└────────┬────────┘
         │
         ▼
   Field-group extraction → Assembly (+ moment fill ∥ synthesis ∥ quiz) → Translation
```

> **Note:** Domain and format come from the tier probe (`pipeline/tier_probe.py`) and the plan
> (`pipeline/plan.py`); the old classifier and the legacy "persona detection" system are gone.
> Call order: [summarizer-workflow.md](./summarizer-workflow.md).

### Implementation

```typescript
// summarizer/src/services/transcript.py

async def validate_video(youtube_id: str) -> VideoValidation:
    """Validate video before processing."""

    # Fetch metadata
    try:
        metadata = await get_video_metadata(youtube_id)
    except VideoNotFoundError:
        return ValidationError("VIDEO_UNAVAILABLE")

    # Check if live
    if metadata.is_live:
        return ValidationError("LIVE_STREAM")

    # Check duration — the real check is pipeline_helpers.validate_duration (summarizer
    # metadata phase; the API does not check duration): it raises a TranscriptError
    if metadata.duration > settings.MAX_VIDEO_DURATION_MINUTES * 60:  # config default 600
        return ValidationError("VIDEO_TOO_LONG")
    if metadata.duration < settings.MIN_VIDEO_DURATION_SECONDS:  # 60
        return ValidationError("VIDEO_TOO_SHORT")

    # Check transcript
    try:
        transcript = await fetch_transcript(youtube_id)
        if not transcript:
            return ValidationError("NO_TRANSCRIPT")
    except RestrictedVideoError:
        return ValidationError("VIDEO_RESTRICTED")

    return VideoValidation(
        valid=True,
        metadata=metadata,
        transcript=transcript
    )
```

### User-Facing Messages

The SSE stream's messages come from `CODE_MESSAGES` in
`apps/web/src/features/video-output/lib/streaming/stream-error-messages.ts`
(`getUserFriendlyError(message, code)`):

| Error             | User Message |
| ----------------- | ------------ |
| NO_TRANSCRIPT     | "This video doesn't have captions available. Videos need captions or subtitles to be summarized." |
| VIDEO_TOO_LONG    | "This video is too long to summarize. Maximum duration is 4 hours." |
| VIDEO_TOO_SHORT   | "This video is too short to summarize. Minimum duration is 30 seconds." |
| VIDEO_UNAVAILABLE | "This video is unavailable. It may be private, deleted, or region-restricted." |
| VIDEO_RESTRICTED  | "This video has restrictions that prevent it from being processed." |
| LIVE_STREAM       | "Live streams cannot be summarized. Please wait until the stream ends." |

The "4 hours" / "30 seconds" in these texts do not match the summarizer's limits
(`MAX_VIDEO_DURATION_MINUTES`, `MIN_VIDEO_DURATION_SECONDS` = 60).

---

## Retry Strategy

### Automatic Retries (Internal)

Two layers, both real code:

- **LLM calls** — summarizer: `call_llm_with_retry` (`utils/llm_retry.py`) retries
  transient errors on the same model, then falls back to `LLM_FALLBACK_PROVIDER`
  (see [LLM Error Handling](#llm-error-handling)); LiteLLM's own retries and
  fallbacks are off. A refused answer is never retried: it is asked once of the
  previous model ([Refusals](#refusals)). The assistant keeps LiteLLM `num_retries` (`LLM_NUM_RETRIES`).
  The extraction field groups run up to `EXTRACTION_PARALLEL_BATCHES` (6) calls at once per run (× `WORKER_CONCURRENCY` per worker);
  a group whose answer is lost (not JSON, none of its keys, a broken warm-up stream) gets one
  more call, then counts as failed — its tabs drop or demote and the run is `degraded`; only
  every group failing is an `LLM_ERROR` failure. With `EXTRACTION_PARALLEL=false` chunked
  batches run one at a time, and a batch still rate-limited after its retries re-runs in a
  sequential second pass (2 s apart). `pipeline.timing` counts 429s per run — drop the setting to 4 if any appear.
- **Queue jobs** — `services/summarizer/src/worker/runner.py`: a pipeline exception
  with `attempt < WORKER_MAX_RETRIES` (3) waits
  `WORKER_RETRY_BACKOFF_SECONDS × 2^(attempt-1)` (5 → 10 → 20 s, capped 120 s) and
  republishes with `attempt + 1`; at the cap the message is dead-lettered
  (`reject(requeue=False)` → DLQ) and one Sentry event fires. Validation errors go
  straight to the DLQ. Replay via `POST /api/admin/queue/replay`.

### Stalled runs (self-healing)

A producer that dies mid-run leaves the cache row `processing`. Two mechanisms
recover it:

- **Lazy (API)** — `video.service.ts`: a resubmission of the same video whose row
  is `pending`/`processing` and untouched for 30 min re-dispatches.
- **Active (summarizer stall sweeper)** — `src/services/stall_sweeper.py`, every
  `STALL_SWEEP_INTERVAL_SECONDS` (300): rows `processing` with `updatedAt` older
  than `STALL_THRESHOLD_MINUTES` (30) **and** no live Redis producer lock are
  flipped to `failed` (`errorCode: UNKNOWN_ERROR`, message "Pipeline stalled…"),
  the `video.status` callback runs (userVideos, WS broadcast, dispatch-guard
  release) and a `pipeline_stalled` alert is written + delivered. The flip is an
  atomic compare-and-set (`mongodb_repository.mark_stalled_failed`, filtered on
  `status == processing` **and** the stale `updatedAt`): if a resumed producer or
  the API's lazy re-dispatch bumped the row between the sweeper's find and its
  write, the update matches nothing and the live run is left alone — no callback,
  no alert. The lock check fails open, so a Redis outage pauses sweeping instead
  of mass-failing live runs.

### YouTube Transcript Rate Limit Retry

A caption 429 is IP-scoped, so the retry strategy depends on how many proxy exits are available (`services/summarizer/src/services/transcription/transcript.py`):

- **Direct connection or one exit** — tenacity retries on the same IP with exponential backoff:

  ```python
  @tenacity.retry(
      stop=tenacity.stop_after_attempt(3),
      wait=tenacity.wait_exponential(multiplier=2, min=4, max=30),
      retry=tenacity.retry_if_exception(_is_rate_limit_error),
      reraise=True,
  )
  def _fetch_transcript_sync(video_id: str) -> tuple[list[dict], str, str, str | None]:
      return _fetch_once(video_id, ytdlp_proxy_url())
  ```

- **`YOUTUBE_PROXY_EXIT_COUNT` > 1** (sticky Webshare exits `USERNAME-1…N`) — `_fetch_rotating_sync` tries the next exit on each 429 instead of waiting (`download_utils.ytdlp_proxy_exit_urls`, at most 3 exits, one shared loop `download_utils.try_proxy_exits`). The same rotation covers YouTube's "Sign in to confirm you're not a bot" check on every proxied call — metadata `extract_info`, the low-res/720p/audio downloads and playlist lookups as well as captions (`download_utils.is_exit_blocked`); other errors never rotate, and when every exit is blocked the call fails with its usual error. The exit that last worked starts the next rotation in that process (`download_utils.record_working_exit`). The metadata-phase timedtext fetch (`youtube._fetch_subtitle_data_sync`) rotates the same way, since its URL is not bound to the exit that produced it. When every exit it tried returned 429, the 15-minute `vie:captions:429` marker is written and the caption API (same exits) is skipped for audio. The marker is also written when the caption API's own rotation ends in 429 on every exit.

Rate limit detection matches the library's `RequestBlocked` by type — the parent of `IpBlocked` (429 / reCAPTCHA), also raised for the "confirm you're not a bot" check. Both lose their "429" / "too many requests" text once a proxy config is attached, so a proxied bot check still rotates exits and writes the marker. Anything else falls back to a text match:
```python
def _is_rate_limit_error(exception: BaseException) -> bool:
    if isinstance(exception, RequestBlocked):
        return True
    error_str = str(exception).lower()
    return any(x in error_str for x in ["429", "too many", "rate limit"])
```

When all retries are exhausted the 429 becomes a `RATE_LIMITED` `TranscriptError`, which **falls back to audio transcription** (Whisper/Gemini) rather than failing the pipeline — the audio download is a separate, un-throttled YouTube endpoint. `RATE_LIMITED` only reaches the user when audio fallback is unavailable (`WHISPER_ENABLED=false`, video over `WHISPER_MAX_DURATION_MINUTES`, or audio transcription also fails). See the audio-fallback gate in [SERVICE-SUMMARIZER](./SERVICE-SUMMARIZER.md).

### Whisper deadline + partial-transcript return

Whisper chunked transcription degrades gracefully instead of failing a long
video. Each chunk has a per-request timeout (`WHISPER_CLIENT_TIMEOUT_SECONDS`,
300s) with `WHISPER_MAX_RETRIES=1`, and chunks transcribe concurrently bounded
by `WHISPER_CHUNK_CONCURRENCY` (3). Chunk 0 always runs; later chunks are
**skipped once a soft deadline is reached**, and if any chunk fails or is
skipped the pipeline returns a **partial transcript** (possibly missing the end
of a long video) rather than raising a `TranscriptError`. The partial output is
still usable downstream.

```python
# services/summarizer/src/services/summarizer.py

async def run_summarization(
    video_summary_id: str,
    youtube_id: str,
    url: str,
    user_id: str,
):
    """Process with retry logic."""

    for attempt in range(RETRY_CONFIG["max_retries"]):
        try:
            result = await summarize_video(youtube_id)
            return result

        except RetryableError as e:
            if attempt < RETRY_CONFIG["max_retries"] - 1:
                delay = RETRY_CONFIG["backoff"][attempt]
                logger.warning(f"Retry {attempt + 1}, waiting {delay}s")
                await asyncio.sleep(delay)
            else:
                # Max retries reached - mark as failed
                await update_status(video_summary_id, "failed", error=str(e))
                raise
```

### Manual Retry (User-Initiated)

```
POST /api/videos/:id/retry
```

Available when:

- Status is `failed`
- Error is retryable (not INVALID_URL, VIDEO_TOO_LONG, etc.)
- Within 24 hours of failure

Response:

```json
{
  "video": {
    "id": "...",
    "status": "pending",
    "retryCount": 1
  }
}
```

Implementation:

```typescript
// api/src/routes/videos.routes.ts

fastify.post("/videos/:id/retry", async (req, reply) => {
  const video = await findUserVideo(req.params.id, req.user.id);

  if (!video) {
    return reply.code(404).send({ error: "VIDEO_NOT_FOUND" });
  }

  // Check if retryable
  const nonRetryableErrors = [
    "INVALID_URL",
    "VIDEO_TOO_LONG",
    "VIDEO_TOO_SHORT",
    "NO_TRANSCRIPT",
    "VIDEO_RESTRICTED",
    "LIVE_STREAM",
  ];

  if (video.status !== "failed") {
    return reply.code(400).send({
      error: "INVALID_STATE",
      message: "Video is not in failed state",
    });
  }

  if (nonRetryableErrors.includes(video.errorCode)) {
    return reply.code(400).send({
      error: "NOT_RETRYABLE",
      message: "This error cannot be retried",
    });
  }

  // Reset and re-trigger
  await updateVideoCache(video.videoSummaryId, {
    status: "pending",
    errorMessage: null,
    errorCode: null,
    retryCount: video.retryCount + 1,
  });

  // Trigger summarizer via HTTP
  await triggerSummarization({
    videoSummaryId: video.videoSummaryId,
    youtubeId: video.youtubeId,
    url: video.url,
    userId: req.user.id,
  });

  return {
    video: {
      id: video._id,
      status: "pending",
      retryCount: video.retryCount + 1,
    },
  };
});
```

---

## Failed Jobs Tracking

Failed jobs after max retries are tracked in MongoDB:

```python
# services/summarizer/src/services/mongodb.py

async def mark_job_failed(
    video_summary_id: str,
    error_code: str,
    error_message: str,
    attempts: int,
):
    """Mark job as failed after max retries."""
    await db.videoSummaryCache.update_one(
        {"_id": ObjectId(video_summary_id)},
        {
            "$set": {
                "status": "failed",
                "errorCode": error_code,
                "errorMessage": error_message,
                "attempts": attempts,
                "failedAt": datetime.utcnow(),
            }
        }
    )
```

Failed job record:

```json
{
  "_id": "507f1f77bcf86cd799439020",
  "youtubeId": "dQw4w9WgXcQ",
  "status": "failed",
  "errorCode": "LLM_ERROR",
  "errorMessage": "Rate limit exceeded",
  "attempts": 3,
  "failedAt": "2024-01-15T10:00:00Z"
}
```

### Monitoring Failed Jobs

```python
# Query failed jobs
async def get_failed_jobs_count() -> int:
    return await db.videoSummaryCache.count_documents({
        "status": "failed",
        "failedAt": {"$gte": datetime.utcnow() - timedelta(hours=24)}
    })

# Alert if too many failures
if await get_failed_jobs_count() > 10:
    await send_alert("More than 10 failed jobs in last 24h")
```

---

## LLM Error Handling

Every summarizer stage calls the LLM through `call_llm_with_retry`
(`services/summarizer/src/utils/llm_retry.py`); frame vision runs its own retry
with the same error classes (`TRANSIENT_LLM_ERRORS`). Each attempt is one request
to one model (LiteLLM `num_retries=0`, no LiteLLM `fallbacks`), so telemetry and
the Langfuse generation always name the model that actually answered.

### Request rules (per model)

Every request passes `llm_model_rules.request_for_model` on its way to LiteLLM
(`LLMProvider._ask`, the one `acompletion` seam), keyed by the model it goes to,
never by call site:

- **Thinking off + effort.** Claude Haiku 5.5 gets `thinking: {"type": "disabled"}`,
  Claude Sonnet 5.5 `{"type": "between_tools"}` (the only off shape it accepts;
  any other model rejects it), each with `output_config.effort` — `low` by
  default, a caller may pass `effort` (the plan sends `medium`,
  `plan.PLAN_EFFORT`). An OpenAI gpt-5 / gpt-6 reasoning model whose LiteLLM map
  entry supports it (gpt-6-luna) gets `reasoning_effort="none"`. Only the answer's
  text blocks are read (`message.content`), never thinking blocks.
- **Sampling.** `temperature` / `top_p` / `top_k` are dropped, with a warning, for a
  model that rejects them — by LiteLLM's own predicates on the effort the request
  carries: an Anthropic model whose map says `supports_sampling_params: false`
  (the 5.5 models), a gpt-5 / gpt-6 model unless its effort resolves to `none`
  (gpt-6-luna at `none` keeps it). `temperature=1` always passes.
- **No assistant prefill.** A request whose last message is the assistant's raises
  `AssistantPrefillError` before LiteLLM (a programming error — the 5.x models
  reject prefill; a reply format belongs in `response_format`). None exists today.

Haiku 4.5, Sonnet 4.6, gpt-4o-mini and gpt-4o match none of these rules: their
requests are byte-identical to the pre-4.0b ones. `llm_model_rules` also registers
a Haiku 5.5 entry in LiteLLM's model map when the loaded map lacks one (1.105.0's
bundled map does): LiteLLM keys the thinking / effort mapping, the sampling gate
and `completion_cost` on it.

### Errors

| Error | Action |
| --- | --- |
| Timeout, 429, 5xx, connection error, empty reply | Retry (the stage's `max_retries` budget) |
| Connection error / LiteLLM `Timeout` in under 1 s (a dropped pooled connection — the request never reached the model) | Re-sent at once, once per call, outside the retry budget |
| 400 / 401 / 403 / 404 / 422, programming errors (`AssistantPrefillError` included) | Raise on first occurrence — another attempt cannot fix them |
| The same, on a **fallback** attempt (e.g. the fallback provider's key is missing) | The call ends with `None` like any other failed attempt — the last resort never turns a recoverable stage failure into a crash |
| Refusal (HTTP 200, the model declined) | Asked once of the previous model inside the same attempt; no answer → `LLMRefusalError` ends the call — no retry, no cross-provider fallback ([Refusals](#refusals)) |

Order per call: attempt 1 → (same-model retries: provider `retry-after-ms` /
`retry-after` header, capped at 20 s, else a linear backoff 1 s, 2 s, …) → the
fallback model when one applies (no wait on the switch). A stage that gets `None`
takes its own fallback; a stage that gets a raised 400 fails unless it catches it.

**Credit exhaustion is a non-retryable 400.** Anthropic's "Your credit balance is
too low" arrives as `BadRequestError` (HTTP 400): it is not retried and does not
fall back cross-provider (LiteLLM's old built-in fallbacks used to mask it with
gpt-4o), so every Anthropic call fails on its first attempt; stages that catch
take their fallback, extraction does not, so the run fails (6 of 18 golden-set
runs on 2026-10-08). Top up the account; whether billing 400s
should take the cross-provider fallback is an open phase-1 gate decision.

### Fallback

`LLM_FALLBACK_PROVIDER` (blank = none) names a cross-provider fallback for
**primary-model** calls: the first try and ONE same-provider retry, then the
fallback provider's default model for the remaining attempts
(`max_retries` 1 or 2 → primary, primary, fallback; 3 → primary, primary,
fallback, fallback; 0 → a single attempt). Fast-model calls never fall back, and
a fallback on the primary's own provider is ignored. A fallback attempt is tagged
`fallbackFrom` in the span metadata and counted in `pipeline.timing`
(`counts.fallbacks`).

When every attempt fails the wrapper returns `None` and the stage takes its own
fallback (fallback plan, no memory, metadata tier, …); the parallel batch
extractor alone asks for the last rate-limit error to be re-raised
(`propagate_rate_limit`) so it can re-run that batch sequentially — never when
the call ended on a refusal, which would send it back to the model that refused.

### Refusals

A refusal is an HTTP 200 whose answer the model declined: Anthropic
`stop_reason: "refusal"` (LiteLLM: `finish_reason="content_filter"`), OpenAI's
non-streamed `message.refusal`, or any provider's `content_filter` finish
(`llm_common.refusal.refusal_of`). In `LLMProvider._run_completion`:

1. The declined answer is recorded (a `pipeline.timing` `llmFailures` row
   `error: "Refusal"` with its tokens and cost, a Langfuse generation with
   `refusal: true`, a warning log).
2. The **same call** is asked once of the refused model's previous model
   (`config.PREVIOUS_MODEL`: `claude-sonnet-5-5` → `claude-sonnet-4-6`,
   `claude-haiku-5-5` → `claude-haiku-4-5-20251001`, `gpt-6-luna` →
   `gpt-4o-mini`), shaped by that model's own request rules, within the same
   attempt and the call's deadline (`call_deadline` — see below for the stages
   it does not cover). Never chained, never back to the refused
   model. Its answer is the call's answer (`fallbackUsed`, `refusedModel` in
   `pipeline.timing`; `fallback_from` in `llm_usage`).
3. No previous model configured (every model but those three — Haiku 4.5,
   Sonnet 4.6, gpt-4o-mini, gpt-4o, Gemini), the previous model refuses too, its
   request errors, or the call's deadline passes during the re-send
   (`llm_call.call_deadline` turns that timeout into the refusal) →
   `LLMRefusalError(model, category)`.

`call_deadline` bounds only calls made through `LLMService.call_llm` /
`call_llm_fast` (`llm.py`). A stage cap that sits outside it — frame vision's
`asyncio.wait_for` (`frame_analyzer._send_batch`) and the description analysis'
30 s total cap (`description_analyzer._request_analysis`) — cancels a re-send in
flight as a plain timeout: it is recorded under the refused model, without
`refusedModel`, and the stage treats it as its own timeout.

`LLMRefusalError` is not transient: `call_llm_with_retry` ends the call there —
no same-model retry, no cross-provider fallback — and returns `None` (the stage's
own fallback), or raises it when the caller passes `propagate_refusal=True`. The
split extraction's warm-up call (each chunk's text1) is the one caller that does:
a refused warm-up releases the waiting groups and counts as failed instead of
taking the extra call a broken stream gets. Frame vision counts the batch as
failed (not retried).

Not detectable today: an OpenAI refusal on a **streamed** call (LiteLLM 1.105.0
drops the refusal deltas, so it reads as an empty answer and is retried on the
same model). Under the default config no pipeline OpenAI call streams — the one
streamed call, the split extraction's warm-up, goes to Anthropic; but
`on_first_chunk` streams every attempt, so with `LLM_FALLBACK_PROVIDER=openai`
the warm-up's cross-provider fallback attempt streams on `gpt-4o`. Refused attempts are billed and
priced: they count in `pipeline.timing.costUsd` and in the per-user daily cap
(their `llm_usage` rows are `success: true`).

---

## Frontend Error Handling

### API Client

```typescript
// web/src/api/client.ts

class ApiClient {
  private token: string | null = null;

  async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const res = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      credentials: "include", // For cookies
      headers: {
        "Content-Type": "application/json",
        ...(this.token && { Authorization: `Bearer ${this.token}` }),
      },
    });

    if (res.status === 401) {
      // Try refresh
      const refreshed = await this.refreshToken();
      if (refreshed) {
        return this.request(endpoint, options); // Retry
      }
      // Redirect to login
      window.location.href = "/login";
      throw new Error("Session expired");
    }

    if (!res.ok) {
      const error = await res.json();
      throw new ApiError(error.error, error.message, res.status);
    }

    return res.json();
  }

  private async refreshToken(): Promise<boolean> {
    try {
      const res = await fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });

      if (res.ok) {
        const { accessToken } = await res.json();
        this.token = accessToken;
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }
}
```

### User Notifications

Stream errors are mapped by `getUserFriendlyError(message, code)`
(`apps/web/src/features/video-output/lib/streaming/stream-error-messages.ts`): the
`ErrorCode` first (`CODE_MESSAGES`, see [User-Facing Messages](#user-facing-messages)),
then regex `PATTERNS` over the raw message (timeout, rate limit, network, missing
captions, unavailable video, 401, 5xx), else a default ("Something went wrong while
processing this video. Please try again.").

---

## Monitoring Errors

### Error Logging

```typescript
// api/src/utils/logger.ts

export function logError(error: Error, context: object) {
  logger.error({
    error: {
      code: error.code || "UNKNOWN",
      message: error.message,
      stack: error.stack,
    },
    context: {
      userId: context.userId,
      endpoint: context.endpoint,
      method: context.method,
      requestId: context.requestId,
    },
    timestamp: new Date().toISOString(),
  });
}
```

### Alert Thresholds

Every alert is a row in Mongo `llm_alerts` (admin UI → Alerts) **and** a JSON POST
to `ALERT_WEBHOOK_URL` when set. Unset webhook = nobody is told.

| `type` | Source | Trips when | Re-alert cooldown |
| --- | --- | --- | --- |
| `high_cost_call` | llm-common usage callback (inline, every service) | one LLM call costs > `ALERT_COST_THRESHOLD_USD` (0.50) | none (per call) |
| `daily_spend_spike` | admin `alert_evaluator` (5 min) | today's spend > `daily_spike_multiplier` × prorated 7-day baseline (baseline ≥ $0.50/day) | 6 h |
| `high_failure_rate` | admin `alert_evaluator` (5 min) | failures / calls over the last hour > `failure_rate_threshold`, sample ≥ 10 | 1 h |
| `backup_stale` | admin `alert_evaluator` (5 min) | newest `backups/*/manifest.json` older than `BACKUP_MAX_AGE_HOURS` (26) | 6 h |
| `pipeline_stalled` | summarizer stall sweeper (5 min) | `processing` row idle > `STALL_THRESHOLD_MINUTES` with no producer lock | none (row is flipped to `failed`) |

`daily_spike_multiplier` / `failure_rate_threshold` are edited via admin
`POST /alerts/config`; the per-call threshold is env-only.

---

## Error Handling Checklist

Before deploying:

- [ ] All API endpoints return consistent error format
- [ ] Validation errors include field-level details
- [ ] Rate limit responses include Retry-After header
- [ ] Frontend handles 401 with token refresh
- [ ] Video edge cases detected before processing
- [ ] Retry logic with exponential backoff
- [ ] Failed jobs tracked in MongoDB
- [ ] Error logging with request context
- [ ] Alerts configured for critical thresholds
