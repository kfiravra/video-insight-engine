# Service: vie-assistant

Python service providing video-aware conversational AI with RAG-powered context retrieval and tool routing.

**Type:** HTTP service (FastAPI + SSE streaming)

---

## Tech Stack

| Technology | Purpose |
|------------|---------|
| Python 3.12+ | Runtime |
| FastAPI | Web framework + SSE streaming |
| LiteLLM | Multi-provider LLM abstraction (Anthropic, OpenAI, Gemini) |
| sentence-transformers | Embedding model for RAG search |
| Qdrant | Vector database for transcript chunk retrieval |
| Motor (MongoDB) | Async MongoDB driver for video context + notes |
| Pydantic | Request/response validation + settings |
| structlog | Structured logging |

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │              vie-api (gateway)            │
                    │  /chat, /library/{chat,search}, /action   │
                    └────────────────┬─────────────────────────┘
                                     │ X-Internal-Secret (+X-User-Id)
                                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        vie-assistant (:8001)                        │
│                                                                     │
│  Chat (both modes: /chat and /library/chat)                         │
│  ┌──────────────┐   ┌───────────────────────────────────────────┐  │
│  │ RAG retrieve │──▶│ Agentic loop (agent_loop.py)              │  │
│  │ encode →     │   │  LLM owns tool selection each round;      │  │
│  │ Qdrant top-k │   │  budgets: 4 iters / 5 calls / 15 per req  │  │
│  │ → relevance  │   │  destructive/costly calls park behind the │  │
│  │ floor → dedup│   │  ConfirmationGate until the user confirms │  │
│  └──────────────┘   └───────────────────────────────────────────┘  │
│                                                                     │
│  Structured /action (deterministic, no intent detection)            │
│  ┌───────────────────────────────┐   ┌──────────────────────────┐  │
│  │ tool_router.py                │──▶│ Tools                    │  │
│  │  ToolRouter = name-keyed      │   │  navigator, note_taker   │  │
│  │  registry; ActionDispatcher   │   │  concept_explain, quiz   │  │
│  │  validates params + dispatches│   │  folder/library organize │  │
│  └───────────────────────────────┘   │  video_generator         │  │
│                                       └──────────────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │ Qdrant   │  │ MongoDB  │  │ LiteLLM  │  │ vie-api ApiClient │   │
│  │ (vectors)│  │ (context)│  │ (LLM)    │  │ (internal writes) │   │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

Free-form chat has **no keyword intent detection** — the agentic loop lets the
LLM pick tools in both chat modes. `tool_router.py` survives only as the
registry + `ActionDispatcher` behind the structured `POST /action` endpoint.
All public endpoints are rate-limited in-memory per caller (`rate_limit.py`).

---

## Project Structure

```
services/assistant/
├── Dockerfile
├── requirements.txt
├── pyproject.toml
└── src/
    ├── server.py                 # FastAPI app + routes
    ├── bootstrap.py              # Lifespan: dependency wiring + tool registration
    ├── config.py                 # Settings + model mapping
    ├── exceptions.py             # AppError hierarchy
    ├── logging_config.py         # structlog setup
    │
    ├── models/
    │   ├── requests.py           # ChatRequest, ActionRequest, ActionName Literal
    │   └── responses.py          # RAGSource, ChatEvent, ActionResponse
    │
    ├── services/
    │   ├── assistant.py          # AssistantService (orchestrator)
    │   ├── agent_loop.py         # Agentic tool-calling loop (both chat modes)
    │   ├── agent_tools.py        # Agent tool schemas + execution
    │   ├── confirmation.py       # ConfirmationGate for destructive/costly actions
    │   ├── tool_router.py        # Tool registry + ActionDispatcher (/action only)
    │   ├── rate_limit.py         # In-memory sliding-window rate limits
    │   ├── llm_provider.py       # LiteLLM multi-provider abstraction
    │   ├── rag.py                # RAGService (embed + Qdrant search + floor + dedup)
    │   ├── context_builder.py    # System prompt assembly
    │   ├── api_client.py         # Outbound vie-api client (internal writes)
    │   └── observability/        # Langfuse session traces + spans
    │
    ├── repositories/
    │   ├── video_repository.py   # VideoContext from MongoDB
    │   ├── notes_repository.py   # Notes CRUD
    │   └── qdrant_repository.py  # Qdrant vector search
    │
    ├── tools/
    │   ├── base.py               # BaseTool protocol
    │   ├── navigator.py          # Tab/section fuzzy search
    │   ├── concept_explain.py    # Deep concept explanations (Sonnet)
    │   ├── quiz_generator.py     # Quiz generation (fast model)
    │   ├── note_taker.py         # Save notes to MongoDB
    │   ├── folder_organizer.py   # Folder CRUD via vie-api
    │   ├── library_organizer.py  # LLM-planned library reorganization
    │   └── video_generator.py    # Dispatch new video processing via vie-api
    │
    └── utils/
        ├── prompt_templates.py   # Prompt template strings
        └── language_detect.py    # User language detection for query translation
```

