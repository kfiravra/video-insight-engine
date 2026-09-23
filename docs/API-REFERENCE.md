# API Reference

Complete API documentation for all Video Insight Engine services.

---

## Table of Contents

- [REST API](#rest-api) - vie-api HTTP endpoints
- [WebSocket API](#websocket-api) - Real-time updates
- [Assistant Chat API](#assistant-chat-api) - AI-powered video chat
- [SSE Streaming API](#sse-streaming-api) - Progressive summarization

---

# REST API

All endpoints served by `vie-api` on port 3000.

Base URL: `/api`

---

## Authentication

### POST /auth/register

Create new account.

**Request:**

```json
{
  "email": "user@example.com",
  "password": "SecurePass123",
  "name": "John Doe"
}
```

**Response (201):**

```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIs...",
  "expiresIn": 900,
  "user": {
    "id": "507f1f77bcf86cd799439011",
    "email": "user@example.com",
    "name": "John Doe"
  }
}
```

**Cookies Set:**

```
Set-Cookie: refreshToken=xxx; HttpOnly; Secure; SameSite=Strict; Path=/api/auth/refresh; Max-Age=604800
```

---

### POST /auth/login

**Request:**

```json
{
  "email": "user@example.com",
  "password": "SecurePass123"
}
```

**Response (200):**

```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIs...",
  "expiresIn": 900,
  "user": {
    "id": "507f1f77bcf86cd799439011",
    "email": "user@example.com",
    "name": "John Doe"
  }
}
```

**Cookies Set:**

```
Set-Cookie: refreshToken=xxx; HttpOnly; Secure; SameSite=Strict; Path=/api/auth/refresh; Max-Age=604800
```

---

### POST /auth/refresh

Get new access token using refresh token cookie.

**Cookies Required:** `refreshToken` (HttpOnly cookie)

**Response (200):**

```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIs...",
  "expiresIn": 900
}
```

**Error (401):**

```json
{
  "error": "REFRESH_EXPIRED",
  "message": "Session expired, please login again",
  "statusCode": 401
}
```

---

### POST /auth/logout

Clear refresh token and end session.

**Cookies Required:** `refreshToken` (HttpOnly cookie)

**Response (200):**

```json
{
  "success": true
}
```

**Cookies Cleared:**

```
Set-Cookie: refreshToken=; HttpOnly; Path=/api/auth/refresh; Max-Age=0
```

---

### GET /auth/me

Get current user.

**Headers:** `Authorization: Bearer {accessToken}`

**Response (200):**

```json
{
  "id": "507f1f77bcf86cd799439011",
  "email": "user@example.com",
  "name": "John Doe"
}
```

---

## Folders

### GET /folders

List user's folders.

**Response (200):**

```json
{
  "folders": [
    {
      "id": "507f1f77bcf86cd799439012",
      "name": "AI Learning",
      "parentId": null,
      "path": "/AI Learning",
      "level": 1,
      "color": "#3B82F6",
      "icon": "folder"
    }
  ]
}
```

---

### POST /folders

Create folder.

**Request:**

```json
{
  "name": "React Tutorials",
  "parentId": "507f1f77bcf86cd799439012",
  "color": "#10B981",
  "icon": "code"
}
```

**Response (201):**

```json
{
  "id": "507f1f77bcf86cd799439013",
  "name": "React Tutorials",
  "parentId": "507f1f77bcf86cd799439012",
  "path": "/AI Learning/React Tutorials",
  "level": 2
}
```

---

### PATCH /folders/:id

Update folder.

**Request:**

```json
{
  "name": "React & Hooks",
  "parentId": null
}
```

**Response (200):** Updated folder object.

---

### DELETE /folders/:id

Delete folder. Contents moved to parent (or unfiled).

**Response:** `204 No Content`

---

## Videos

### GET /videos

List user's videos (offset-paginated, newest first).

**Query:**

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `folderId` | ObjectId | — | Optional folder filter |
| `limit` | int | 50 | Page size, 1–100 (values outside the range → 400) |
| `offset` | int | 0 | Items to skip, ≥ 0 |

**Response (200):**

```json
{
  "videos": [
    {
      "id": "507f1f77bcf86cd799439014",
      "videoSummaryId": "507f1f77bcf86cd799439020",
      "youtubeId": "dQw4w9WgXcQ",
      "title": "React Hooks Tutorial",
      "channel": "Fireship",
      "duration": 1200,
      "thumbnailUrl": "https://img.youtube.com/...",
      "status": "completed",
      "folderId": "507f1f77bcf86cd799439013",
      "createdAt": "2024-01-15T10:00:00Z"
    }
  ],
  "pagination": { "limit": 50, "offset": 0, "total": 137 }
}
```

`pagination.total` is the full count of the filtered set (not the page size), so clients can page with `offset += limit` while `offset < total`. The `pagination` key is an additive sibling — existing consumers that only read `videos` are unaffected.

---

### GET /videos/:id

Get video with summary.

**Response (200):**

```json
{
  "video": {
    "id": "507f1f77bcf86cd799439014",
    "youtubeId": "dQw4w9WgXcQ",
    "title": "React Hooks Tutorial",
    "status": "completed"
  },
  "summary": {
    "tldr": "Comprehensive guide to React Hooks...",
    "keyTakeaways": ["useState for state", "useEffect for side effects"],
    "chapters": [
      {
        "id": "ch-001",
        "timestamp": "00:00",
        "startSeconds": 0,
        "endSeconds": 180,
        "title": "Introduction",
        "isCreatorChapter": true,
        "content": [
          {"blockId": "uuid-1", "type": "paragraph", "text": "..."}
        ],
        "summary": "Overview of React Hooks...",
        "bullets": ["What are hooks", "Why use them"]
      }
    ],
    "concepts": [
      {
        "id": "con-001",
        "name": "useState",
        "definition": "Hook for managing state in functional components",
        "timestamp": "02:30"
      }
    ]
  },

  // sourceLanguage — present ONLY for non-English videos whose translation
  // phase completed. English-source videos and sound-only videos omit
  // this field entirely (not `null`). The FE presence-checks it to decide
  // whether to render the language toggle.
  "sourceLanguage": {
    "code": "he",
    "name": "עברית",
    "isRTL": true,
    "tabs": [/* TabEntry[] in the source language — mirrors top-level shape */],
    "meta": {/* VIEResponseMeta in the source language */}
  }
}
```

The same `sourceLanguage` block is included on the public share endpoint (`GET /share/:slug`) and is consumed by `apps/web/src/api/share.ts` + `apps/web/src/api/videos.ts`.

---

### POST /videos

Submit YouTube URL for summarization.

**Request:**

```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "folderId": "507f1f77bcf86cd799439013"
}
```

**Logic:**

1. Extract `youtubeId` from URL
2. Check `videoSummaryCache` for existing summary
3. If **HIT**: create `userVideo` reference, return immediately
4. If **MISS**: create cache entry, publish job, return with `status: pending`

**Response (201) - Cache Hit:**

```json
{
  "video": {
    "id": "507f1f77bcf86cd799439014",
    "videoSummaryId": "507f1f77bcf86cd799439020",
    "youtubeId": "dQw4w9WgXcQ",
    "status": "completed",
    "title": "React Hooks Tutorial"
  },
  "cached": true
}
```

**Response (201) - Cache Miss:**

```json
{
  "video": {
    "id": "507f1f77bcf86cd799439014",
    "videoSummaryId": "507f1f77bcf86cd799439020",
    "youtubeId": "dQw4w9WgXcQ",
    "status": "pending"
  },
  "cached": false
}
```

**Response (429) - Daily Cost Limit Reached:**

The handler atomically reserves an estimated cost against the user's daily cap
(`COST_LIMITS_PER_TIER`) before publishing the job. If the reservation pushes
the user past the cap, the increment is refunded and the request fails:

```json
{
  "error": "DAILY_LIMIT_REACHED",
  "message": "You've reached your daily cost limit ($2.00). Resets at midnight UTC.",
  "resetAt": "2026-05-15T00:00:00.000Z",
  "limitUsd": 2
}
```

Cache hits refund the reservation immediately. Real spend is reconciled from
`llm_usage` when the pipeline reaches a terminal status (see
[Per-User Cost Model](./llm-cost-model.md)).

---

### DELETE /videos/:id

Remove video from user's library.

**Note:** Removes the caller's `userVideos` row, the idempotency keys that completed against it and, unless the caller still holds the video in another folder, their own assistant notes for it. The shared summary, vectors and frames stay (see `DELETE /api/admin/videos/:youtubeId`). `404 VIDEO_NOT_FOUND` when the row is not the caller's.

**Response:** `204 No Content`

---

## Playlists

### POST /playlists/preview

Preview a playlist before importing.

**Rate Limit:** 30 requests per hour

**Request:**

```json
{
  "url": "https://www.youtube.com/playlist?list=PLxxx",
  "maxVideos": 100
}
```

**Response (200):**

```json
{
  "playlist": {
    "playlistId": "PLxxx",
    "title": "React Tutorial Series",
    "channel": "Fireship",
    "thumbnailUrl": "https://img.youtube.com/...",
    "totalVideos": 15,
    "videos": [
      {
        "videoId": "dQw4w9WgXcQ",
        "title": "React Hooks",
        "position": 0,
        "duration": 1200,
        "thumbnailUrl": "https://img.youtube.com/...",
        "isCached": true
      }
    ],
    "cachedCount": 5
  }
}
```

---

### POST /playlists/import

Import a playlist, creating a folder and adding all videos.

**Rate Limit:** 5 requests per 24 hours

**Request:**

```json
{
  "url": "https://www.youtube.com/playlist?list=PLxxx",
  "folderId": "507f1f77bcf86cd799439013",
  "maxVideos": 100
}
```

**Response (201):**

```json
{
  "folder": {
    "id": "507f1f77bcf86cd799439013",
    "name": "React Tutorial Series"
  },
  "videos": [
    {
      "id": "507f1f77bcf86cd799439014",
      "videoSummaryId": "507f1f77bcf86cd799439020",
      "youtubeId": "dQw4w9WgXcQ",
      "title": "React Hooks",
      "status": "completed",
      "position": 0
    }
  ],
  "totalVideos": 15,
  "cachedCount": 5,
  "processingCount": 10,
  "failedCount": 0
}
```

---

### GET /playlists/:playlistId/videos

Get videos in a playlist, sorted by position.

**Response (200):**

```json
{
  "videos": [
    {
      "id": "507f1f77bcf86cd799439014",
      "videoSummaryId": "507f1f77bcf86cd799439020",
      "youtubeId": "dQw4w9WgXcQ",
      "title": "React Hooks",
      "channel": "Fireship",
      "duration": 1200,
      "thumbnailUrl": "https://img.youtube.com/...",
      "status": "completed",
      "folderId": "507f1f77bcf86cd799439013",
      "playlistInfo": {
        "playlistId": "PLxxx",
        "playlistTitle": "React Tutorial Series",
        "position": 0,
        "totalVideos": 15
      },
      "createdAt": "2024-01-15T10:00:00Z"
    }
  ]
}
```

---

## Explain

### GET /explain/:videoSummaryId/:targetType/:targetId

Get auto-generated expansion for a section or concept ("Go Deeper").

**Parameters:**

- `videoSummaryId`: ID of videoSummaryCache entry
- `targetType`: `section` or `concept`
- `targetId`: UUID of section or concept

**Logic:** Calls vie-assistant HTTP API. Results are cached in `systemExpansionCache`.

**Response (200):**

```json
{
  "expansion": "# Documentation: Foundation & The Autolyse Phase\n\n**Video Section:** Mixing Ingredients..."
}
```

---

### POST /explain/video-chat

Send a message about a video. Ephemeral — no server-side persistence.

**Request:**

```json
{
  "videoSummaryId": "507f1f77bcf86cd799439020",
  "message": "What are the main ingredients?",
  "chatHistory": [
    { "role": "user", "content": "Hello" },
    { "role": "assistant", "content": "Hi! How can I help?" }
  ]
}
```

**Logic:** Calls vie-assistant HTTP API. Chat history is passed from client-side React state.

**Response (200):**

```json
{
  "response": "The main ingredients for this bread recipe are..."
}
```

---

## Share

### POST /share/:videoSummaryId

Generate a share link for a video summary. Auth required.

**Rate Limit:** 20 per hour

**Response (201):**

```json
{
  "shareSlug": "aBcDeFgHiJ",
  "shareUrl": "/s/aBcDeFgHiJ"
}
```

---

### GET /share/:slug

Get public summary by share slug. No auth required.

**Rate Limit:** 100 per minute

**Response (200):**

```json
{
  "id": "507f1f77bcf86cd799439020",
  "youtubeId": "dQw4w9WgXcQ",
  "title": "React Hooks Tutorial",
  "channel": "Fireship",
  "thumbnailUrl": "https://img.youtube.com/...",
  "duration": 1200,
  "contentTags": ["tech", "learning"],
  "primaryTag": "tech",
  "context": { "contentTags": ["tech", "learning"], "primaryTag": "tech", "tags": ["react"] },
  "summary": { "tldr": "...", "chapters": [...] },
  "shareSlug": "aBcDeFgHiJ",
  "viewsCount": 42,
  "likesCount": 7,
  "sharedAt": "2026-03-01T10:00:00Z"
}
```

---

### POST /share/:slug/like

Like a shared summary. No auth, IP rate limited.

**Rate Limit:** 10 per minute per IP

**Response (200):**

```json
{
  "likesCount": 8
}
```

---

## Override

### PATCH /videos/:id/override-category

Override the detected classification for a video. Triggers re-classification through the plan stage. Auth required.

**Request:**

```json
{
  "category": "cooking"
}
```

**Response (200):**

```json
{
  "videoSummaryId": "507f1f77bcf86cd799439020",
  "category": "cooking",
  "outputType": "recipe",
  "previousCategory": "standard"
}
```

> **Note:** This endpoint now triggers re-classification, not just a category override. The video will be reprocessed with the new domain hint.

**Valid categories:** `cooking`, `coding`, `fitness`, `travel`, `education`, `podcast`, `reviews`, `gaming`, `diy`, `music`, `standard`

---

## Payments

### POST /payments/webhook

Paddle webhook handler. Verifies signature in production.

**Headers:** `Paddle-Signature` or `X-Paddle-Signature`

**Response (200):**

```json
{
  "received": true
}
```

**Handled events:**
- `subscription.created` — set tier to 'pro' or 'team'
- `subscription.updated` — update tier
- `subscription.cancelled` — schedule downgrade
- `subscription.past_due` — grace period

---

### GET /payments/checkout

Generate Paddle checkout URL. Auth required.

**Rate Limit:** 10 per hour

**Query:** `?tier=pro|team`

**Response (200):**

```json
{
  "checkoutUrl": "https://checkout.paddle.com/..."
}
```

---

### GET /payments/tier

Get current user tier and limits. Auth required.

**Rate Limit:** 60 per minute

**Response (200):**

```json
{
  "tier": "free",
  "limits": {
    "videosPerDay": 3,
    "chatPerOutput": 5,
    "shareEnabled": true,
    "exportEnabled": false
  }
}
```

---

## Usage

### GET /users/me/usage

Today's LLM cost spend, remaining headroom, and the next UTC midnight reset.
Used by the in-app usage tile and `DailyLimitCallout`.

Auth required (resolves the caller's tier).

**Response (200):**

```json
{
  "tier": "free",
  "today": {
    "date": "2026-05-14",
    "rawUsd": 0.42,
    "creditAdjustmentUsd": 0,
    "effectiveUsd": 0.42,
    "videoCount": 3
  },
  "limitUsd": 2,
  "remainingUsd": 1.58,
  "resetAt": "2026-05-15T00:00:00.000Z"
}
```

- `effectiveUsd = rawUsd + creditAdjustmentUsd`. Admin credits are stored as
  negative `creditAdjustmentUsd`, so a grant lowers `effectiveUsd`.
- `limitUsd: -1` (or `0`) means the tier is unlimited; `remainingUsd` will be
  `null` in that case so the wire format stays JSON-honest (no `Infinity`).
- `resetAt` is always the next UTC midnight ISO timestamp.

---

## SSR (Server-Side Rendered)

### GET /s/:slug

Server-side rendered share page with OG meta tags for social previews. No `/api` prefix.

**Rate Limit:** 60 per minute

**Response:** `text/html` with OG tags, Twitter Cards, JSON-LD, and redirect to frontend app.

---

### GET /s/:slug/og-image.png

Dynamic OG image for social preview cards.

**Rate Limit:** 30 per minute

**Response:** `image/png` with 24h cache header.

---

## Error Responses

All errors follow this format:

```json
{
  "error": "NOT_FOUND",
  "message": "Video not found",
  "statusCode": 404
}
```

Common error codes:

- `400` - Bad Request (validation), `INVALID_CATEGORY`, `INVALID_WEBHOOK`
- `401` - Unauthorized
- `403` - Forbidden: `TIER_LIMIT_EXCEEDED`, `SHARE_NOT_ALLOWED`
- `404` - Not Found: `SHARE_NOT_FOUND`
- `409` - Conflict: `ALREADY_SHARED`
- `429` - Rate Limited: `RATE_LIMITED`, `DAILY_LIMIT_REACHED` (per-user daily cost cap; also returns `resetAt` + `limitUsd`)
- `500` - Internal Server Error, `PAYMENT_ERROR`
- `503` - Service Unavailable: `COST_LIMIT_EXCEEDED` (global aggregate cap)

---

# WebSocket API

Real-time updates for async operations.

---

## Connection

**Endpoint:** `ws://localhost:3000/ws`

**Authentication:** Token as query parameter

```javascript
const token = localStorage.getItem("token");
const ws = new WebSocket(`ws://localhost:3000/ws?token=${token}`);
```

---

## Message Format

All messages are JSON:

```json
{
  "type": "event.name",
  "payload": { ... }
}
```

---

## Events

### video.status

Video processing status update.

**Payload:**

```json
{
  "type": "video.status",
  "payload": {
    "videoSummaryId": "507f1f77bcf86cd799439020",
    "userVideoId": "507f1f77bcf86cd799439014",
    "youtubeId": "dQw4w9WgXcQ",
    "status": "processing" | "completed" | "failed",
    "progress": 45,
    "message": "Extracting sections...",
    "error": null
  }
}
```

**Status flow:**

1. `pending` - Job created, HTTP POST sent to summarizer
2. `processing` - Summarizer started processing
3. `completed` - Summary ready
4. `failed` - Error occurred

**Progress values:**

- 10: Fetching transcript
- 30: Cleaning text
- 50: Detecting sections
- 70: Summarizing sections
- 90: Extracting concepts
- 100: Complete

---

### expansion.status

Expansion generation status (for explain_auto on cache miss).

**Payload:**

```json
{
  "type": "expansion.status",
  "payload": {
    "videoSummaryId": "507f1f77bcf86cd799439020",
    "targetType": "section",
    "targetId": "550e8400-e29b-41d4-a716-446655440001",
    "status": "processing" | "completed" | "failed",
    "error": null
  }
}
```

---

### chat.message

New chat message (for streaming support).

**Payload (complete):**

```json
{
  "type": "chat.message",
  "payload": {
    "chatId": "507f1f77bcf86cd799439040",
    "role": "assistant",
    "content": "Here's an example...",
    "done": true
  }
}
```

**Payload (streaming):**

```json
{
  "type": "chat.message",
  "payload": {
    "chatId": "507f1f77bcf86cd799439040",
    "role": "assistant",
    "content": "Here's",
    "done": false
  }
}
```

---

## Client Implementation

```typescript
// src/hooks/useWebSocket.ts

export function useWebSocket() {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) return;

    const ws = new WebSocket(`${import.meta.env.VITE_WS_URL}?token=${token}`);

    ws.onopen = () => {
      console.log("WebSocket connected");
    };

    ws.onmessage = (event) => {
      const { type, payload } = JSON.parse(event.data);

      switch (type) {
        case "video.status":
          // Update video in cache
          queryClient.setQueryData(
            ["video", payload.userVideoId],
            (old: any) => ({ ...old, status: payload.status })
          );

          // Invalidate list if completed
          if (payload.status === "completed") {
            queryClient.invalidateQueries(["videos"]);
          }
          break;

        case "expansion.status":
          if (payload.status === "completed") {
            queryClient.invalidateQueries([
              "expansion",
              payload.videoSummaryId,
              payload.targetType,
              payload.targetId,
            ]);
          }
          break;

        case "chat.message":
          // Handle streaming or completed message
          break;
      }
    };

    ws.onclose = () => {
      console.log("WebSocket disconnected");
      // Implement reconnection logic
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };

    wsRef.current = ws;

    return () => {
      ws.close();
    };
  }, [queryClient]);

  return wsRef;
}
```

---

## Server Implementation (vie-api)

```typescript
// src/plugins/websocket.ts

import fastifyWebsocket from "@fastify/websocket";

export async function websocketPlugin(fastify: FastifyInstance) {
  await fastify.register(fastifyWebsocket);

  // Store connections by userId
  const connections = new Map<string, WebSocket>();

  fastify.get("/ws", { websocket: true }, (socket, req) => {
    const token = req.query.token as string;

    try {
      const { userId } = fastify.jwt.verify(token);
      connections.set(userId, socket);

      socket.on("close", () => {
        connections.delete(userId);
      });
    } catch {
      socket.close(4001, "Unauthorized");
    }
  });

  // Expose broadcast function
  fastify.decorate("broadcast", (userId: string, event: any) => {
    const socket = connections.get(userId);
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(event));
    }
  });
}
```

---

## Admin (x-admin-key)

Static-key endpoints for operators; the admin panel proxies to them. `x-admin-id` is optional operator metadata written to the audit row (not auth).

### DELETE /api/admin/videos/:youtubeId

Removes a video from every store for every user, in this order: summarizer purge (Qdrant `transcript_chunks` by `video_id`, S3 `videos/<id>/` prefix, Redis response + pipeline keys) → Mongo `userVideos`, `shareLikes`, `shareViews`, `agentNotes`, `idempotencyKeys`, then `videoSummaryCache` (all versions) → dispatch-guard keys → `videoDeletions` audit row. A failed purge aborts before any Mongo row is touched (idempotent; retry).

Headers: `x-admin-key` (required), `x-admin-id` (optional, `[A-Za-z0-9._-]{1,64}`). Body (optional): `{ "reason": "..." }`.

Response `200`:

```json
{
  "scope": "global",
  "youtubeId": "dQw4w9WgXcQ",
  "summaryIds": ["6a7b2f6abacdb32871507996"],
  "counts": { "videoSummaryCache": 1, "userVideos": 3, "shareLikes": 0, "shareViews": 0, "agentNotes": 2, "idempotencyKeys": 1, "dispatchGuards": 1, "qdrantPoints": 58, "s3Objects": 41, "redisKeys": 2 },
  "warnings": []
}
```

Errors: `401 UNAUTHORIZED`, `400 VALIDATION_ERROR` (bad id or `x-admin-id`), `429 RATE_LIMITED` (300/hour with a valid key, 30/hour otherwise, plus the global per-IP limit), `502 SUMMARIZER_PURGE_FAILED`. A `200` with `partial: true` means a Mongo step failed after the purge; `warnings` names it.

User-scope deletion (`DELETE /api/videos/:id`, JWT) is different: it removes only the caller's library row, their idempotency key and their own assistant notes for the video; the shared summary stays. User-account deletion is documented in [GDPR.md](./GDPR.md).

## HTTP Callback Integration

Status updates flow from summarizer via HTTP callback:

```
vie-summarizer
      │
      │ HTTP POST to /internal/status
      ▼
