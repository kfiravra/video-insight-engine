# RAG Architecture

How the assistant retrieves grounded answers from transcripts and assembled tab output.

**Last verified:** 2026-05-14 — see [reports/chunker-coverage.md](../reports/chunker-coverage.md) for the per-component coverage matrix.

## Overview

Three content sources are indexed in **one** Qdrant collection (`transcript_chunks`, 384-dim cosine) and filtered apart by the `source` payload field:

| `source` value     | What                                                          | Producer                                      |
|--------------------|---------------------------------------------------------------|-----------------------------------------------|
| `transcript`       | Sentence-windowed transcript chunks (raw + optional original-language) | `chunk_transcript()` → `store_transcript_chunks` |
| `default_output`   | Natural-language strings extracted per tab component (the overview included — its fields live in `props.data`) | `chunk_assembled_tabs()` → `store_default_output_chunks` |
| `visual`           | What the frames showed: the rendered `<visual_annotations>` entries (`[m:ss] caption \| on-screen text`), packed whole into ≤1,000-char chunks labelled `On screen:` with the first/last frame's `timestamp`/`end_timestamp`; stored `language="en"` | `chunk_visual_entries()` → `store_visual_chunks` |

Transcript points are speech only: since pipeline-1min 1c.2 frame annotations never enter `clean_text`, so `visual` points are the only place visual facts are searchable. `/library/search` accepts `sources: ["transcript" | "default_output" | "visual"]` (any other value is a 422); every hit carries its `source`.

Single-video chat (assistant `/chat`) and cross-library search (assistant `/library/search`) both go through the same `RAGService` + `QdrantRepository` path; the only difference is the size of the `video_ids` filter.

## Pipeline

```
Pipeline complete                               (summarizer)
        │
        ├─ SSE emit "complete"          ◄── consumer unblocks here
        │
        ├─ asyncio.create_task(store_transcript_chunks)
        ├─ asyncio.create_task(store_default_output_chunks)  ◄── fire-and-forget
        ├─ asyncio.create_task(store_visual_chunks)
        │
        ├─ SSE emit "done"
        ▼
                                                (background tasks finish later)
        store_*  ─► chunker  ─► embed  ─► VectorService.delete_by_video_and_source
                                                       │
                                                       ▼
                                          VectorService.store_chunks (upsert)
```

Background tasks **never block SSE** — see `_index_in_qdrant` in `services/summarizer/src/services/pipeline/phases/assembly.py`. An eval-user run (`evalRun`, D25) writes no Qdrant points at all (nor the Redis response copy): the collection keeps the users' own result.

## Output chunker rules

`services/summarizer/src/services/vector/output_chunker.py` walks `assembled_tabs[]` and emits one `OutputChunk` per natural retrieval unit (one summary, one quiz question, one comparison row).

| Rule                                            | Why                                                            |
|-------------------------------------------------|----------------------------------------------------------------|
| Per-component handler table                     | Silent fallback would index button labels and IDs as embeddings, polluting retrieval. Unknown component → 0 chunks. |
| `<6-word` drop                                  | Below this, embeddings encode incidentals more than meaning.   |
| No raw code / lyrics / URLs / numbers           | Handler picks prose fields by name; structured data is skipped.|
| `display_section` whitelist                     | Only `text, description, summary, explanation, content, analysis, caption, label, instruction, tip, note` keys are flattened. |
| Pre-delete by `(video_id, source)` before upsert | Reprocesses with fewer chunks no longer leave orphan points. `store_visual_chunks` embeds first and deletes after, so a failed embed keeps the previous run's points; an empty block still deletes. |

**Authoritative coverage table:** [reports/chunker-coverage.md](../reports/chunker-coverage.md). All 17 components in `ASSEMBLER_REGISTRY` are covered. Legacy `timeline` and `clip_player` handlers stay until `db.videoSummary.distinct("tabs.component")` no longer returns them.

## Qdrant payload schema

Each point carries:

| Field             | Type     | Set on   | Purpose                                              |
|-------------------|----------|----------|------------------------------------------------------|
| `text`            | string   | both     | English-language chunk text (the embedded source)    |
| `text_original`   | string?  | non-EN   | Source-language text aligned by proportional offset  |
| `video_id`        | string   | both     | YouTube ID — filter scope for retrieval              |
| `chunk_index`     | int      | both     | Ordering within the source                           |
| `timestamp` / `end_timestamp` | float? | transcript, visual | Seconds; transcript chunks get them when segments are passed, visual chunks always. `null` on output points and on points written before payload schema v2 (heal on re-ingest). |
| `source`          | string   | all      | `transcript`, `default_output` or `visual`           |
| `tab_id`          | string?  | output   | Originating tab (`overview_tab`, `key_moments`, ...) |
| `tab_component`   | string?  | output   | Originating component (`overview`, `quiz`, ...)      |
| `prop_path`       | string?  | output   | Dot/bracket path to source field (`spots[3]`, `keyTakeaways[1]`) |

Output chunks let the assistant deep-link a retrieval hit back to the exact tab + prop the user is looking at.

## Embedding model decision

**Current:** `all-MiniLM-L6-v2` (sentence-transformers, 384 dims, MIT license).