---

## Endpoints

### `GET /health`

Health check. No authentication required.

**Response** `200`:
```json
{
  "status": "healthy",
  "service": "vie-assistant",
  "model": "anthropic/claude-sonnet-4-6"
}
```

### `POST /chat`

Stream a conversational response about a video via SSE. Requires `X-Internal-Secret` header.

Single-video chat is **not toolless**: `_rag_chat` routes through `_run_agentic_loop`, so the same library action tools as `/library/chat` are offered **when a `user_id` is present** (forwarded as `X-User-Id`). Tool exposure is gated by `_agent_instructions_prefix` — with no `api_client` or no `user_id`, the agent instructions and tools are omitted and it behaves as plain RAG. (Before this fix, opening a video silently switched the assistant to a no-tools chat that refused actions.)

**Request:**
```json
{
  "video_id": "dQw4w9WgXcQ",
  "message": "What are the key points about attention?",
  "conversation_history": [
    { "role": "user", "content": "Tell me about this video" },
    { "role": "assistant", "content": "This video covers..." }
  ]
}
```

**Response** `200` (`text/event-stream`):
```
data: {"type":"source","sources":[{"text":"...","timestamp":"1:23","timestamp_seconds":83.0,"end_seconds":95.5,"score":0.92}]}

data: {"type":"text","content":"The"}

data: {"type":"text","content":" video"}

data: {"type":"done","metadata":{"video_id":"dQw4w9WgXcQ","sources_count":3}}

```

### `POST /library/search`

Semantic search across a library of videos. Pure retrieval — no LLM call. Requires `X-Internal-Secret` header. Trusts the caller (vie-api gateway) to verify that the requesting user owns or has access to every `video_id` in the list.

**Request:**
```json
{
  "video_ids": ["dQw4w9WgXcQ", "abc123", "xyz789"],
  "query": "React coding with hooks",
  "top_k": 10,
  "sources": ["transcript", "default_output"]
}
```

| Field | Type | Constraints |
|---|---|---|
| `video_ids` | `string[]` | 1–200 items; each matches `^[A-Za-z0-9_\-]+$`, max 64 chars |
| `query` | `string` | 1–500 chars |
| `top_k` | `int` | 1–50 (default 10) |
| `sources` | `string[]` \| `null` | Optional. Subset of `["transcript", "default_output"]`. `null` returns all sources. |

**Response** `200`:
```json
{
  "results": [
    {
      "text": "Hooks are functions that let you tap into React's state and lifecycle features...",
      "video_id": "dQw4w9WgXcQ",
      "score": 0.741,
      "chunk_index": 1,
      "timestamp": "1:23",
      "timestamp_seconds": 83.0,
      "end_seconds": 95.5,
      "source": "transcript",
      "tab_id": null,
      "tab_component": null,
      "prop_path": null
    },
    {
      "text": "Use useState for local component state with simple value-and-setter destructuring patterns.",
      "video_id": "dQw4w9WgXcQ",
      "score": 0.684,
      "chunk_index": 0,
      "source": "default_output",
      "tab_id": "overview_tab",
      "tab_component": "overview",
      "prop_path": "keyTakeaways[0]"
    }
  ]
}
```