vie-api
      │
      │ Broadcast via WebSocket
      ▼
vie-web (user's browser)
```

---

# Assistant Chat API

AI-powered video chat endpoints served by `vie-assistant` on port 8001, proxied through `vie-api`.

**Communication:** HTTP + SSE between vie-api and vie-assistant.

---

## POST /api/videos/:videoSummaryId/chat

Stream a chat response about a specific video. Uses SSE for progressive token delivery.

**Auth:** Bearer token required.

**Request:**

```json
{
  "message": "What are the main ingredients?",
  "chatHistory": [
    { "role": "user", "content": "Hello" },
    { "role": "assistant", "content": "Hi! How can I help?" }
  ]
}
```

**Response:** SSE stream (`text/event-stream`)

```
data: {"type":"token","content":"The"}
data: {"type":"token","content":" main"}
data: {"type":"token","content":" ingredients"}
data: {"type":"done"}
```

**Errors:**

- 404: Video not found
- 422: Invalid request body
- 500: LLM or internal error

---

## POST /api/videos/:videoSummaryId/action

Dispatch a structured action to the assistant service. Proxies to `POST /action` on vie-assistant after verifying ownership.

**Auth:** Bearer token required. Ownership is enforced against the video summary.

**Request:**

```json
{
  "action": "save_note",
  "params": { "text": "Remember this", "timestamp": "1:23" }
}
```

**Supported actions:**

| Action | Tool | Required params | Optional params |
|--------|------|-----------------|-----------------|
| `save_note` | `note_taker` | `text` | `timestamp` |
| `quiz_me` | `quiz_generator` | — | `topic`, `num_questions` |
| `find_moment` | `navigator` | `query` | — |
| `explain` | `concept_explain` | `concept` | — |

Library-scoped actions (`video_id` omitted) are also dispatched through the same
channel and call back into vie-api via `/internal/assistant/*`: `generate_video`,
`organize_library`, `create_folder`, `rename_folder`, `move_folder`,
`delete_folder`, `move_video`. See [SERVICE-ASSISTANT.md](./SERVICE-ASSISTANT.md#post-action).

**Response (200):**

```json
{
  "success": true,
  "action": "save_note",
  "data": { "saved": true, "note_id": "uuid-here" },
  "error": null,
  "trace_id": "abcdef012345"
}
```

The same envelope is returned for action-level errors (400/404) with `success: false` and `error` populated. `trace_id` is a 12-char hex ID — surface it to the user when reporting bugs.

**Errors:**

- `400`: Invalid action body or missing required param (e.g., `save_note` without `text`)
- `401`: Missing or invalid auth
- `404`: User has no access to the video
- `502`: Assistant service unavailable

---

## POST /api/assistant/library/chat

Library-wide RAG chat (SSE) — answers across many of the user's videos when no
single video is open. The gateway resolves the user's owned YouTube IDs via
`getUserVideos`, then forwards to vie-assistant `/library/chat` with
`X-Internal-Secret` + `X-User-Id`. Same SSE event shapes as `/chat`
(`source` / `text` / `error` / `done`); each `source.video_id` is a YouTube ID
for attribution/deep-link.

**Auth:** Bearer token required.

**Request:** `{ "message": "which of my videos covered hooks?", "conversation_history": [] }`
(the gateway injects `video_ids` from the user's library; a client-supplied list is ignored).

## POST /api/assistant/library/search

Retrieval-only sibling of `/library/chat` — returns ranked chunks across the
user's library without an LLM answer. Same auth and gateway forwarding.

## /internal/assistant/* (service-to-service)

Callback routes the vie-assistant uses to mutate the caller's library. **Not
user-JWT** — authenticated by the `authenticateInternal` preHandler
(`X-Internal-Secret` + `X-User-Id`, constant-time secret comparison). All work is
scoped to `X-User-Id`; a body-supplied userId is never trusted.

| Method & path | Purpose |
|---|---|
| `GET /internal/assistant/folders` | List the user's folders |
| `POST /internal/assistant/folders` | Create a folder |
| `PATCH /internal/assistant/folders/:id` | Rename / move a folder |
| `DELETE /internal/assistant/folders/:id` | Delete a folder |
| `GET /internal/assistant/videos` | List the user's videos |
| `PATCH /internal/assistant/videos/:id/move` | Move a video to a folder |
| `POST /internal/assistant/generate` | Generate a video (via `videoService.createVideo` — keeps cost reservation + dispatch guard) |

See [SERVICE-API.md](./SERVICE-API.md#internal-authentication-authenticateinternal) and [SECURITY.md](./SECURITY.md#service-to-service-auth).

---

# SSE Streaming API

Progressive summarization via Server-Sent Events.

---

## Overview

The summarization pipeline uses SSE to stream results progressively, allowing the frontend to display content as it becomes available.

**Endpoint:** `GET /api/videos/:videoSummaryId/stream`

---

## Event Types

### v2 Events (Current Pipeline)

| Event | Phase | Description |
|-------|-------|-------------|
| `phase` | All | Indicates which processing phase started |
| `cached` | 0 | Cache hit — a fast replay follows (`metadata`, `triage_complete`, `meta`, `tab_ready[]`, `synthesis_complete`, `done`) |
| `metadata` | 1 | Video metadata (title, channel, duration, context) |
| `chapters` | 1 | Creator chapters if available |
| `sponsor_segments` | 1 | SponsorBlock segments |
| `transcript_ready` | 1 | Transcript extraction complete (authoritative `duration`) |
| `description_analysis` | 2 | Links, resources extracted from description |
| `triage_complete` | 3 | Content tags, tab layout, confidence from plan stage |
| `extraction_progress` | 4 | Chunked-extraction progress for long videos |
| `extraction_complete` | 4 | Domain extraction finished |
| `frames` | 5 | Extracted frame set (presigned URLs) |
| `meta` | 5 | VIEResponseMeta with videoId, contentTags, tldr, etc. |
| `tab_ready` | 5 | Individual assembled tab with component, props, and `position` (may arrive out of natural order — slot by `position`, see below) |
| `enrichment_complete` | 5 | Enrichment (quiz/flashcards) finished |
| `synthesis_complete` | 5 | TLDR and key takeaways |
| `heartbeat` | Any | Keep-alive during long-running steps (e.g. moment frame fill) — carries `ts`, safe to ignore |
| `complete` | 6 | Processing complete with tab count and timing |
| `done` | 6 | Final event, closes stream |

### Legacy Events (v1 -- still emitted for backward compat)

| Event | Phase | Description |
|-------|-------|-------------|
| `detection_result` | 1 | Legacy: Category detection result with outputType |
| `chapter_ready` | 3 | Legacy: Individual chapter summary with content blocks |
| `concepts_complete` | 4 | Legacy: Key concepts extracted |

---

## Streaming Phases

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STREAMING PHASES (SSE Events)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PHASE 1: METADATA + TRANSCRIPT + FRAMES (parallel)                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  yt-dlp Metadata → then Transcript + Frames in parallel             │   │
│  │                                                                      │   │
│  │  Output events:                                                      │   │
│  │    - metadata (title, channel, thumbnail, duration)                 │   │
│  │    - chapters (if creator chapters exist)                            │   │
│  │    - sponsor_segments (SponsorBlock API)                             │   │
│  │    - transcript_ready                                                │   │
│  │                                                                      │   │
│  │  Transcript source chain (first success wins):                      │   │
│  │    1. S3 cache          → phase: transcript_cached (~instant)       │   │
│  │    2. yt-dlp captions   → phase: transcript (~1-3s)                 │   │
│  │    3. Gemini audio      → phase: audio_transcription (~5-15s)      │   │
│  │    4. Whisper audio     → phase: whisper_transcription (~10-30s)    │   │
│  │    5. Metadata fallback → phase: metadata_fallback (music only)    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 2: VISUAL CONTEXT INJECTION                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Inject [VISUAL at M:SS] annotations from frame analysis            │   │
│  │  into transcript (if frame intelligence enabled)                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 3: CLASSIFIER + PLAN (~2-5 seconds)                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Classifier (fast model) + Plan (Sonnet) determine domain,          │   │
│  │  content tags, and tab layout                                       │   │
│  │                                                                      │   │
│  │  Output event: triage_complete                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 4: EXTRACTION (~5-30 seconds, chunked for long videos)              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Domain-specific extraction using schemas                           │   │
│  │  Chunked extraction for >30min videos (chapter-aware batching)     │   │
│  │                                                                      │   │
│  │  Output event: extraction_complete                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 5: ENRICHMENT + SYNTHESIS + ASSEMBLY (parallel where possible)      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Enrichment (quiz/flashcards, fast model) runs if domain supports   │   │
│  │  Synthesis (fast model) + Assembly (code, no LLM calls) in parallel │   │
│  │                                                                      │   │
│  │  Output events: frames, meta, tab_ready[] (one per tab, carries     │   │
│  │                 `position`; moment_track tabs held back for frame   │   │
│  │                 fill + streamed last, with heartbeats),             │   │
│  │                 synthesis_complete                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 6: SAVE & DONE                                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  - Save complete result to MongoDB                                  │   │
│  │  - Emit "complete" event with tabCount + processingTimeMs           │   │
│  │  - Emit "done" event (triggers confetti on frontend)                │   │
│  │  - Emit "[DONE]" to close SSE stream                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Event Payloads

### phase

```json
{ "event": "phase", "phase": "transcript" | "transcript_cached" | "audio_transcription" | "whisper_transcription" | "metadata_fallback" | "translation" }
```

Only the transcript-source phases and `translation` are emitted as explicit
`phase` events. All other UI phases (metadata, extraction, building, …) are
**derived by the client** from milestone events (`metadata`, `triage_complete`,
`extraction_complete`, `meta`, …) — see `SSE_PHASE_MAP` in
`apps/web/src/features/video-output/lib/streaming/stream-event-processor.ts`
and `VALID_SSE_PHASES` in `sse-validators.ts`.

**Transcript sub-phases** (emitted within Phase 1 based on transcript source):

| Phase | When |
|-------|------|
| `transcript` | Starting transcript extraction (always emitted first) |
| `transcript_cached` | Transcript found in S3 cache |
| `audio_transcription` | No captions available; starting audio transcription (Gemini) |
| `whisper_transcription` | Gemini failed or unavailable; falling back to Whisper |
| `metadata_fallback` | All transcript sources failed for music video; using title/description as transcript |

### metadata

```json
{
  "event": "metadata",
  "title": "React Hooks Tutorial",
  "channel": "Fireship",
  "duration": 627,
  "thumbnailUrl": "https://i.ytimg.com/vi/xxx/maxresdefault.jpg",
  "context": {
    "category": "coding",
    "youtubeCategory": "Science & Technology",
    "tags": ["react", "javascript", "hooks", "programming"],
    "displayTags": ["#React", "#JavaScript", "#Hooks"]
  }
}
```

### triage_complete

Emitted after the plan stage determines content tags and tab layout.

```json
{
  "event": "triage_complete",
  "contentTags": ["tech", "learning"],
  "modifiers": [],
  "primaryTag": "tech",
  "tabs": [
    { "id": "overview", "label": "Overview", "emoji": "📋", "dataSource": "tech.overview" },
    { "id": "code", "label": "Code", "emoji": "💻", "dataSource": "tech.codeSnippets" }
  ],
  "confidence": 0.88
}
```

### meta

VIEResponseMeta with video identity and synthesis results.

```json
{
  "event": "meta",
  "videoId": "dQw4w9WgXcQ",
  "videoTitle": "React Hooks Tutorial",
  "creator": "Fireship",
  "contentTags": ["tech", "learning"],
  "modifiers": [],
  "primaryTag": "tech",
  "userGoal": "Learn React Hooks patterns and best practices",
  "tldr": "Comprehensive guide to React Hooks covering useState, useEffect, and custom hooks...",
  "keyTakeaways": ["useState manages component state", "useEffect handles side effects"],
  "masterSummary": "This tutorial walks through...",
  "seoDescription": "Learn React Hooks..."
}
```

### tab_ready

Individual assembled tab, emitted once per tab. Each tab is self-contained with component name and pre-resolved props.

```json
{
  "event": "tab_ready",
  "id": "overview",
  "label": "Overview",
  "emoji": "📋",
  "component": "overview",
  "position": 0,
  "props": {
    "sections": [...],
    "stats": [...]
  },
  "crossTabLinks": [
    { "targetTab": "code", "label": "See code examples" }
  ],
  "attachments": [
    { "component": "tip_callout", "props": { "text": "..." } }
  ],
  "degradedFrom": "step_player"
}
```

- **`position`** (optional, ≥0): index in the persisted tab order. Tabs may
  arrive **out of natural order** — `moment_track` tabs are held back while
  exact-timestamp frames are filled and stream **last**. Clients must slot
  each tab at `position`, never append.
- **`attachments`** (optional): secondary components rendered around the
  primary (`stat_banner`, `tip_callout`, `summary_header`, `diagram_card`,
  `frame_strip`, `quick_quiz`).
- **`degradedFrom`** (optional): original component name when the assembly
  demote ladder downgraded the tab (e.g. `step_player` → `checklist`).

### complete

```json
{
  "event": "complete",
  "tabCount": 5,
  "processingTimeMs": 25432
}
```

### Frame URLs (presigned, re-signed on serve)

Frame images in `frames` events and tab props are **presigned S3 URLs** with a
limited TTL. Persisted tabs also carry the stable `s3Key` per frame
(`FrameEvidence.s3Key`), and the api gateway re-signs expired URLs on every
serve via `api/src/utils/refresh-frame-urls.ts` (`FRAME_URL_TTL_SECONDS`,
default 21600 = 6h; the summarizer's `S3_PRESIGNED_URL_EXPIRY` must be ≥ this).
Clients should not cache frame URLs beyond a session.

### chapters

```json
{
  "event": "chapters",
  "chapters": [
    {
      "startSeconds": 0,
      "endSeconds": 120,
      "title": "Introduction"
    }
  ],
  "isCreatorChapters": true
}
```

### transcript_ready

```json
{
  "event": "transcript_ready",
  "duration": 627
}
```

### description_analysis

```json
{
  "event": "description_analysis",
  "links": [
    { "url": "https://github.com/...", "type": "github", "label": "Source code" }
  ],
  "resources": [],
  "socialLinks": [
    { "platform": "twitter", "url": "https://twitter.com/..." }
  ]
}
```

### synthesis_complete

```json
{
  "event": "synthesis_complete",
  "tldr": "This video explains how to use React Hooks...",
  "keyTakeaways": [
    "useState is the most fundamental hook",
    "useEffect handles side effects",
    "Custom hooks enable code reuse"
  ]
}
```

### done

```json
{
  "event": "done",
  "videoSummaryId": "507f1f77bcf86cd799439020",
  "processingTimeMs": 25432
}
```

---

## Client Implementation

```typescript
// apps/web/src/hooks/use-summary-stream.ts (simplified)

export function useSummaryStream(videoSummaryId: string | null) {
  const [state, setState] = useState<StreamState>({ phase: 'idle' });

  useEffect(() => {
    if (!videoSummaryId) return;

    const eventSource = new EventSource(
      `/api/videos/${videoSummaryId}/stream`
    );

    eventSource.onmessage = (event) => {
      if (event.data === '[DONE]') {
        eventSource.close();
        return;
      }

      const data = JSON.parse(event.data);

      switch (data.event) {
        case 'metadata':
          setState(s => ({ ...s, metadata: data }));
          break;

        case 'triage_complete':
          setState(s => ({ ...s, triage: data }));
          break;

        case 'meta':
          setState(s => ({ ...s, meta: data }));
          break;

        case 'tab_ready': {
          // Slot by `position`, never append — moment_track tabs are held
          // back server-side and stream last, out of natural order.
          setState(s => {
            const tabs = [...(s.tabs || [])];
            const idx = typeof data.position === 'number' ? data.position : tabs.length;
            tabs[idx] = data;
            return { ...s, tabs };
          });
          break;
        }

        case 'complete':
          setState(s => ({ ...s, phase: 'complete', tabCount: data.tabCount }));
          break;

        case 'done':
          setState(s => ({ ...s, phase: 'done', processingTimeMs: data.processingTimeMs }));
          break;
      }
    };

    eventSource.onerror = () => {
      setState(s => ({ ...s, phase: 'error' }));
      eventSource.close();
    };

    return () => eventSource.close();
  }, [videoSummaryId]);

  return state;
}
```

---

## LLM Calls Summary

For a typical video (v2 pipeline):

| Stage | Model | Calls | Parallel? |
|-------|-------|-------|-----------|
| Classifier | Fast (Haiku) | 1 | Yes (with Plan) |
| Plan | Sonnet | 1 | Yes (with Classifier) |
| Extraction | Sonnet | 1-N (chunked for long videos) | Batched |
| Enrichment | Fast (Haiku) | 1 (if domain supports it) | No |
| Synthesis | Fast (Haiku) | 1 | Yes (with Assembly) |
| Assembly | None (0 LLM calls; no longer instant — may run exact-timestamp moment frame extraction with SSE heartbeats) | 0 | Yes (with Synthesis) |

**Total: ~4-6 LLM calls, ~15-40 seconds** (varies with video length and chunking)
