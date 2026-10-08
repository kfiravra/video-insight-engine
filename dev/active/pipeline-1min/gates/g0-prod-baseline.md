# 0.10 Prod baseline — accepted as partial (3/6 runs), 2026-10-08

Prod box on `c57c2a6` (PR #22; summarizer/worker/api rebuilt 2026-10-07 18:27 UTC). Source: prod Mongo
`videoSummaryCache.pipeline.timing` + `llm_usage` ledger (read-only). Raw: `g0-prod-baseline.json`.
Seconds, server-side. Client SSE offsets are discarded because the WSL clock runs **~6 % fast** relative to the
server (40 s server = 42.4 s local). That also applies to any wall time measured on the dev box.

**Trigger:** public demo login (`POST /api/auth/login {demo:true}`, no credentials handled) →
`POST /api/videos {url, bypassCache:true}` → SSE stream followed to `done`. One run at a time, no
pending/processing rows before each, ≥ 6 min apart so the prompt cache is cold. jMq8 r1 is the exception:
it started 3 min after T1d r1, so its plan read 14 k cached tokens ($0.026 vs $0.072–0.079).

**Decision (Kfir, 2026-10-08):** accepted as partial; the remaining three runs are not run. Cold
references below; warm re-runs are reported separately. All timings come from `pipeline.timing` only.

**Cold references** (gate targets compare against these)
| Video | Total | First tab | Meta | Frames (scene / vision / hires) | Plan | Extract | Synth ∥ enrich | Assembly (moment dl) | Cost |
|---|---|---|---|---|---|---|---|---|---|
| T1dQhQAm8Tc (STANDARD) ³ | 240.0 | ≈ 226 | — | 85.7 (— / 35.8 / —) | 27.9 + classifier 2.2 + chapters 3.4 | 76.0 | 4.6 ∥ 12.0 | — | $0.186 |
| jMq8lEu-of0 (HIGH), r1 | 201.8 | 201.7 | 3.9 ¹ | 106.0 (14.5 / 55.8 / 11.0) | 26.5 | 51.8 | 13.5 | 0.1 (prefetch reused) | $0.216 |
| uC45_4nnEAI (0 cand.), r1 | 168.6 | 164.2 | 13.1 | 58.5 (45.4 / — / —) | 24.2 | 58.5 | 9.9 | 4.4 (failed ²) | $0.114 |

**Warm re-runs** (S3 transcript + frame manifest; not comparable with cold runs)
| Run | Total | First tab | Meta | Frames | Plan | Extract | Synth ∥ enrich | Assembly (moment dl) | Cost |
|---|---|---|---|---|---|---|---|---|---|
| T1dQhQAm8Tc r1 | 147.1 | 134.0 | 11.6 | 0.1 (vision restored) | 38.5 (classifier 6.5) | 68.9 | 14.6 | 13.2 (12.0) | $0.129 |
| T1dQhQAm8Tc r2 | **failed** at metadata after 1.8 s, `VIDEO_UNAVAILABLE` (bot check), $0 | | | | | | | | |

³ The pre-phase-0 prod run (Mongo v1, `processingTimeMs` 239.5 s, created 2026-10-06 09:49 UTC, no
`pipeline.timing`). Phase walls from `evidence/A-DIGEST.md`; proxy 143 MB. uC45 counts as cold here:
the transcript came from S3, but scene detect re-runs on the 0-candidate path.
Replay comparison: T1d 239.0 vs 240.0; uC45 171.1 vs 168.6 (−1.5 %); jMq8 261.9 dev cassette vs 201.8 (−23 %).
Every run had `rateLimited` 0 and `fallbacks` 0; tabs planned 5/4/3 → assembled 6/5/4. Downloads:
T1d 1 file (54 MB); jMq8 2 files (161 MB); uC45 3 files (249 MB, incl. an unused 720p prefetch of 174 MB).
**Spend $0.458** (ledger = `pipeline.timing.costUsd` on every run).

## Surprises
- **`bypassCache` is not a cold run.** The transcript (S3) and frame manifest + vision descriptions
  (`phases/frames.py:170`) are reused, so re-runs skip download, scene detect, vision and hires. Only a
  first-ever run (jMq8 r1) measures frames. uC45's 0-candidate path writes no manifest, so it re-detects
  every time (45 s scene detect) and still prefetches a 720p it never uses.
- **YouTube bot check on prod (all users).** From 00:04 UTC the primary proxy exit gets "Sign in to
  confirm you're not a bot": first uC45's moment fill (no moment frames), then T1d r2's metadata. As of
  00:49 a no-write probe through the app's yt-dlp path fails for all 3 videos on the primary exit and passes
  on exits 2 and 3. `try_proxy_exits` rotates only on 429, so **every new prod submission fails at
  metadata until the exit clears**. These runs downloaded ~460 MB (uC45: 249 MB within 35 s at 00:01), which may
  have contributed. Kfir switched prod to exit 2 (workers restarted 2026-10-08 04:12 UTC). Follow-up
  → 1a: rotate on the bot check as on a 429, and choose the exit per job round-robin.
- **Prod side effects:** T1d's latest version is the failed v3, and the demo library's T1d row points to it.
  Kfir asked for one T1d re-run with an admin or eval account. **Still open:** prod has no eval user, and
  no admin login credentials exist locally or on the box (only `ADMIN_API_KEY`, which has no re-run
  route). It needs Kfir to create the eval user (`env-changes.md`) or supply admin credentials.
  jMq8 v1 is new in the demo library. The demo account (free tier, $1/day) was capped from 18:43 UTC to
  00:00 UTC on 10-07 ($1.05; it was at $0.70 before these runs).
- Moment fill re-downloads 720p in assembly on warm runs (T1d: 12 s between first tab and `complete`).

## gpt-4o-mini sub-second "Timeout"
Prod logs show `litellm.Timeout: APITimeoutError … timeout value=30.0, time taken=0.03 s` (and 0.05 s
against 90 s, 0.61 s against 30 s). These are transport failures, not real timeouts. LiteLLM 1.91.0 uses its aiohttp
transport by default (`DISABLE_AIOHTTP_TRANSPORT` unset), and that transport maps any `asyncio.TimeoutError`
to `httpx.TimeoutException`, which the OpenAI SDK reports as `APITimeoutError`.
- **Scale:** 4 of 187 prod gpt-4o-mini calls since 2026-10-04 (2 %), all OpenAI and none Anthropic. One
  is from 10-05, so it **predates phase 0**.
- **Pattern:** 3 of 4 hit the first OpenAI call after a 50–120 s OpenAI-idle gap (3/39 calls in that
  window vs 1/118 under 30 s and 0/25 over 120 s). That fits stale keep-alive connection reuse.
- **Impact:** synthesis and translation retried fine (+~1 s). **Description analysis has no retry**, so jMq8 r1
  ran without it.
- **Cause:** not a prod config error (the limits are honoured). It's a provider/transport issue → 1d.5.
  Candidates: retry `APITimeoutError` under ~2 s at once (description analysis included), or A/B
  `DISABLE_AIOHTTP_TRANSPORT=true` (env change → `env-changes.md`).
