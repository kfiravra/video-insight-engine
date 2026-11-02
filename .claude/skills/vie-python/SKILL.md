---
name: vie-python
description: Repo facts for the Python services (summarizer pipeline and worker, assistant RAG chat, admin) and packages/llm-common. Loads on its own when Python files under services/ or packages/llm-common/ are touched. Use when changing pipeline stages, prompts, LLM calls, the worker, Qdrant retrieval, or admin reports.
paths:
  - "services/**/*.py"
  - "packages/llm-common/**"
---

# vie-python

FastAPI + Pydantic 2 + LiteLLM (JSON mode, prompt caching) on Python 3.11 in CI and 3.12 locally.
`services/summarizer` runs the pipeline and the RabbitMQ worker (sync pymongo, aio-pika);
`services/assistant` is the RAG chat (Motor, Qdrant via `query_points()`, a hand-rolled agent loop
in `src/services/agent_loop.py`, `tool_router.py`, `agent_tools.py`, bounded by
`MAX_TOOL_ITERS`/`MAX_TOOL_CALLS_*`); `services/admin` serves the admin API and a built React UI;
`packages/llm-common` holds the shared LiteLLM wrappers and cost accounting. No ODM, no agent
framework. Conventions: [.claude/rules/python.md](../../rules/python.md).

## Verify

```
cd services/<summarizer|assistant|admin> && .venv/bin/python -m pytest
services/summarizer/.venv/bin/ruff check --config ruff.toml services/summarizer services/assistant services/admin packages/llm-common
cd services/summarizer && npx pyright@1.1.407 --project .      # also for assistant
```

Redis on `localhost:6379` is needed for the replay-broker and leaked-stream Lua tests (they skip
without it); `tests/eval/test_retrieval_eval.py` needs Qdrant on 6333. `test_stream_routes` error
tests return `cached` instead of `error` on this box only (known, [dev/gotchas.md](../../../dev/gotchas.md)).
`scripts/register_prompts.py` (Langfuse prompt sync) must run from the summarizer venv.

## Pipeline order (summarizer)

Metadata + description analysis → transcript ∥ frames → visual injection → classifier → plan
(Sonnet) → extraction (chunked above 30 min, parallel batches) → synthesis ∥ assembly → enrichment
→ translation. Stage outputs are stored under `pipeline.*` in Mongo and traced in Langfuse. Per-stage
models come from `LLM_<STAGE>_MODEL`. The full call order is in [docs/summarizer-workflow.md](../../../docs/summarizer-workflow.md).

## Facts that bite

- `vie-summarizer` hot-reloads `src/` (uvicorn `--reload`), so editing code during a live run
  restarts the run. The worker shares the image but does not reload:
  `docker compose restart vie-summarizer-worker` (asks first; check the admin for live runs).
- The SSE stream drives the pipeline: a job only runs while a client consumes the stream.
- `PIPELINE_VERSION` comes from `packages/shared/src/config/pipeline-version.json` (bind-mounted,
  force-recreate after edits). Bumping it invalidates every cached response.
- `[VISUAL]` frame descriptions are injected as a block and never interleaved with transcript text.
  Frame extraction is two-pass; quality changes need a `SCENE_S3_PREFIX` bump; `SCENE_HIRES_ENABLED=false`
  is the rollback. Vision runs on the fast tier (`LLM_VISION_MODEL`).
- Transcripts: `YTDLP_PLAYER_CLIENTS=android` only; `YOUTUBE_PROXY_URL` is the only proxy setting
  (Webshare, exit rotation on caption 429 with a negative cache); `<lang>-orig` caption tracks beat
  plain `<lang>`, manual tracks win. TED talks arrive labelled `ar`.
- The worker mirrors the api's queue topology in `src/worker/topology.py`; any drift in queue
  arguments stops the consumer. Every message ends acked or rejected: validation error → DLQ,
  retriable error with attempts left → new retry message then ack, exhausted → DLQ.
- Qdrant client calls are sync: `MagicMock` in tests; `search()` no longer exists.
- Prompts and playbooks are config-single-sourced (`domains.json`, `effective_requirements()`);
  edit the config, not the prompt copies. Translation status is owned by the translation stage.
- Cost: cache credits are tracked per call ([docs/llm-cost-model.md](../../../docs/llm-cost-model.md)); tag every new LLM call so it
  shows in the ledger; `cache_savings_usd` has a known zero bug.
- Ruff: the root `ruff.toml` carries per-file ignores for summarizer and assistant; `ruff check --fix`
  removes `import x as x` re-exports. opencv is pinned `<5` by hand in the lock.
- Pyright baselines 19 summarizer files in `pyrightconfig.json`; do not add to the list, fix the file.

## Read before changing

| Area | Doc |
|---|---|
| Pipeline internals, who calls whom | [docs/summarizer-workflow.md](../../../docs/summarizer-workflow.md), [docs/SERVICE-SUMMARIZER.md](../../../docs/SERVICE-SUMMARIZER.md) |
| RAG chunker, embeddings, library search | [docs/RAG.md](../../../docs/RAG.md), [docs/SERVICE-ASSISTANT.md](../../../docs/SERVICE-ASSISTANT.md) |
| Tracing, prompt registry, faithfulness | [docs/OBSERVABILITY.md](../../../docs/OBSERVABILITY.md) |
| Cost and cache crediting | [docs/llm-cost-model.md](../../../docs/llm-cost-model.md), [docs/llm-cost-output-quality.md](../../../docs/llm-cost-output-quality.md) |
| Admin reports and alerts | [docs/SERVICE-ADMIN.md](../../../docs/SERVICE-ADMIN.md) |
| Error codes, retry, DLQ | [docs/ERROR-HANDLING.md](../../../docs/ERROR-HANDLING.md) |
