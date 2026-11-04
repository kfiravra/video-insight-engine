# Video Insight Engine

YouTube URL → interactive knowledge app. pnpm monorepo: `api/` (Fastify, :3000), `apps/web/` (React 19 + Vite, :5173),
`services/summarizer|assistant|admin` (FastAPI, :8000/:8001/:8002), `packages/types|shared|llm-common`,
Mongo + Redis + Qdrant + RabbitMQ via docker compose. Full context dump: [PROJECT-BRIEFING-TLDR.md](../PROJECT-BRIEFING-TLDR.md).

## Verify (`/check` picks the right subset for the packages you touched)

- Shared packages first: `pnpm --filter @vie/types --filter @vie/shared build`
- api: `pnpm --filter @vie/api typecheck` · `pnpm --filter @vie/api lint` · `pnpm --filter @vie/api test`
- web: `pnpm --filter web exec tsc -b` (never `tsc --noEmit`, it checks nothing) · `pnpm --filter web lint` · `pnpm --filter web exec vitest run`
- web smoke e2e: `pnpm --filter web test:e2e:smoke` (needs `docker compose up -d --build --wait vie-api` and `CORS_ADDITIONAL_ORIGINS=http://localhost:5273` in `.env`)
- python: `cd services/<summarizer|assistant|admin> && .venv/bin/python -m pytest`
- ruff: `services/summarizer/.venv/bin/ruff check --config ruff.toml services/summarizer services/assistant services/admin packages/llm-common` (ruff is not on PATH)
- pyright: `npx pyright@1.1.407 --project .` inside `services/summarizer` or `services/assistant`
- admin UI: `cd services/admin/ui && npx vitest run`
- infra hooks: `node --test .claude/hooks/tests/*.test.mjs`
- Never run the api, web and python suites in parallel.

## Gotchas (what the code does not tell you)

- `vie-api` runs the image's `dist`: api edits need `pnpm docker:build:api` plus a recreate before they are live.
- `vie-summarizer` hot-reloads from `src/`: no code edits during a live pipeline run. The worker does not reload: `docker compose restart vie-summarizer-worker`.
- `vie-web` serves a stale build; use `pnpm dev:web` for hot reload.
- Single-file bind mounts (`pipeline-version.json`, `domains.json`) need `--force-recreate` to pick up edits.
- `packages/shared/src/config/pipeline-version.json` is ONE value read by api and summarizer; a bump creates new dedupKey rows for every video ([docs/IDEMPOTENCY.md](../docs/IDEMPOTENCY.md)). `bypassCache: true` in the `POST /api/videos` body is the surgical refresh.
- Mongo is a single-node replica set (`rs0`); host clients need `?directConnection=true`.
- CI runs Python 3.11, local venvs are 3.12; async tick-count tests can pass locally and fail in CI.
- `gh` works only as `env -u GITHUB_TOKEN gh ...` (the exported token is invalid).
- The SSE stream drives the pipeline: POST only registers, headless clients must consume the stream. New `phase` values go in both `VALID_SSE_PHASES` and `SSE_PHASE_MAP` or the UI freezes.
- `ruff check --fix` deletes `import x as x` re-exports and not-yet-used imports; the pre-commit hook runs it.
- `YTDLP_PLAYER_CLIENTS=android` only (never `android,default`). opencv is pinned `<5` by hand in the lock; never regenerate the lock bare.
- `QdrantClient` calls are sync: mock with `MagicMock`; use `query_points()` (`search()` is gone).
- Redis response keys are `vie:response:v<N>:<ytid>`; frame-quality changes need a `SCENE_S3_PREFIX` bump.
- `YOUTUBE_PROXY_URL` is the only proxy setting. Check the admin for live runs before restarting containers (the hook asks).
- `vie-alloy` (Grafana Cloud logs + metrics) is behind the `monitoring` profile; prod sets `COMPOSE_PROFILES=backup,monitoring`. On Docker Desktop it has no per-container metrics.

## Git