**Errors:**
- `403` — invalid or missing `X-Internal-Secret`
- `422` — validation error (empty `video_ids`, invalid char in id, oversized `top_k`, missing `query`)
- `429` — rate limit exceeded (60/min/caller — keyed on `X-User-Id` forwarded by vie-api; falls back to a shared `anonymous` bucket if the header is absent. Separate bucket from `/chat`.)
- `503` — RAG service not initialized (lifespan hasn't completed)

### `POST /library/chat`

Library-wide RAG **chat** (SSE) — answers across many videos when no single video is open. Same internal-secret + `X-User-Id`/`X-Session-Id` headers and SSE event shapes as `/chat` (`source` / `text` / `error` / `done`); each `source.video_id` is the YouTube ID so the UI can attribute/deep-link. Pairs with the retrieval-only `/library/search` above.

**Request:** `{ "video_ids": ["abc123", ...], "message": "which of my videos covered hooks?", "conversation_history": [] }` — `video_ids` 0–200 (empty allowed → friendly "nothing found"); `message` 1–10000. The vie-api gateway derives `video_ids` from the user's `userVideos` server-side; the assistant trusts the list (same pattern as `/chat`).

**Gateway routes (vie-api):** `POST /api/assistant/library/chat` and `/library/search` (JWT) resolve owned YouTube IDs via `getUserVideos` then forward with `X-Internal-Secret` + `X-User-Id`; `POST /api/assistant/action` (JWT) proxies to `/action`. User-scoped writes flow assistant → `POST /internal/assistant/{folders,videos,generate}` (vie-api `authenticateInternal`: `X-Internal-Secret` + `X-User-Id`) → existing services; generate goes through `videoService.createVideo` (cost reservation + dispatch-guard preserved).

### `POST /action`

Structured action endpoint — dispatches to a registered tool based on `action`. `video_id` is **optional** (omit for library-scoped actions). Actions: `save_note`, `quiz_me`, `find_moment`, `explain` (video-scoped) plus `generate_video`, `organize_library`, `create_folder`, `rename_folder`, `move_folder`, `delete_folder`, `move_video` (library/action channel — these call back into vie-api via the assistant `ApiClient`).

**Headers**
- `X-Internal-Secret: <secret>` — required
- `X-User-Id: <user_id>` — optional; used to attribute notes and key the rate limit bucket

**Request:**
```json
{
  "video_id": "abc123",
  "action": "save_note",
  "params": { "text": "Remember this for later" }
}
```

**Actions and required params:**

| Action | Tool | Required params | Optional params |
|--------|------|-----------------|-----------------|
| `save_note` | `note_taker` | `text` | `timestamp` |
| `quiz_me` | `quiz_generator` | (none) | `topic`, `num_questions` |
| `find_moment` | `navigator` | `query` | — |
| `explain` | `concept_explain` | `concept` | — |

**Response (200):**
```json
{
  "success": true,
  "action": "save_note",
  "data": { "saved": true, "note_id": "uuid" },
  "error": null,
  "trace_id": "abcdef012345"
}
```

The same envelope is used for 400/404 with `success: false` and `error` populated. `trace_id` is a 12-char hex ID for log correlation.

**Errors:**
- `400` — missing required param (e.g., `save_note` without `text`)
- `403` — invalid or missing `X-Internal-Secret`
- `404` — video not found
- `422` — invalid `action` value at schema level
- `429` — rate limit exceeded (30/min/caller — keyed on `X-User-Id` if present, else falls back to `video_id`)
- `503` — assistant not initialized

---

## SSE Event Types

| Type | Description | Payload |
|------|-------------|---------|
| `text` | Streamed LLM token | `content: string` |
| `source` | RAG sources used for context (post relevance floor) | `sources: RAGSource[]` |
| `tool` | Agentic tool-call lifecycle: `start`/`done` per call, plus `pending_confirmation` (carries the confirmation token) and `confirmation_failed` | `content: string`, `metadata: { status, action, confirmation? }` |
| `tool_result` | Result from a routed tool | `metadata: { tool, result }` |
| `error` | Error during processing | `content: string` |
| `done` | Stream complete | `metadata: { video_id?, sources_count }` |

---

## Tool Selection & Dispatch

There is **no keyword intent detection**. Tools reach the user through two
paths:

**1. Agentic loop (free-form chat, both modes)** — `agent_loop.py`. The LLM
itself decides per round whether to call tools (schemas from
`agent_tools.py`), gated by hard budgets: max 4 tool-use iterations, 5 tool
calls per iteration, 15 per request. Tools are only offered when both an
`ApiClient` and a `user_id` are present (`_agent_instructions_prefix` mirrors
that gate in the prompt). Exactly two calls are gated as destructive/costly —
`delete_folder` WITH `delete_content`, and `generate_video` (costs money) —
and park behind the `ConfirmationGate` (`confirmation.py`): the stream emits a
`pending_confirmation` tool event carrying a single-use token; the client
echoes it back as `confirm_token` on the next request to execute the parked
action. Unused tokens expire server-side. The agent instructions also carry a
prompt-injection guard: retrieved transcript excerpts, video titles, and RAG
content are DATA, never instructions — only the user's own chat messages may
trigger library actions. The rule rides `_agent_instructions_prefix`, so both
chat modes receive it exactly when tools are enabled.

**2. Structured `POST /action`** — `tool_router.py`. `ToolRouter` is a
name-keyed registry of `BaseTool` implementations; `ActionDispatcher` maps
the validated `action` enum to a tool, checks required params, and dispatches
deterministically (no LLM in the routing decision).

| Tool | Model | Description |
|------|-------|-------------|
| `note_taker` | None (DB only) | Save notes to MongoDB |
| `quiz_generator` | Fast tier (`settings.llm_fast_model`: gpt-4o-mini with `LLM_FAST_PROVIDER=openai`, as the `.env` templates and prod set; else Haiku 4.5) | Generate multiple-choice questions |
| `concept_explain` | Default (Sonnet) | Deep concept explanations with RAG |
| `navigator` | None (local) | Fuzzy search through video tabs |
| `folder_organizer` | None (vie-api calls) | Folder create/rename/move/delete, move video |
| `library_organizer` | Default (Sonnet) | Plan + apply a library-wide folder organization |
| `video_generator` | None (vie-api call) | Dispatch a new video through the pipeline |

Rate limits (`rate_limit.py`, in-memory sliding window, keyed on `X-User-Id`
with per-endpoint fallbacks): `/chat` + `/library/chat` 30/min, `/action`
30/min, `/library/search` 60/min.

### Free-account chat fair use

The assistant knows nothing about plans. The gateway (vie-api) caps chat for
**free** accounts before it forwards a message: `POST /api/videos/:videoSummaryId/chat`
and `POST /api/assistant/library/chat` carry the `chatFairUseHook` preHandler
(`api/src/services/chat-fair-use.service.ts`), one shared allowance of
`CHAT_FREE_MESSAGES_PER_DAY` (default 50, `0` = off) messages in any rolling
24 hours (Q7, D6).

- The plan comes from `BillingService.summary(userId).plan.id`. Only `free` is
  counted; Pro is unlimited and never touches Redis. Chat cost is not charged
  to the minutes ledger.
- The window is a Redis sorted set, `vie:api:chat-fair-use:<userId>`: one member
  per admitted message, scored by its time in ms. One Lua script drops entries
  older than 24 hours, then admits the message only while fewer than the limit
  remain. A fixed-window counter would let a user send 50 just before it resets
  and 50 just after, and could not say when the next message is allowed. The
  set holds at most 50 short members per free user and expires after 24 hours
  idle.
- Over the limit the gateway answers `429 CHAT_LIMIT_REACHED` with `nextAt` =
  oldest counted message + 24 hours (see [ERROR-HANDLING.md](./ERROR-HANDLING.md)).
  Refused messages are not recorded, so retrying never extends the wait. The
  assistant's own 30/min window still applies on top.
- Fail-open: a Redis or plan-lookup error logs a warning and forwards the
  message. The `/action` routes are not chat messages and are not counted.
- The shared demo account is an ordinary free account, so every demo visitor
  draws on one allowance.

---

## RAG Pipeline

1. **Detect** user language (`language_detect.py`)
2. **Translate** non-English queries to English for RAG search (LLM translation)
3. **Encode** query via sentence-transformers (model name from `EMBEDDING_MODEL_NAME` setting, default `all-MiniLM-L6-v2`, lazy-loaded at startup — must match the summarizer's index-side value; a parity test guards the defaults)
4. **Search** Qdrant for top-k chunks filtered by `video_ids` (single-video uses `MatchValue`; multi-video uses `MatchAny`) and optionally by `sources` (subset of `transcript`/`default_output`)
5. **Filter** hits below the relevance floor (`RAG_MIN_SCORE`, default 0.25 cosine similarity) — Qdrant's top-k is unconditional, so off-topic questions would otherwise stuff the k least-unrelated chunks into the prompt. If nothing survives, the system prompt tells the model to say it found nothing relevant instead of guessing
6. **Deduplicate** near-identical chunks using cosine similarity (threshold: 0.95)
7. **Build** system prompt with video metadata + RAG chunks + conversation history
   - Uses `text_original` (Qdrant payload) when user language matches video language (non-English)
   - Uses the English-primary `meta`/`tabs` (with the original artifact nested under `sourceLanguage`) for English users on non-English videos — replaces the legacy `synthesis_en`/`tabs_en` triple
   - Appends language instruction for non-English responses; when chunks carry `[M:SS]` timestamps the prompt teaches the model to cite them
8. **Stream** LLM response token by token via SSE

### Retrieval payload schema

Each result carries enough metadata for the UI to render a deep link back into a specific tab:

| Field | Description |
|---|---|
| `text` | Natural-language chunk used for retrieval |
| `text_original` | Original-language text (only set for non-English transcripts) |
| `video_id` | Source video — populated for multi-video / library queries |
| `score` | Cosine similarity score (post relevance floor, so always ≥ `RAG_MIN_SCORE`) |
| `timestamp` | Formatted `M:SS` / `H:MM:SS` display string (null for v1-legacy points) |
| `timestamp_seconds` / `end_seconds` | Numeric chunk start/end seconds (payload schema v2) — drive the UI seek and `&t=` deep-link buttons; null for v1-legacy points |
| `source` | `"transcript"` or `"default_output"` |
| `tab_id` / `tab_component` / `prop_path` | Set when `source == "default_output"`. Identify the originating tab and prop (e.g. `tab_component="quiz"`, `prop_path="questions[2]"`) |
| `chunk_index` | Position within the (video_id, source, tab_id) group |

### Two retrieval entry points

| Method | Scope | Used by |
|---|---|---|
| `RAGService.search(query, video_id, top_k, sources)` | Single video | `/chat`, `concept_explain` tool |
| `RAGService.search_library(query, video_ids, top_k, sources)` | Many videos at once; populates `video_id` on every result so the UI can group | `POST /library/search` |

---

## Environment Variables

```bash
# Server
ASSISTANT_PORT=8001

# MongoDB
MONGODB_URI=mongodb://vie-mongodb:27017/video-insight-engine

# Qdrant
QDRANT_URL=http://vie-qdrant:6333
QDRANT_COLLECTION=transcript_chunks

# RAG retrieval
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2  # MUST match summarizer's index-side value
RAG_MIN_SCORE=0.25                     # Relevance floor (cosine); 0 disables

# Internal auth (service-to-service)
INTERNAL_SECRET=dev-internal-secret-change-me

# LLM Provider
LLM_PROVIDER=anthropic          # anthropic | openai | gemini
LLM_FAST_PROVIDER=              # Optional: separate provider for fast model
LLM_FALLBACK_PROVIDER=          # Optional: fallback provider
LLM_MODEL=                      # Override default model
LLM_FAST_MODEL=                 # Override fast model
LLM_CHAT_MODEL=                 # Model for chat + agentic loop (default: primary provider's fast tier)
LLM_TIMEOUT_SECONDS=60.0
LLM_NUM_RETRIES=2

# Provider API Keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=
GEMINI_API_KEY=

# Assistant limits
MAX_CONTEXT_CHUNKS=8
MAX_CONVERSATION_TURNS=20

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=console              # console | json
```

---

## Dependencies

| Service | Purpose | Required |
|---------|---------|----------|
| MongoDB | Video context, notes storage | Yes |
| Qdrant | Transcript chunk vector search | Yes |
| LiteLLM | LLM completion (Anthropic/OpenAI/Gemini) | Yes |
| sentence-transformers | Query embedding for RAG | Yes |
| vie-api | Gateway proxy (forwards requests) | Yes (production) |

---

## Docker

```bash
# Build
docker build -f services/assistant/Dockerfile -t vie-assistant .

# Run
docker run -p 8001:8001 \
  -e MONGODB_URI=mongodb://host:27017/video-insight-engine \
  -e QDRANT_URL=http://host:6333 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e INTERNAL_SECRET=your-secret \
  vie-assistant
```

The service runs via uvicorn: `uvicorn src.server:app --host 0.0.0.0 --port 8001`

---

## Development

```bash
cd services/assistant

# Install dependencies
pip install -r requirements.txt

# Run tests
python3 -m pytest tests/ -v

# Run specific tool tests
python3 -m pytest tests/test_tools/ -v

# Run with auto-reload
uvicorn src.server:app --reload --port 8001
```

Test configuration in `pyproject.toml`: `asyncio_mode = "auto"` (all async tests run automatically).

---

## Error Handling

Exception hierarchy rooted in `AppError`:

| Exception | Status | Code | When |
|-----------|--------|------|------|
| `AppError` | 500 | `INTERNAL_ERROR` | Base class |
| `NotFoundError` | 404 | `NOT_FOUND` | Video not in MongoDB |
| `LLMError` | 502 | `LLM_ERROR` | LLM provider failure (timeout, rate limit, auth) |
| `ValidationError` | 400 | `VALIDATION_ERROR` | Invalid input (empty note, bad params) |
| `ServiceUnavailableError` | 503 | `SERVICE_UNAVAILABLE` | External service down |

All exceptions return JSON: `{ "error": "message", "code": "ERROR_CODE" }`.

Unhandled exceptions are caught by a generic handler and return 500 with `INTERNAL_ERROR`. Stack traces are logged but never exposed to clients.
