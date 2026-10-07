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
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | dataset-run upload | exist |

After the secrets land: re-enable the `schedule:` trigger in `.github/workflows/eval.yml` (kept
manual-dispatch only until then).

## Prod data / accounts

| Change | Why | Status |
|---|---|---|
| _(none yet)_ | | |
