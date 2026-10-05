# Video Insight Engine (VIE)

Turns any YouTube URL into an interactive app built from the video's own content — a cooking video becomes a timed recipe player; a coding tutorial becomes a code explorer with a quiz.

A video is a poor format for using what it teaches: you cannot search it, check items off, copy the code, or find one step without scrubbing. VIE extracts the content into components that fit it, such as checklists, step flows, code blocks and quizzes, and links them back to the moments in the video they came from.

[![CI](https://github.com/kfiravra/video-insight-engine/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kfiravra/video-insight-engine/actions/workflows/ci.yml)

<!-- LIVE-DEMO PLACEHOLDER — after demo mode is deployed, replace this comment with:
**Live demo:** https://vie.ad — one click, no sign-up.
-->

![Paste a URL, tabs stream in, ask the chat a question, jump to a moment](docs/images/demo.gif)

*A real run on a 5-minute video. The processing wait (about 3 minutes) is sped up 55×.*

[Full walkthrough video (64 s, processing sped up 6×)](docs/images/walkthrough.mp4)

## Screenshots

<p>
  <img src="docs/images/app-cooking.png" width="49%" alt="Generated app for a cooking video" />
  <img src="docs/images/app-coding.png" width="49%" alt="Generated app for a coding tutorial" />
</p>

*Same pipeline, two videos. The cooking video gets an ingredient checklist and a recipe step flow; the coding tutorial gets copyable code snippets and a quiz. Classification decides which components are generated.*

![RAG chat answering a question about the open video](docs/images/rag-chat.png)

*Chat over the video's indexed content, scoped to the open video or the whole library.*

## What you get

Each video gets its own set of tabs, chosen by the planner from 29 registered components. These are the tabs generated for the two videos above.

A cooking video (Chicken Piccata, 5 minutes):

| Tab | Component | What it does |
| --- | --- | --- |
| 10 Ingredients | `checklist` | Checkable ingredient list with quantities and prep notes |
| 8 Steps to Piccata | `step_flow_canvas` | The recipe laid out as a step-by-step flow |
| Key Techniques | `moment_track` | Gallery of key moments; Jump seeks the video to each one |
| Visual Moments | `video_filmstrip` | Frame scrubber across the whole video |

A coding tutorial (a 15-minute tooling walkthrough):

| Tab | Component | What it does |
| --- | --- | --- |
| 6-Step Wayfinder Workflow | `step_player` | The workflow as steps you play through and mark done |
| 5 Commands & Patterns | `code_playground` | Code snippets with copy |
| 6 Core Concepts | `concept_canvas` | Concepts and how they connect |
| Test Yourself | `quiz_arena` | Timed quiz on the content |

## How it works

```mermaid
flowchart LR
  URL([YouTube URL]) --> META[Metadata]
  META --> TR["Transcript<br/>captions, audio fallback"]
  META --> FR["Frames<br/>FFmpeg scene detect → OpenCV scoring → OCR + vision"]
  TR --> CP["Classify + plan<br/>domain, tabs, components"]
  FR -->|timestamped visual context| CP
  CP --> EX["Extraction<br/>chunked by chapter"]
  EX --> SY[Synthesis]
  EX --> AS["Assembly<br/>pure code, no LLM"]
  SY --> EN[Enrichment]
  AS --> EN
  AS -. "tab_ready × N" .-> UI([SSE → React tabs])
  EN -.-> UI
```

- **Classify + plan.** A fast classifier and one planning call decide the video's domain (14 domains, e.g. food, tech, travel) and design its tabs from 29 registered components. The plan determines what extraction looks for.
- **Chunked extraction.** Long videos are split by chapter (creator chapters, then detected chapters, then time splits) and batched into calls up to the context limit, so a multi-hour video takes a handful of calls.
- **Assembly.** Deterministic code turns extraction output into validated component props. The frontend renders each tab with one registry lookup.
- **Frames.** FFmpeg scene detection yields about 200 candidates. OpenCV scores them locally on six signals, and only the roughly 25 winners are re-extracted at 720p and sent to OCR and vision.
- **Streaming.** Each assembled tab is sent as an SSE event with its position, so tabs appear in place while the rest of the pipeline runs.

Stage-by-stage detail: [docs/summarizer-workflow.md](./docs/summarizer-workflow.md).

## Architecture

```mermaid
flowchart TB
  WEB["React 19 SPA<br/>Vite, TypeScript"] -->|REST + SSE| API["API gateway<br/>Fastify, TypeScript"]
  API -->|HTTP| SUM["Summarizer<br/>FastAPI, LiteLLM"]
  API -->|AMQP| MQ[(RabbitMQ)]
  MQ --> WK["Summarizer worker<br/>retry, DLQ"]
  API -->|HTTP| AST["Assistant<br/>FastAPI, RAG chat"]
  ADM["Admin<br/>FastAPI + React"] --> MONGO
  API --> MONGO[(MongoDB)]
  API --> REDIS[(Redis)]
  SUM --> MONGO
  SUM --> REDIS
  SUM --> S3[("S3<br/>frames, transcripts")]
  SUM --> QD[(Qdrant)]
  WK --> MONGO
  AST --> QD
  AST --> MONGO
  SUM --> LLM{{LLM providers}}
  WK --> LLM
  AST --> LLM
```

The gateway owns auth, rate limits and idempotency. The Python services own every LLM call. Full data flows and the service contract table: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).

## Engineering highlights

### Cost

About $0.29 for a ~20-minute video with captions (measured, v8 ledger); the Whisper fallback adds roughly $0.14 when captions are unavailable.

Models are chosen per stage through LiteLLM (`LLM_<STAGE>_MODEL`): a stronger model plans, fast models classify, synthesize and enrich. Work that does not need a model does not get one: frame scoring is local OpenCV, assembly is pure code, and prompt caching covers the plan and extraction prompts. Every call is written to an `llm_usage` ledger with its stage, model, tokens and cost. See [docs/llm-cost-model.md](./docs/llm-cost-model.md).

### Caching and idempotency

Summaries are stored under a content-addressed key that includes the pipeline version, so the same video submitted by another user is served from cache with no LLM calls. Bumping one shared version file invalidates the API's idempotency keys, the dedup key and the Redis response cache together. A Redis dispatch guard stops concurrent submissions from starting the pipeline twice. See [docs/IDEMPOTENCY.md](./docs/IDEMPOTENCY.md).

### RAG chat

The assistant retrieves from Qdrant over chunks built per component, scoped to one video or the whole library. It runs a tool-calling loop on LiteLLM: tools generate a new video app, organize the library into folders, and move videos. See [docs/RAG.md](./docs/RAG.md).

### Evaluation and observability

A retrieval gate in CI runs a golden query set against a real Qdrant and fails below recall@3 0.85 or MRR 0.7. A 20-video golden dataset runs through the live pipeline weekly and publishes to Langfuse; pull requests run the same harness as a zero-spend dry run. A sampled LLM judge scores faithfulness; it is informational, not a gate. Each pipeline run is one Langfuse trace, a request id follows a request across services, and Sentry is wired into the API, the web app and the Python services. See [docs/OBSERVABILITY.md](./docs/OBSERVABILITY.md).

### CI and deploy

The main CI workflow runs 11 parallel jobs on GitHub Actions: TypeScript typecheck and lint, design guards, ruff, seven test suites (with pyright on the Python services), and a Docker Compose image build that also validates the production config. Separate workflows on pull requests run a Playwright smoke suite against the Compose stack and the eval gates above.

Deploy is CI-gated: after a green CI run on `main`, a workflow deploys to a single EC2 host. It assumes an AWS role over OIDC, so no long-lived AWS keys are stored, and opens SSH ingress for the runner only for the duration of the deploy. See [docs/DEPLOY.md](./docs/DEPLOY.md).

## Run locally

Requires Docker and one LLM API key.

```bash
git clone https://github.com/kfiravra/video-insight-engine.git
cd video-insight-engine

cp .env.example .env        # set ANTHROPIC_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY)
docker compose up -d

curl http://localhost:3000/health    # API gateway
curl http://localhost:8000/health    # summarizer
open http://localhost:5173
```

`.env.example` documents every variable. Compose services, ports, backups and environment detail: [docs/INFRASTRUCTURE.md](./docs/INFRASTRUCTURE.md).

## Documentation

| Topic | Doc |
| --- | --- |
| System architecture and data flows | [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) |
| Pipeline walkthrough | [docs/summarizer-workflow.md](./docs/summarizer-workflow.md) |
| API contracts | [docs/API-REFERENCE.md](./docs/API-REFERENCE.md) |
| Data models | [docs/DATA-MODELS.md](./docs/DATA-MODELS.md) |
| Security | [docs/SECURITY.md](./docs/SECURITY.md) |
| Error handling, retry, DLQ | [docs/ERROR-HANDLING.md](./docs/ERROR-HANDLING.md) |
| Frontend patterns | [docs/FRONTEND.md](./docs/FRONTEND.md) |
| GDPR deletion | [docs/GDPR.md](./docs/GDPR.md) |

## Built with Claude Code

The repo carries its Claude Code harness in [`.claude/`](./.claude): rule-based skill activation (keyword, intent and path triggers load the matching skill before an edit), six task-specific subagents, MCP integrations, and hooks that guard destructive git commands and warn on source edits without a test change.

## License

[MIT](./LICENSE)