**Why it stays for now:**
- 384-dim matches the existing Qdrant collection (`VECTOR_SIZE = 384` in `qdrant_service.py:30`). A swap requires recreating collections.
- `all-MiniLM-L6-v2` is **English-only**. Non-English content is translated to English via Whisper **before** embedding (see `services/summarizer/src/services/pipeline/phases/assembly.py:137` and `services/summarizer/src/services/pipeline/phases/translation.py`). Cross-video failures previously observed in production were due to index corruption from missing pre-deletes — fixed by `VectorService.delete_by_video_and_source` in `vector/store.py`. Do **not** assume the embedding model has cross-lingual ability when evaluating swaps.
- Output chunks are short, prose-only retrieval units (handler-enforced) which is exactly the regime MiniLM handles well.

**Swap candidate:** `BAAI/bge-small-en-v1.5` — same 384 dims, drop-in replacement, generally +5–8% retrieval quality on English benchmarks. Larger candidates (e.g., `bge-base-en-v1.5` at 768 dims) require recreating collections and are out of scope for this task.

**How to validate before swapping:**

```bash
cd services/summarizer
python -m scripts.benchmark_embeddings
```

The script (`services/summarizer/scripts/benchmark_embeddings.py`) prints per-query top-10 breakdown across 5 ingested fixtures and 4 queries. Pass criteria (advisory):
- `"React coding"` → ≥ 7/10 from the React fixture
- `"reactivity system"` → ≤ 3/10 from React; Vue/Svelte should dominate
- `"async data fetching"` → sensible spread across all fixtures

Edit `DEFAULT_FIXTURES` in the script to point at YouTube IDs that are actually ingested in your local `vie-qdrant`. **If you swap `settings.EMBEDDING_MODEL_NAME` you MUST re-ingest the fixtures with the new model so the query encoder and stored vectors agree.**

## Retrieval eval (gating)

Beyond the advisory benchmark above, retrieval quality is **measured and gated** (project-score-9 3.2) by `services/summarizer/tests/eval/test_retrieval_eval.py` over the golden fixture `services/summarizer/tests/fixtures/retrieval_golden.json` (4 synthetic videos, 21 query→answer-span pairs). It exercises the real path — `chunk_transcript` → `embed_texts` → Qdrant upsert → `query_points` — against a uniquely-named **ephemeral collection** (created and deleted per run; never touches `transcript_chunks`), with zero LLM spend.

- **Committed baseline** (`dev/golden-dataset/baseline.json`, measured 2026-07-12): recall@3 = **1.000**, MRR = **0.865** (n=21).
- **Hard floors** (in the fixture, asserted by the test): recall@3 ≥ 0.85, MRR ≥ 0.70. Verified to trip: a deliberate chunker regression (120-char chunks, no overlap) scored recall@3 0.524 / MRR 0.500 and failed the job.
- **CI**: `.github/workflows/eval.yml` → `retrieval-eval` job runs it on PRs touching prompts/pipeline/vector code against a `qdrant/qdrant:v1.18.2` service container, and fails if the suite silently skips.
- **Local**: `cd services/summarizer && .venv/bin/python -m pytest -q tests/eval/test_retrieval_eval.py -s` (skips gracefully without a reachable Qdrant).

This also gives the bge-small swap decision its measuring stick: run the eval before/after the model swap (with re-ingest) and compare against the committed baseline.

## Multi-video search

`QdrantRepository.search` at `services/assistant/src/repositories/qdrant_repository.py:32` accepts `video_ids: list[str]` and:

- `len(video_ids) == 1` → `MatchValue` (cheaper, hashed lookup)
- `len(video_ids) > 1`  → `MatchAny` (set-membership)
- `len(video_ids) == 0` → short-circuits, returns `[]` (never hits Qdrant)

Tests in `services/assistant/tests/test_qdrant_repository.py` lock in all three branches.

## Endpoints

| Endpoint                       | Service     | Purpose                            | Auth                       |
|--------------------------------|-------------|------------------------------------|----------------------------|
| `POST /chat`                   | assistant   | Single-video SSE chat with grounding | `X-Internal-Secret` header |
| `POST /library/search`         | assistant   | Cross-library retrieval, no LLM    | `X-Internal-Secret` header; 60 req/min per `X-User-Id` |

The library endpoint trusts the upstream Node gateway to enforce `video_ids` ownership before forwarding.

## Verification commands

```bash
# Per-component chunker tests
cd services/summarizer && python3 -m pytest tests/test_output_chunker.py -v

# Pre-delete + scheduling guarantees
cd services/summarizer && python3 -m pytest tests/test_vector_store.py tests/test_qdrant_service.py -v

# Multi-video search + payload + /library/search endpoint
cd services/assistant && python3 -m pytest tests/test_qdrant_repository.py tests/test_rag.py tests/test_server.py -v

# Manual smoke (requires running assistant + Qdrant)
curl -X POST http://localhost:8001/library/search \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: $INTERNAL_SECRET" \
  -H "X-User-Id: smoke-user" \
  -d '{"query":"git rebase","video_ids":["a","b"],"top_k":10}'
```

## Adding a new component

1. Add the assembler to `ASSEMBLER_REGISTRY` in `assemblers.py`.
2. Add a `_h_{component}` handler in `output_chunker.py` that returns prose-only chunks.
3. Register it in `_COMPONENT_HANDLERS`.
4. Add a `Test{Component}` class to `test_output_chunker.py` asserting:
   - Prose is emitted at the expected `prop_path`.
   - Structured/numeric fields are **not** in the chunk text.
   - Short / empty items are dropped.
5. Update [reports/chunker-coverage.md](../reports/chunker-coverage.md).
