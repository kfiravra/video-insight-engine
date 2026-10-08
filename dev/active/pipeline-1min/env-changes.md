# pipeline-1min — Env and secrets changes (apply at the END of the task)

Rule (Kfir, 2026-10-07): no prod `.env` edit until the task ends. Every merge must run on prod with the
current `.env`, so every new or changed setting ships with a safe default in `config.py` AND a literal
default in both compose anchors. This file lists what to change once everything has merged.

Prod today (read from `docker compose config` on the box, 2026-10-07):
`LLM_EXTRACTION_MODEL=anthropic/claude-haiku-4-5-20251001`, `LLM_ENRICHMENT_MODEL=openai/gpt-4o-mini`,
`LLM_VISION_MODEL=anthropic/claude-sonnet-4-6`, `LLM_MODEL` / `LLM_FAST_MODEL` blank (config defaults).

## Prod `.env` (`.env.production` on the box)

| Setting | Value | Why | Added by | Needed before |
|---|---|---|---|---|
| **URGENT (incident 2026-10-08)** `YOUTUBE_PROXY_EXIT_COUNT` | `3` (or the number of sticky exits on the plan) | YouTube bot-check blocks the main exit → every submission fails. Today's code rotates only on caption 429s; after fix 7304540 deploys it also rotates on the bot check for metadata/downloads/audio. Stopgap before the deploy: point `YOUTUBE_PROXY_URL` at a working sticky session (`-3`). Recreate vie-summarizer + vie-summarizer-worker (no rebuild). | incident / 7304540 | NOW (stopgap) and with the phase-1 deploy |
| check `LLM_CLASSIFIER_MODEL` (should be unset/blank or Haiku 4.5) | unset → Haiku default | compose now passes it through (review fix G07-1); an old classifier-era value (e.g. gpt-4o-mini) would move the tier probe off Haiku (D21) | review fixes | before the phase-1 deploy |
| check for newly passed-through keys: `grep -E '^(EXTRACTION_\|FRAME_(VISION\|TIER)\|CHUNKED_EXTRACTION_THRESHOLD\|MAX_(TOKENS\|MINUTES)_PER_BATCH\|TRANSCRIPT_CLEANING_ENABLED\|LLM_CLASSIFIER_MODEL)' .env` | expect none (or intended values) | compose ignored these keys before phase 1; any line becomes live with the merge | 1d.6 + review fixes | before the phase-1 deploy |
| remove `SCENE_HIRES_TIMEOUT`, `FRAME_EXTRACTION_ENABLED`, `MAX_FRAMES_PER_VISUAL`, `MAX_FRAMES_PER_CHAPTER`, `FRAME_MIN_SPACING_SECONDS`, `FRAME_WITHIN_BLOCK_DEDUP_THRESHOLD` if present | (delete the lines) | settings deleted in 1a.2 (bf618ee) — ignored by the code now; cleanup only | 1a.2 | optional, any time |

## GitHub secrets (scheduled eval, prod-API mode — D13)

| Secret | Purpose | Status |
|---|---|---|
| `EVAL_API_URL` | prod API base URL the scheduled eval targets | to add at the end |
| `EVAL_USER_EMAIL` | eval user login | to add at the end |
| `EVAL_USER_PASSWORD` | eval user login (exists today) | exists |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | dataset-run upload + faithfulness/format read-back (a primary metric — the live job skips without them) | exist — must be the **prod** Langfuse project's keys (the traces of prod runs live there) |

Not needed on GitHub any more (0.7): `ANTHROPIC_API_KEY` (no compose boot on the runner; only if the
0.8 tier-probe A/B ever runs in CI). `LANGFUSE_BASE_URL` defaults to `https://cloud.langfuse.com`.

After the secrets land (0.7): in `.github/workflows/eval.yml` add under `on:` (next to
`workflow_dispatch:`)

```yaml
  schedule:
    # Golden eval twice a month — 1st and 15th, 03:00 UTC (D13).
    - cron: "0 3 1,15 * *"
```

then dispatch once with `mode=eval, limit=1` to prove the secrets. The jobs' `if:` conditions already
handle `schedule` (dry-run + retrieval skip, live runs with `mode=eval`, `limit=0`).

## Prod data / accounts

| Change | Why | Status |
|---|---|---|
| Prod eval user (`EVAL_USER_EMAIL`) created by an admin — prod runs `ALLOW_REGISTRATION=false`, so the eval's register call gets 403 and falls through to login | the live eval logs in as this user | gate-0 decision (0.7) |
| Prod eval user `tier: team` (`USER_COST_LIMIT_TEAM=-1`) — prod `USER_COST_LIMIT_FREE=1` USD/day stops a free user after ~5 runs; `pro` (20 USD) also fits 18 runs | 18 runs per scheduled eval ≈ $3.5–6 | gate-0 decision (0.7) |
| Dev `eval@vie.local` has `isEvalUser: true` — since 1d.8 (00a68bc) the API keys the eval-user rule on it (D25: its runs become `evalRun` rows, never served to / pruning other users) | the eval user's results never replace real users' versions | dev: done |
| Prod eval user flagged `isEvalUser: true` AFTER it is created (1d.8): on the box `docker exec vie-mongodb sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin video-insight-engine --eval "db.users.updateOne({ email: \"<EVAL_USER_EMAIL>\" }, { \$set: { isEvalUser: true } })"'` → expect `matchedCount: 1`; then `db.users.countDocuments({ isEvalUser: true })` = 1. Safe before the deploy (old code ignores it). | scheduled eval must not touch real users' versions | at the end, before enabling the schedule |
| Prod `VIDEO_DAILY_LIMIT` (30 in the local `.env.production` copy; per user, rolling 24 h) ≥ 36 ONLY if the noise baseline (18 × 2) ever runs on prod; 18 per scheduled run fits | POST /api/videos limiter is global, not per tier | gate-0 decision (0.7) — none needed if the noise run stays on dev |

## Prod `.env` check before the PR #23 merge (2026-10-08 15:09 UTC, read-only)
- `LLM_CLASSIFIER_MODEL` and the 9 newly passed-through keys (`EXTRACTION_PARALLEL`, `EXTRACTION_PARALLEL_BATCHES`, `CHUNKED_EXTRACTION_THRESHOLD`, `MAX_TOKENS_PER_BATCH`, `MAX_MINUTES_PER_BATCH`, `EXTRACTION_FORCE_SPLIT_CHUNKS`, `FRAME_VISION_ENABLED`, `FRAME_VISION_PARALLEL`, `FRAME_TIER_ENABLED`): **absent** → new defaults apply (probe on Haiku 4.5, parallel extraction × 6 batches, parallel vision batches, tier on). ✓ no action.
- `TRANSCRIPT_CLEANING_ENABLED`: set to the new default's value → no change. ✓
- `LLM_VISION_MODEL=anthropic/claude-sonnet-4-6`: new default is blank → primary model, which is also Sonnet 4.6 → same behaviour. Optional: remove the line so vision follows future primary-model changes.
- `LLM_NUM_RETRIES`: still set, ignored after the merge. Optional cleanup (phase 4).
- Dropped keys (`EXTRACTION_USE_FAST_FIRST`, `FRAME_EXTRACTION_ENABLED`, `SCENE_HIRES_TIMEOUT`, old frame settings): absent. ✓
- Proxy: `YOUTUBE_PROXY_URL` + `YOUTUBE_PROXY_EXIT_COUNT` both set → the URGENT row above is DONE.
- Queue idle at 15:09 UTC (0 ready / 0 unacked / 1 consumer, DLQ 0, no pending/processing rows) — recheck right before the merge.
