# Cross-Cutting Concerns

Guidelines for work that spans multiple services.

---

## When Does This Apply?

Use this guide when your task involves:

- Frontend + Backend changes (new API endpoint + UI)
- vie-api + vie-summarizer (HTTP calls, status updates)
- vie-api + vie-assistant (HTTP + SSE)
- Any change affecting multiple services

---

## The Golden Rule

**Define the contract first, build in dependency order.**

```
1. CONTRACT   →   2. BACKEND   →   3. FRONTEND
   (API spec)      (Provider)       (Consumer)
```

Why this order?
- Backend defines what's possible
- Frontend consumes what exists
- Contract prevents miscommunication

---

## Cross-Service Development Flow

### Step 1: Define the Contract

Before writing any code, document the interface:

```markdown
## New Feature: Retry Failed Video

### API Endpoint
POST /api/videos/:id/retry

### Request
None (empty body)

### Response (200)
{
  "video": {
    "id": "...",
    "status": "pending",
    "retryCount": 1
  }
}

### Errors
- 404: VIDEO_NOT_FOUND
- 400: INVALID_STATE (not in failed state)
- 400: NOT_RETRYABLE (permanent error like NO_TRANSCRIPT)

### WebSocket Event
{
  "type": "video.status",
  "payload": { "videoSummaryId": "...", "status": "pending" }
}
```

**Where to document:**
- All API contracts → `docs/API-REFERENCE.md` (REST, WebSocket, SSE)

### Step 2: Build Backend First

```
vie-api                    vie-summarizer/assistant
  │                              │
  ├── Add route                  │
  ├── Add service method         │
  ├── Add validation             ├── Add handler (if needed)
  ├── Add tests                  ├── Add tests
  └── Verify with curl           └── Verify integration
```

**Verification before frontend:**
```bash
# Test the endpoint directly
curl -X POST http://localhost:3000/api/videos/123/retry \
  -H "Authorization: Bearer $TOKEN"
```

### Step 3: Build Frontend Last

Only after backend is working:

```
vie-web
  │
  ├── Add API client method
  ├── Add React Query hook
  ├── Add UI component
  ├── Handle loading/error states
  └── Test full flow
```

---

## Service Communication Patterns

### vie-api ↔ vie-summarizer (Async via HTTP)

```
┌─────────┐    POST /summarize    ┌──────────────┐
│ vie-api │──────────────────────▶│vie-summarizer│
└─────────┘                       └──────┬───────┘
     │                                   │
     │ Returns to user                   │
     │ immediately                       │ Background
     ▼                                   │ task runs
  User sees                              │
  "processing"                           │
                                         │ Updates DB
     Frontend polls ◀────────────────────┘
```

**Pattern:**
1. vie-api sends POST request to vie-summarizer with job details
2. vie-summarizer immediately returns 202 Accepted
3. vie-summarizer processes in background (FastAPI BackgroundTasks)
4. vie-summarizer updates MongoDB status directly
5. Frontend polls for status or receives WebSocket update

**Key files:**
- `api/src/services/summarizer-client.ts`
- `services/summarizer/src/main.py`
- `docs/API-REFERENCE.md`

### vie-api ↔ vie-assistant (HTTP + SSE)

```
┌─────────┐   HTTP POST    ┌──────────────┐
│ vie-api │───────────────▶│ vie-assistant │
└─────────┘◀───────────────└──────────────┘
            SSE stream
```

**Pattern:**
1. vie-api proxies chat requests to vie-assistant via HTTP
2. vie-assistant streams responses back via SSE
3. Chat history is passed per-request (no server-side session)
4. Results may be cached in MongoDB

**Key files:**
- `api/src/services/assistant-client.ts`
- `services/assistant/src/server.py`
- `docs/API-REFERENCE.md`

### Token-ledger ids across services

Who mints each id, who carries it, and who settles on it:

| Id | Minted by | Carried as | Settled by |
|---|---|---|---|
| `billingRunId` (a video run) | vie-api `VideoSubmissionService` (UUID per submit; a playlist item's run id) | the cache row's dispatch stamp, read by the summarizer at run start | the summarizer's `POST /internal/run-cost {videoSummaryId, billingRunId, costUsd}` after `pipeline.timing` persists; a `failed` / cache-hit status releases it; the repair job settles a hold whose callback never came |
| `requestId` | the Fastify request id (or a playlist item's run id) | `X-Request-ID`, `llm_usage.request_id` | the repair job's late settle (Σ `llm_usage` by it) and its assistant backfill |
| `executionId` (a chat turn, library chat, action) | vie-api, per request (UUID) | `X-Execution-Id` to vie-assistant | the assistant's `POST /internal/assistant/usage {executionId, requestId, kind, costUsd}` when the request ends (`debit:exec:<executionId>:…`, once) |

vie-api owns every token amount (`ceil(cost × 3 × 1000)`); the Python services only report dollars of real LLM cost.

---

### Vector store: shared collection, source-filtered access

Both `vie-summarizer` (writer) and `vie-assistant` (reader) speak to the
same `transcript_chunks` Qdrant collection. The `source` payload field
splits content within that collection, so callers can scope retrieval
without splitting infrastructure.

```
                  ┌─────────────────────────────────────┐
                  │ Qdrant: transcript_chunks (384-dim) │
                  └─────────────────────────────────────┘
                     ▲                              │
   writer            │                              │  reader (filter by source)
   ┌────────────────────────────┐         ┌──────────────────────────────┐
   │ vie-summarizer assembly    │         │ vie-assistant RAGService     │
   │  ├─ store_transcript_chunks│         │  ├─ search(video_id, sources)│
   │  │   source="transcript"   │         │  └─ search_library(video_ids)│
   │  └─ store_default_output_  │         └──────────────────────────────┘
   │      chunks                │
   │      source="default_output"│
   └────────────────────────────┘
```

**Single contract:**
- Payload: `source`, `video_id`, `user_id` (reserved, currently `null`),
  `tab_id`, `tab_component`, `prop_path`, `chunk_index`, `text`,
  `text_original`, `language`
- Point ID: `sha256(f"{source}:{video_id}:{tab_id or ''}:{prop_path or ''}:{chunk_idx}")` (transcript path keeps the legacy `f"{video_id}_{idx}"` for backward compat)
- Both writers pre-delete by `(video_id, source)` before upsert — orphan-free reprocess
- `EMBEDDING_MODEL_NAME` (summarizer) MUST match the model the assistant's `RAGService` loads (defaults to `all-MiniLM-L6-v2` in both); changing it requires re-ingesting fixtures

**When adding a new content source** (e.g. `user_notes`):
1. Add the source name to the writer's `source` literal
2. Pre-delete by the new `(video_id, source)` pair before upsert
3. Update the assistant's `RAGService.search` / `search_library` to accept it in `sources`
4. Update `output_chunker.py` if the new source has its own chunking rules

---

## Shared Types Strategy

### TypeScript (vie-api, vie-web)

Use `packages/types` for shared interfaces:

```typescript
// packages/types/src/video.ts
export interface VideoSummary {
  id: string;
  youtubeId: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  // ...
}

// Import in both vie-api and vie-web
import { VideoSummary } from '@vie/types';
```

### Python (vie-summarizer, vie-assistant)

Define Pydantic models, generate JSON schema if needed:

```python
# services/summarizer/src/models/schemas.py
class SummarizeRequest(BaseModel):
    videoSummaryId: str
    youtubeId: str
    url: str
    userId: str | None = None
```

### VIEResponse Contract (v2)

The pipeline produces two output formats stored side-by-side for backward compatibility:

| Field            | Format | Description                              |
| ---------------- | ------ | ---------------------------------------- |
| `triage`         | v1     | Content tags + tab layout (legacy)       |
| `output`         | v1     | Extraction + synthesis + enrichment      |
| `assembledMeta`  | v2     | VIEResponseMeta (domain, title, etc.)    |
| `assembledTabs`  | v2     | TabEntry[] (component-addressed tabs)    |

The frontend checks for `assembledTabs` first (v2 path). If absent, it falls back to building a VIEResponse from `triage` + `output` (v1 path).

### Assembly Stage as Cross-Cutting Concern

The assembly stage (`services/summarizer/src/services/pipeline/assembly/`, entry `core.py`) produces `TabEntry[]` that must be understood by both:

- **Python (summarizer)**: Produces and stores the assembled tabs (`assembly/registry.py` — `ASSEMBLER_REGISTRY`, 29 assemblers)
- **TypeScript (web)**: Renders tabs via `COMPONENT_REGISTRY` in `features/video-output/components/output/component-registry.tsx`

Each `TabEntry` has: `{ id, label, emoji, component, props, crossTabLinks?, attachments?, degradedFrom? }`

The `component` field maps to a React renderer. Adding a new component requires updates in:

1. the Python assembler registry (`assembly/registry.py`),
2. the TypeScript component registry (`component-registry.tsx`),
3. `packages/shared/src/config/domains.json` (`components` list, `densityGates`, per-domain `domainRequirements`, and a `playbooks` entry if format-specific), and
4. the frontend Zod prop boundary in `apps/web/src/features/video-output/lib/tab-prop-schemas.ts`.

### Type Sync Checklist

When adding a new type:
- [ ] Define in `packages/types` (TypeScript)
- [ ] Define in Python service schemas (`domain_types.py` — undeclared fields silently drop on `model_dump`)
- [ ] Update API docs with examples
- [ ] Verify JSON serialization matches
- [ ] If TabEntry-related: update the assembly package, `component-registry.tsx`, and `tab-prop-schemas.ts`

---

## Run and trace ids (P0b)

One attempt of the pipeline = one `runId` = one Langfuse trace. Who owns which id:

| Id | Minted by | Carried in | Stored | Read by |
|---|---|---|---|---|
| `runId` | vie-api `dispatchPipeline` (one per dispatch); the summarizer mints a fresh one, with `retryOf`, for an attempt the api did not dispatch or whose id already ran | queue payload `runId`, `POST /summarize` body `runId`, status callback `runId` | `videoSummaryCache.currentRunId`, `runs[].runId` | summarizer (trace id), api (closes `runs[]`), `POST /api/videos` + `GET /api/videos/:id` responses, eval |
| `traceId` | derived, never sent alone: `sha256(runId)` hex, first 32 chars (`traceIdForRun` in TS, `trace_id_for_run` in Python, same as `Langfuse.create_trace_id(seed=)`; one test vector pinned on both sides) | responses next to `runId` | `runs[].traceId` | admin deep links, eval |
| `requestId` | vie-api request id | `X-Request-ID`, queue `requestId` | row `requestId`, `runs[].requestId`, `llm_usage.request_id` | billing repair (late settle, assistant backfill), logs, Sentry |
| `sessionId` (P1) | the web, one uuid per chat surface (video chat / library chat), in the `vie-chat` store | `X-Session-Id` on every chat and action request → vie-api → vie-assistant; assistant → `/internal/assistant/generate` → `chatSessionId` in the queue payload and `POST /summarize` body | `runs[].chatSessionId`, `llm_usage.session_id` | Langfuse sessions `chat-<id>`, pipeline tag `chatSession:<id>`, logs, Sentry tag (api) |
| `billingRunId` | vie-api submission (token ledger) | read off the row by the summarizer, sent back in `POST /internal/run-cost` | row `billingRunId` | billing only; unchanged by P0b |
| session `pipeline-<videoSummaryId>` | derived by the summarizer | Langfuse only | | groups every attempt of a row |

### Correlation headers

| Header | Sent by | Validated | Forwarded to |
|---|---|---|---|
| `X-Request-ID` | any client (optional); vie-api answers with it on every response | `^[A-Za-z0-9_-]{8,128}$` (api `genRequestId`, Python `RequestContextMiddleware`); a bad or missing value → a fresh UUID | summarizer (`POST /summarize` header, queue `requestId`), assistant (all four calls), assistant → vie-api internal calls |
| `X-Session-Id` | the web on every assistant request (`lib/assistant.ts` `assistantHeaders()`) | the same pattern; a bad value is dropped, the request still runs | assistant (`/chat`, `/library/chat`, `/library/search`, `/action`); assistant → vie-api internal calls (so `generate_video` runs carry it) |

vie-api's CORS `allowedHeaders` lists `X-Session-Id`, which the browser sends; the web never sends `X-Request-ID` (vie-api mints it and returns it on the response).

## Error Handling Across Services

### Error Flow

```
vie-web          vie-api           vie-summarizer
   │                │                    │
   │   request      │                    │
   ├───────────────▶│    HTTP POST       │
   │                ├───────────────────▶│
   │                │                    │ error occurs
   │                │    (updates DB)    │
   │                │◀───────────────────┤
   │   websocket    │                    │
   │◀───────────────┤                    │
   │                │                    │
   show error       │                    │
```

### Error Code Consistency

All services must use the same error codes (from `docs/ERROR-HANDLING.md`):

| Code | HTTP | Service |
|------|------|---------|
| `NO_TRANSCRIPT` | 422 | vie-summarizer detects, vie-api returns |
| `VIDEO_TOO_LONG` | 422 | vie-summarizer detects, vie-api returns |
| `LLM_ERROR` | 500 | vie-summarizer/assistant detects |

### Error Translation

Each service translates errors at its boundary:

```typescript
// vie-api: Translate assistant errors to HTTP
try {
  const result = await assistantClient.chat(...);
  return result;
} catch (error) {
  if (error.code === 'NOT_FOUND') {
    throw new HttpError(404, 'VIDEO_NOT_FOUND', 'Video not found');
  }
  throw new HttpError(500, 'INTERNAL_ERROR', 'Something went wrong');
}
```

---

## Testing Cross-Service Features

### Integration Test Strategy

```
Unit Tests           Integration Tests           E2E Tests
(per service)        (service pairs)             (full flow)
     │                     │                         │
     │                     │                         │
vie-api tests        vie-api + MongoDB         vie-web + vie-api +
vie-web tests        vie-api + Summarizer      vie-summarizer +
vie-summarizer                                 MongoDB
tests
```

### What to Test Where

| Test Type | What to Test | Tools |
|-----------|--------------|-------|
| Unit | Service logic in isolation | Jest, pytest |
| Integration | API + Database | Supertest, TestClient |
| Contract | API response shapes | Zod, Pydantic |
| E2E | Full user flow | Playwright, Cypress |

### Integration Test Example

```typescript
// Test vie-api with mocked assistant client
describe('POST /api/videos/:id/chat', () => {
  it('returns streamed response from assistant', async () => {
    // Mock assistant client
    mockAssistant.chat.mockResolvedValue('The main ingredients are...');

    const response = await app.inject({
      method: 'POST',
      url: '/api/videos/123/chat',
      headers: { Authorization: `Bearer ${token}` },
      payload: { message: 'What are the ingredients?' },
    });

    expect(response.statusCode).toBe(200);
  });
});
```

---

## Deployment Considerations

### Service Dependencies

```yaml
# Startup order matters!
1. vie-mongodb      # Database first
2. vie-summarizer   # Service can start
3. vie-assistant    # Assistant service ready
4. vie-api          # API connects to all
5. vie-web          # Frontend last
```

### Health Checks

Each service should verify its dependencies:

```typescript
// vie-api health check
fastify.get('/health', async () => {
  const mongoOk = await checkMongo();
  const assistantOk = await checkAssistant();

  if (!mongoOk || !assistantOk) {
    return reply.code(503).send({ status: 'unhealthy', mongo: mongoOk, assistant: assistantOk });
  }

  return { status: 'healthy' };
});
```

---

## Quick Reference

### Which Skill to Load?

| Working on... | Primary Skill | Also Consider |
|---------------|---------------|---------------|
| vie-api route + vie-web component | backend-node, react-vite | This doc |
| vie-api + vie-summarizer | backend-node, backend-python | This doc |
| vie-api + vie-assistant | backend-node, backend-python | This doc |
| Full feature (all services) | All three + This doc | - |

### Key Documentation

| Need | Document |
|------|----------|
| API contracts (REST, WebSocket, SSE) | [docs/API-REFERENCE.md](./API-REFERENCE.md) |
| Error codes | [docs/ERROR-HANDLING.md](./ERROR-HANDLING.md) |
| Data models | [docs/DATA-MODELS.md](./DATA-MODELS.md) |