- Work on feature branches; local commits at green phase boundaries need no approval.
- push, PR create/merge and rebase happen only on Kfir's word: an explicit verb in the current turn ("push", "ship", "rebase").
  "Continue", "go ahead" and "finish it" are not that word; the hook and the ask rules enforce it.
- stash, reset --hard, checkout --, restore and clean are blocked by the hook; `git stash` is banned outright (2026-05-28 near-loss). `/guard off` is the user's switch, not yours.
- `dev/active/` is untracked and is never staged: no `git add dev/active`, no `-A` or `.` while a task folder is dirty.
  Task folders are local working memory, not history; `.gitignore` covers them but the rule holds even where it does not.
- No docs-only commits: documentation rides with the code commit it describes.
  If only docs changed, leave them uncommitted for the next code commit, or ask.
- Conventional commits (`type(scope): description`), one logical change per commit, one green phase = one commit. Attribution trailers are disabled by setting; never add them by hand.

## Working conventions

- Task state lives in `dev/active/<task>/plan.md` + `notes.md`; it is local and never committed (`/task-plan`, `/resume`, `/handoff`).
- Phase end: `/phase-review` by area, then one review-fix commit.
- Spend is stated before any run that costs money or minutes: pipeline runs (video, model, cached or not), agent fan-outs (count, model), e2e against live services.
  One line, then wait for the word; never start it in the same breath.
- Models: Fable for planning sessions, Opus for execution, Sonnet only for named mechanical lanes (a lane the plan lists by name).
  Subagents default to opus (`CLAUDE_CODE_SUBAGENT_MODEL`) and never inherit the session model; at most 6 at once.
- Reviewers read [.claude/standards/CODING-STANDARDS.md](standards/CODING-STANDARDS.md); per-language conventions live in `.claude/rules/` and load only when a matching file is touched.

## Where to read

| Topic | Doc |
|---|---|
| Auth, rate limits, CORS | [docs/SECURITY.md](../docs/SECURITY.md) |
| Mongo schemas, VIEResponse | [docs/DATA-MODELS.md](../docs/DATA-MODELS.md) |
| API contracts | [docs/API-REFERENCE.md](../docs/API-REFERENCE.md) |
| Pipeline call order, who calls whom | [docs/summarizer-workflow.md](../docs/summarizer-workflow.md) |
| Idempotency, dedup, PIPELINE_VERSION bumps | [docs/IDEMPOTENCY.md](../docs/IDEMPOTENCY.md) |
| Compose, env vars, backups, prod deploy | [docs/INFRASTRUCTURE.md](../docs/INFRASTRUCTURE.md), [docs/DEPLOY.md](../docs/DEPLOY.md) |
| Langfuse, prompt registry, Sentry | [docs/OBSERVABILITY.md](../docs/OBSERVABILITY.md) |
| RAG chunker, embeddings, library search | [docs/RAG.md](../docs/RAG.md) |
| Frontend patterns and design language | [docs/FRONTEND.md](../docs/FRONTEND.md), [.impeccable/DESIGN.md](../.impeccable/DESIGN.md) |
| GDPR cascade deletion | [docs/GDPR.md](../docs/GDPR.md) |
| Error codes, retry, DLQ | [docs/ERROR-HANDLING.md](../docs/ERROR-HANDLING.md) |
| Multi-service contracts, who owns which field | [docs/CROSS-CUTTING.md](../docs/CROSS-CUTTING.md) |
| LLM cost model, cache crediting | [docs/llm-cost-model.md](../docs/llm-cost-model.md) |

## Skills

`/task-plan [name]` plan a task · `/resume [task]` pick it up · `/handoff` save state before clearing · `/check [all]` verify touched packages · `/phase-review` by-area review · `/ship` pre-PR gate · `/guard off|on` the user's switch for the git guard · `/impeccable [command]` design passes on UI work (shape, critique, audit, polish) · `/diagnose` and `/tdd` load on their own when relevant. Maintenance: `/doctor prompt-audit`, `/skill-doctor`, `/checkup`, `node --test .claude/hooks/tests/*.test.mjs`.
