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
| _(none yet)_ | | | | |

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
| Dev `eval@vie.local` has `isEvalUser: true` (set by hand in `mongosh` 2026-10-07 for the work parked on `wip/eval-faithfulness`) — nothing on `feat/pipeline-1min` reads it | none today; 1d.8 may key its eval-user rule on it | prod: decided in 1d.8 |
| Prod `VIDEO_DAILY_LIMIT` (30 in the local `.env.production` copy; per user, rolling 24 h) ≥ 36 ONLY if the noise baseline (18 × 2) ever runs on prod; 18 per scheduled run fits | POST /api/videos limiter is global, not per tier | gate-0 decision (0.7) — none needed if the noise run stays on dev |
