# Video Insight Engine

Personal video knowledge management system — YouTube URL to interactive knowledge app.

> **LLM onboarding:** [PROJECT-BRIEFING.md](./PROJECT-BRIEFING.md) (full) and [PROJECT-BRIEFING-TLDR.md](./PROJECT-BRIEFING-TLDR.md) (quick) are paste-into-any-chat context dumps covering the concept, architecture, workflows, schema, and conventions.

## ⚠️ Working-tree safety — READ FIRST

**NEVER run any working-tree-mutating git command without explicit, current-turn user permission.** This includes `git commit`, `git push`, `git stash`, `git stash pop`, `git reset --hard`, `git checkout -- <file>` (to discard), `git restore`, `git clean`, `git rebase`, force-push, or branch deletion. Read-only ops (`git status`, `git diff`, `git log`, `git show`) are fine.

- **`git stash` is BANNED for "let me quickly test against the clean tree" use cases.** Looks reversible — isn't. Hooks that auto-write files (session snapshots, log files) can fire between stash and pop, causing the pop to fail and stranding ALL modified files inside the stash. Happened 2026-05-28; nearly lost a day's work. If you need to compare against the clean tree, read files via `git show HEAD:<path>`, spawn an Agent in `isolation: worktree` mode, or just ask.
- "Continue", "go ahead", "finish it", "do what's needed" are NOT commit/stash authorization. Only an explicit verb in the current turn ("commit", "stash", "push", "ship") counts.

## Tech Stack

| Service | Tech | Port |
|---------|------|------|
| vie-api | Node.js + Fastify + TypeScript | 3000 |
| vie-web | React 19 + Vite + TypeScript | 5173 |
| vie-summarizer | Python + FastAPI + LiteLLM | 8000 |
| vie-assistant | Python + FastAPI + LiteLLM + Qdrant | 8001 |
| vie-admin | Python + FastAPI + React + Recharts | 8002 |
| vie-mongodb | MongoDB 7 | 27017 |
| vie-redis | Redis 7 | 6379 |
| vie-qdrant | Qdrant | 6333/6334 |
| vie-rabbitmq | RabbitMQ 3.13 (mgmt) | 5672/15672 |
| vie-summarizer-worker | (shares vie-summarizer image) | — |

## Task Workflow

1. Check `dev/active/` for current tasks — read `plan.md`, `context.md`, `tasks.md`
2. When skill activation hook fires: READ the SKILL.md and ALL suggested resource files BEFORE writing code
3. Load skill by service: api → `backend-node`, web → `react-vite`, summarizer/assistant → `backend-python`

## Commands

| Command | Purpose |
|---------|---------|
| `/task-plan {name}` | Create task documentation |
| `/resume {task}` | Resume after chat clear |
| `/complete-task {task}` | Plan → test → security → review |
| `/review` | Code review changes |
| `/test {file}` | Generate tests |
| `/ship` | Pre-deploy checklist |
| `/security-check` | Security audit |
| `/list-tasks` | List active tasks in `dev/active/` |
| `/task-plan-update` | Update task docs before context compaction |
| `/audit-infra` | Audit `.claude/` infra for drift |

## Project Structure

```
video-insight-engine/
├── .claude/        # Skills, agents, hooks, commands, rules
├── docs/           # Architecture, security, error handling, services
├── dev/            # Task planning (survives context resets)
├── packages/       # @vie/shared, @vie/types, llm-common (Python)
├── api/            # vie-api gateway (Node.js + Fastify)
├── services/       # summarizer, assistant (Python)
├── apps/           # web frontend (React)
└── scripts/        # Utility scripts
```

## Critical Docs

| When | Read |
|------|------|
| Auth, rate limiting, CORS | [docs/SECURITY.md](./docs/SECURITY.md) |
| Error codes, retry, DLQ | [docs/ERROR-HANDLING.md](./docs/ERROR-HANDLING.md) |
| MongoDB schemas, VIEResponse | [docs/DATA-MODELS.md](./docs/DATA-MODELS.md) |
| Multi-service contracts | [docs/CROSS-CUTTING.md](./docs/CROSS-CUTTING.md) |
| System architecture | [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) |
| API contracts | [docs/API-REFERENCE.md](./docs/API-REFERENCE.md) |
| Service docs | [docs/SERVICE-API.md](./docs/SERVICE-API.md), [SERVICE-SUMMARIZER.md](./docs/SERVICE-SUMMARIZER.md), [SERVICE-ASSISTANT.md](./docs/SERVICE-ASSISTANT.md), [SERVICE-ADMIN.md](./docs/SERVICE-ADMIN.md) |
| Pipeline call-order walkthrough | [docs/summarizer-workflow.md](./docs/summarizer-workflow.md) |
| Compose, env vars, backup/restore, prod deploy | [docs/INFRASTRUCTURE.md](./docs/INFRASTRUCTURE.md) |
| Single-host AWS deploy runbook (EC2, Caddy, backups, CI deploy role) | [docs/DEPLOY.md](./docs/DEPLOY.md) |
| July 2026 audit scorecard (historical) | [docs/AUDIT-2026-07.md](./docs/AUDIT-2026-07.md) |
| Frontend patterns | [docs/FRONTEND.md](./docs/FRONTEND.md) |
| LLM cost & cache crediting | [docs/llm-cost-model.md](./docs/llm-cost-model.md) |
| LLM tracing, prompt registry, faithfulness, request-id, Sentry | [docs/OBSERVABILITY.md](./docs/OBSERVABILITY.md) |
| RAG chunker, embeddings, `/library/search` | [docs/RAG.md](./docs/RAG.md) |
| Request idempotency, cross-user dedup, dispatch guard, `PIPELINE_VERSION` bumps | [docs/IDEMPOTENCY.md](./docs/IDEMPOTENCY.md) |
| GDPR Art. 17 cascade deletion, soft-delete window, audit | [docs/GDPR.md](./docs/GDPR.md), [docs/PRIVACY.md](./docs/PRIVACY.md) |

## Quick Start

```bash
cp .env.example .env  # Add ANTHROPIC_API_KEY
docker-compose up -d
curl http://localhost:3000/health && curl http://localhost:8000/health
open http://localhost:5173
```

This is a monorepo with 5 services (api, web, summarizer, assistant, admin), shared packages (@vie/types, @vie/shared, llm-common), Redis+MongoDB caching (same video = instant serve at $0.00), and Qdrant for RAG-powered video chat. When the skill activation hook fires, always read SKILL.md and all suggested resources before writing any code — this is mandatory with no exceptions. Follow conventional commits, never commit to main directly, and validate all user input at system boundaries.
