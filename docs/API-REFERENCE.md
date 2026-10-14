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

**Demo login:** when the deployment sets `DEMO_USER_EMAIL` and `DEMO_USER_PASSWORD`, the same route accepts this body instead of credentials and signs in the demo account server-side. The response, cookie and rate limit are identical to a normal login.

```json
{
  "demo": true
}
```

Returns `403 DEMO_DISABLED` when demo mode is not configured.

---

### GET /auth/registration

Public. Tells the web app which entry points to show.

**Response (200):**

```json
{
  "open": false,
  "demoEnabled": true
}
```

| Field         | Meaning                                                                     |
| ------------- | --------------------------------------------------------------------------- |
| `open`        | Self-service signup is allowed (`ALLOW_REGISTRATION`)                       |
| `demoEnabled` | `POST /auth/login` accepts `{ "demo": true }` (both `DEMO_USER_*` vars set) |

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
  "folderId": "507f1f77bcf86cd799439013",
  "bypassCache": false,
  "cold": false
}
```

`bypassCache` (optional) starts a fresh run as a new version row (see below).
`cold` (optional, benchmark-only) also marks the row `coldMedia: true` so the
summarizer skips its S3 transcript + scene-frame caches; it implies
`bypassCache` and is allowed only for admin and eval accounts (others get
`403 COLD_RUN_FORBIDDEN`).

**Logic:**

1. Extract `youtubeId` from URL
2. `bypassCache` on a video that already has a user version, or any submission by the
   eval user (`users.isEvalUser`, D25): insert a new version row (`isLatest: false`; numbering and pools in
   [IDEMPOTENCY.md](./IDEMPOTENCY.md#version-rows-bypasscache-and-eval-runs)), re-point
   the user's library entry at it, publish the job, return `status: pending` with
   `newVersion: true`. A user row is promoted to `isLatest` only when it completes; an
   eval row never is.
3. Otherwise check `videoSummaryCache` for the served (`isLatest`) summary
4. If **HIT**: create `userVideo` reference, return immediately
5. If **MISS**: create cache entry, publish job, return with `status: pending`

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

### GET /videos/versions/:youtubeId

Version rows of one video, newest first (`?limit=` 1–50, default 10) — for A/B
comparison of `bypassCache` runs. `404 VIDEO_NOT_FOUND` unless the user has the
video in their library. Eval-run rows (`evalRun: true`, D25) are listed only to the
eval user; everyone else sees the user-version pool only.

```json
{
  "versions": [
    { "_id": "…", "youtubeId": "dQw4w9WgXcQ", "version": 3, "isLatest": true, "status": "completed",
      "title": "…", "createdAt": "…", "processedAt": "…", "processingTimeMs": 61234 }
  ]
}
```

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

## POST /api/assistant/action

Same action envelope as above, with `video_id` in the body (optional; omitted for
library-scoped actions). When `video_id` is given (a `videoSummaryId`, or a
`youtubeId`), the user must hold that video in their library — otherwise
`404 VIDEO_NOT_FOUND` before the assistant is called, so no other user's version
(eval runs included) can be loaded as action context. A `youtubeId` resolves to the
served version, never an eval row.

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
| `phase` | All | Transcript-source hops and `translation` (see [phase](#phase)) |
| `cached` | 0 | Cache hit — a fast replay follows (`metadata`, `triage_complete`, `meta`, `tab_ready[]` — every tab once, `final: true`, `position` = its index — `synthesis_complete`, `done`) |
| `metadata` | 1 | Video metadata (title, channel, thumbnailUrl, duration) |
| `transcript_ready` | 2 | Transcript extraction complete (authoritative `duration`) |
| `synthesis_complete` | 2 + 3 | Up to twice: `{tldr, keyTakeaways}` at memory-done (the hero), then the four-field superset in assembly's late group — see [synthesis_complete](#synthesis_complete) |
| `triage_complete` | 2 | Content tags, tab layout, confidence, `contentFormat`, `evidence` (the reconciled view), `planEvidence` / `memoryEvidence`, `terms` — at plan-done (plan ∥ memory both in, after reconcile) |
| `meta` | 2 | VIEResponseMeta with videoId, contentTags, tab labels — emitted with `triage_complete`; again at frames-done when the screen-code check changed the tab list |
| `frames` | 2 | Extracted frame set (presigned URLs), from the frames branch of phase 2 |
| `extraction_progress` | 2 | Per landed extraction group call (`section: "groups"`, `batch` / `of`); with `EXTRACTION_PARALLEL=false` after phase 2: per chunked batch (`"chunked"` / `"chunked-sequential"`) or `"all"` for one call |
| `extraction_complete` | 2 | The merged extraction, once every group landed (with `EXTRACTION_PARALLEL=false`: after phase 2) |
| `tab_ready` | 2 + 3 | One tab, with `position` (sort key) and `final`. The provisional Moments tab (`provisional: true`) streams at plan-done; with `EXTRACTION_PARALLEL` (default) non-final previews stream as each extraction group lands, the rest from assembly; a tab id can arrive more than once and the later emission replaces the earlier by id (a tab taking the provisional's place under another id names it in `replaces`). Each tab of the final response gets a `final: true` emission before `complete` — the last final wins — see [tab_ready](#tab_ready) |
| `description_analysis` | 3 | Links, resources extracted from description — emitted at assembly (assembly waits ≤ 5 s for the analysis started at metadata-end; past that, nothing) |
| `enrichment_complete` | 3 | The quiz landed (only for a plan with a quiz host; in assembly's late group) |
| `heartbeat` | Any | Keep-alive during long-running steps (e.g. moment frame fill) — carries `ts`, safe to ignore |
| `complete` | 4 | Processing complete: tab count, `tabIds` (the final tab order — a streamed tab missing here was withdrawn), timing |
| `done` | 4 | Final event, closes stream (non-English videos: after the translation phase) |
| `error` | Any | `{message, code?}` — replaces the tail on a failed run |

The registry of emitted events is `SSE_EVENT_MODELS` in
`services/summarizer/src/models/sse_events.py`. `chapters`, `sponsor_segments` and the v1
events `detection_result`, `chapter_ready`, `concepts_complete` are no longer emitted.

---

## Streaming Phases

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STREAMING PHASES (SSE Events)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PHASE 1: METADATA (one yt-dlp extract_info + validate_duration)            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Output event: metadata (title, channel, thumbnail, duration)        │   │
│  │  Then, in the background: caption fetch, description analysis,       │   │
│  │  low-res + one ≤720p download (skipped on a frame-manifest hit)      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 2: TEXT BRANCH ∥ FRAMES ∥ FIELD GROUPS (one parallel group)          │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Text: transcript → tier probe → plan ∥ memory → reconcile           │   │
│  │  Frames: scenes → tier (probe, ≤3 s wait) → hi-res → OCR ∥ vision    │   │
│  │    → frames-done: <visual_annotations> + deferred code tabs          │   │
│  │  Field groups (EXTRACTION_PARALLEL=true): text groups fire at        │   │
│  │    plan-done, the visual group at frames-done                        │   │
│  │                                                                      │   │
│  │  Output events: phase (transcript source), transcript_ready,         │   │
│  │    synthesis_complete (memory-done: tldr + keyTakeaways),            │   │
│  │    triage_complete + meta (plan-done), tab_ready (provisional        │   │
│  │    Moments tab), frames, meta again when frames-done changed the     │   │
│  │    tabs; per landed group: extraction_progress {section:             │   │
│  │    "groups"} + tab_ready previews (final: false);                    │   │
│  │    extraction_complete (the merged groups)                           │   │
│  │                                                                      │   │
│  │  Transcript source chain (first success wins):                       │   │
│  │    1. S3 cache          → phase: transcript_cached                   │   │
│  │    2. yt-dlp captions   → (no phase event)                           │   │
│  │    3. transcript-api    → phase: transcript                          │   │
│  │    4. Whisper audio     → phase: audio_transcription, then           │   │
│  │                           whisper_transcription                      │   │
│  │    5. Gemini audio      → (after Whisper failed or is gated off)     │   │
│  │    6. Metadata fallback → phase: metadata_fallback (music only)      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 3: ASSEMBLY → LATE GROUP                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Output events: description_analysis, tab_ready previews of the      │   │
│  │    tabs the client lacks (the overview, tabs assembly added or       │   │
│  │    the last groups changed), then moment fill ∥ synthesis ∥          │   │
│  │    quiz: moment tabs (framed, heartbeats meanwhile),                 │   │
│  │    synthesis_complete (superset), enrichment_complete; then the      │   │
│  │    final re-emit: every tab not yet final, `final: true`, at its     │   │
│  │    final position (the overview last)                                │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  PHASE 4: SAVE & DONE                                                       │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  - Emit "complete" (tabCount, tabIds, processingTimeMs, degraded)    │   │
│  │  - Save the result to MongoDB; Redis response cache (English,        │   │
│  │    awaited, best-effort); Qdrant + S3 raw transcript in the          │   │
│  │    background                                                        │   │
│  │  - Emit "done" (triggers confetti on frontend) — non-English         │   │
│  │    videos: after the translation phase (phase: translation)          │   │
│  │  - Emit "[DONE]" to close SSE stream                                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

With `EXTRACTION_PARALLEL=false` there are no field groups: after the whole
phase-2 group, frames-done runs and then ONE extraction call (or a long
video's chapter batches, one at a time) — `extraction_progress` carries
`section: "all"` (one call) or `"chunked"` / `"chunked-sequential"` (batches) —
and assembly streams every tab but the moment tabs at once. Event order per
path: the emission sequence under [tab_ready](#tab_ready).

---

## Event Payloads

### phase

```json
{ "event": "phase", "phase": "metadata_retry" | "transcript_retry" | "transcript" | "transcript_cached" | "audio_transcription" | "whisper_transcription" | "metadata_fallback" | "translation" }
```

Only the transcript-source phases and `translation` are emitted as explicit
`phase` events. All other UI phases (metadata, extraction, building, …) are
**derived by the client** from milestone events (`metadata`, `triage_complete`,
`extraction_complete`, `meta`, …) — see `SSE_PHASE_MAP` in
`apps/web/src/features/video-output/lib/streaming/stream-event-processor.ts`
and `VALID_SSE_PHASES` in `sse-validators.ts`.

**Transcript sub-phases** (emitted by the transcript fetch chain,
`transcription/transcript_fetcher.py`, inside Phase 2's text branch):

| Phase | When |
|-------|------|
| `metadata_retry` | A proxy exit got YouTube's bot check / a 429 during the metadata extract; the summarizer rotates exits (no new one after 15 s, at least 3) (web: "YouTube is rate-limiting, retrying" until the `metadata` event) |
| `transcript_retry` | The same during the caption fetch — the timedtext fetch or the caption API (web: the same line until `transcript_ready` or the next `phase` event) |
| `transcript_cached` | Transcript found in the S3 cache (`fetch_transcript`, step 0) |
| — | yt-dlp captions (the track picked at metadata) succeed: no `phase` event |
| `transcript` | No yt-dlp captions: starting youtube-transcript-api (skipped while the caption-429 marker is set) |
| `audio_transcription` | Starting the audio layers (`fetch_audio_transcript`: Whisper, then Gemini) — also for the audio pass after the tier probe judged the captions unusable |
| `whisper_transcription` | Right after `audio_transcription`, when Whisper may run (`WHISPER_ENABLED`, under the duration cap); Gemini, tried only after Whisper failed or was gated off, emits no event of its own |
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

Emitted at plan-done — plan and memory both in, the tabs reconciled (re-pointed, demoted,
dropped, backfilled, quiz last, 3–6 tabs; `pipeline.reconcile` in
[DATA-MODELS](./DATA-MODELS.md) lists every decision).

```json
{
  "event": "triage_complete",
  "contentTags": ["tech", "learning"],
  "modifiers": [],
  "primaryTag": "tech",
  "userGoal": "Learn React Hooks patterns",
  "tabs": [
    { "id": "code", "label": "Code", "emoji": "💻", "component": "code_playground",
      "dataSource": "tech.snippets", "brief": { "what": "", "where": [], "expect": 0 } }
  ],
  "confidence": 0.88,
  "contentFormat": "tutorial",
  "evidence": { "has_code": true, "is_learnable": true },
  "planEvidence": { "has_code": false, "is_learnable": true },
  "memoryEvidence": { "has_code": true, "is_learnable": true },
  "terms": ["useState", "useEffect"]
}
```

- **`evidence`** — the view every reader shares: a key is `true` when the plan OR the video
  memory said true, `false` only when BOTH answered false, absent otherwise (neither said true
  and one was silent, or memory failed). It gates the domain requirements, the quiz and
  `<video_memory>`.
- **`planEvidence` / `memoryEvidence`** — each reader's own answers (`memoryEvidence: null` =
  memory failed).

A `has_code` key that is false in both readers defers the code tabs (and tech's required
`code_playground` when no tab was planned for it) to frames-done, where the screen decides; the
tab list can then change, announced by a fresh `meta` (see the emission sequence).

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

One tab: a `TabEntry` (`id, label, emoji, component, props, goal?, crossTabLinks?,
attachments?, degradedFrom?`) plus four stream fields. Typed as `SSETabReadyEvent`
(`packages/types/src/api.ts`); emitted through `TabLedger`
(`services/summarizer/src/services/pipeline/tab_ledger.py`, pipeline-1min 2.1).

```json
{
  "event": "tab_ready",
  "id": "key_moments",
  "label": "Key Moments",
  "emoji": "🎬",
  "component": "moment_track",
  "position": 3,
  "final": true,
  "props": { "items": [...] },
  "goal": "Jump to any part of the video.",
  "crossTabLinks": [{ "targetTab": "code", "label": "See code examples" }],
  "attachments": [{ "component": "tip_callout", "props": { "text": "..." } }],
  "degradedFrom": "step_player"
}
```

- **`position`** (≥0): the tab's index in the response's tab order — a **sort
  key**, never a splice index. Exact on a `final` emission; an **estimate**
  before it (a provisional or held-back tab), so two tabs may briefly share a
  value — a final emission wins over a non-final one at the same value.
- **`final`**: `true` = this is the tab's **last** emission of the run: its
  content and position will not change. Every tab of the final response gets a
  final emission before `complete`, normally exactly one. Should a tab still
  change after it (a contract slip the summarizer logs as an error), a
  **corrected final follows for the same id: the last final wins** — never
  freeze a tab on its first final. `false` = a later `tab_ready` with the same
  id may replace it. Absent only on pre-2.1 emitters (treat as `false`).
- **`provisional`** (optional, only ever `true`): a placeholder built before
  extraction — the **Moments tab at plan-done**, from the memory outline →
  YouTube chapters → description timestamps. Never final; the real tab with
  the **same id** replaces it, or the tab whose `replaces` names it (render it
  in a "provisional" state).
- **`replaces`** (optional): on the **first** emission of the tab that takes
  the provisional tab's place under **another id** — the provisional id. It
  happens when the tab the plan-done copy named does not survive assembly
  (e.g. podcast's required `moment_track` is backfilled as `segments` while
  plan-done streamed `key_moments`). The client drops the provisional tab and
  gives this tab its slot (and focus, if the provisional had it). Never on a
  provisional tab, never naming the tab's own id; a tab with the provisional's
  own id carries none (it replaces by id).
- **`attachments`** (optional): secondary components rendered around the
  primary (`stat_banner`, `tip_callout`, `summary_header`, `diagram_card`,
  `frame_strip`, `quick_quiz`).
- **`degradedFrom`** (optional): original component name when the assembly
  demote ladder downgraded the tab (e.g. `step_player` → `checklist`).

**Client rules.** Keep tabs in a map by `id`; a later `tab_ready` for an id
replaces the earlier one (content, position, flags) — a later final included
(last final wins). A tab carrying `replaces` removes the tab it names and takes
its slot. Order by `position`. A tab streamed earlier whose id is missing from
`complete.tabIds` was **withdrawn** — drop it (the fallback for `replaces`; also
a minimum-3 fallback tab the quiz made unnecessary). `meta.tabCount`/`tabLabels`
are the plan's tabs, sent before any `tab_ready`; the always-Moments tab can
arrive without a label there — `complete.tabIds` is authoritative.

**Emission sequence of a run** (English, the quiz planned). The default
(`EXTRACTION_PARALLEL=true`, the field-group extraction):

1. `metadata` … `synthesis_complete` (memory's hero) ∥ the plan.
2. Plan-done (plan and memory both in, the tabs reconciled): `triage_complete` +
   `meta`, then `tab_ready {provisional: true, final: false}` — the Moments tab,
   with the id, label and goal of the tab that will replace it (the plan's
   first `moment_track` tab, else `key_moments`). Not sent when no source has
   an entry.

   2½. Frames-done (frames and plan-done both reached; it can fall between any
   two groups of step 3): when reconcile deferred a code tab or tech's
   `code_playground`, the screen decides — a fresh `meta` when that changed the
   tab list (a code tab dropped or demoted, or `code_playground` added); the
   visual group is cut after it.
3. Per extraction group: `extraction_progress {section: "groups", batch, of}`,
   then a **preview** of every planned tab whose fields have all landed —
   `final: false`, **without `crossTabLinks` and `attachments`**, at the
   position its plan order gives it (normally its final one); never the
   overview, a `moment_track` tab or the quiz. A preview is re-sent only when
   its content changed. One `extraction_complete` once every group landed.
4. Assembly: `description_analysis`, then a preview (same rules, exact
   positions) of each tab the client lacks: the **overview**, tabs assembly
   added (backfills, filmstrip, minimum-3 fallbacks) and tabs the last
   groups or the frames changed.
5. Late group, in parallel: the `moment_track` tabs once framed (replacing the
   provisional by id, or under another id with `replaces`; final when no quiz
   is pending), `synthesis_complete` (superset), `enrichment_complete` (the
   quiz call started when the groups holding its domains' fields landed).
6. The **final re-emit**: every tab not yet received final, `final: true` at
   its final position, links and attachments included — the overview last.
7. `complete {tabCount, tabIds}`, then `done` (non-English: after translation).

With `EXTRACTION_PARALLEL=false` (one extraction call after frames-done):

1. – 2. as above.

   2½. Frames-done, before extraction starts: the same screen-code decision and
   fresh `meta` as above.
3. Assembly: `description_analysis`, then every tab but the `moment_track`
   tabs at its position — `final: false` while the quiz is pending (it can
   change any tab: links, a `quick_quiz` attachment, the overview's count);
   without a quiz `final: true`, except the overview while a late synthesis
   is pending.
4. Late group, in parallel: the `moment_track` tabs once framed (replacing the
   provisional by id, or under another id with `replaces`; final when no quiz
   is pending), `synthesis_complete` (superset), `enrichment_complete`.
5. The **final re-emit**: every tab not yet received final, in order, `final:
   true` at its final position — with a quiz, every tab of the response
   including the new quiz tab (last); without one, typically just the
   synthesis-patched overview.
6. `complete {tabCount, tabIds}`, then `done` (non-English: after translation).

A **cache hit** (completed row, or the Redis response cache) sends `cached`,
`metadata`, `triage_complete`, `meta`, then every stored tab once with
`final: true` and `position` = its index, `synthesis_complete`, `done` — no
`complete`, nothing provisional. A **reconnect** replays the run's stream from
its first event, and only the current run's events (the producer's lock claim
empties the stream).

### complete

```json
{
  "event": "complete",
  "tabCount": 5,
  "tabIds": ["overview", "stack_explorer", "key_moments", "concepts", "quiz"],
  "processingTimeMs": 25432,
  "degraded": false
}
```

- **`tabIds`**: the final response's tab ids in order (`tabCount` of them).
  Any tab streamed earlier but absent here was withdrawn.

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

Emitted **up to twice per run**, every field optional:

1. **Memory-done** (phase 2, only when memory produced a tldr or takeaways) — the hero:

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

2. **Synthesis-done** (phase 5, after the first tabs) — the four-field superset:
   `{tldr, keyTakeaways, masterSummary, seoDescription}`. `tldr`/`keyTakeaways` are
   memory's; synthesis writes them only when memory left one empty. A failed
   synthesis still emits it: memory's hero, empty `masterSummary`/`seoDescription`.
   A cached replay sends one superset event.

**Merge rules** (one rule, two consumers — `filledSynthesisFields` in `@vie/types`):

- An empty string or array never counts as set, so a later emission never blanks a
  field an earlier one filled; filled fields merge field by field.
- The superset wins: once a `masterSummary` is held, an emission without one (a
  reconnect replaying the partial) is ignored.
- Web: `handleSynthesisComplete` in `stream-event-processor.ts`. API relay
  (`stream.routes.ts` → `VideoRepository.mergeSynthesis` → `buildSynthesisMerge`):
  dotted `$set` of the filled `synthesis.*` fields; a partial is guarded by
  `synthesis.masterSummary ∈ {null, ""}`. Re-applying either emission is idempotent.
- Every (re-)dispatch of a row (new version, retry, stale-version regen, stall)
  first `$unset`s its `synthesis`, so a previous run's `masterSummary` can't block
  this run's partial.

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
          // Replace by id, order by `position` (a sort key, see #tab_ready):
          // the provisional Moments tab and non-final tabs arrive again (the
          // last final wins); `replaces` names a provisional tab to drop.
          setState(s => {
            const byId = { ...(s.tabsById || {}), [data.id]: data };
            if (data.replaces) delete byId[data.replaces];
            const tabs = Object.values(byId).sort((a, b) => a.position - b.position);
            return { ...s, tabsById: byId, tabs };
          });
          break;
        }

        case 'complete':
          // tabIds is the final order — drop tabs it withdrew.
          setState(s => {
            const kept = new Set(data.tabIds ?? (s.tabs || []).map(t => t.id));
            const tabs = (s.tabs || []).filter(t => kept.has(t.id));
            return { ...s, phase: 'complete', tabCount: data.tabCount, tabs };
          });
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

Per run (v10 pipeline; the default `EXTRACTION_PARALLEL=true`; models = the `config.py` / compose defaults, 4.0 picks — under compose a blank `.env` line keeps them; "Fast" = the fast tier: Haiku 4.5 by default, gpt-4o-mini with `LLM_FAST_PROVIDER=openai` as both `.env` templates and prod set):

| Stage | Model | Calls | Parallel? |
|-------|-------|-------|-----------|
| Description analysis | gpt-6-luna (`LLM_DESCRIPTION_MODEL`) | 1 | Background from metadata-end |
| Tier probe | Haiku 4.5 (`LLM_CLASSIFIER_MODEL`) | 1 | At transcript-ready, ∥ frames |
| Plan | Primary (Sonnet 5.5, effort medium) | 1 | ∥ Memory |
| Memory | Haiku 5.5 (`LLM_MEMORY_MODEL`; blank = `LLM_EXTRACTION_MODEL` in config.py only — under compose a blank line keeps Haiku 5.5) | 1 (none without timed transcript) | ∥ Plan |
| Frame vision | Sonnet 5.5 (`LLM_VISION_MODEL`) | 1-5 batches (none on LOW tier / manifest hit) | ∥ text branch |
| Chapter detect | Fast | 0-1 (videos past one extraction batch — over `MAX_MINUTES_PER_BATCH` or `MAX_TOKENS_PER_BATCH` — with no listed chapters, no description timestamps and no usable memory outline) | No — before the field groups fire |
| Extraction field groups | Sonnet 5.5 at effort low (`LLM_EXTRACTION_MODEL`) | 1-5 text groups + 0-1 visual group, per chunk (`grouping` in domains.json: `maxTextGroups` 5, `groupOutputBudget` 1,500 tokens; a long video has one chunk per chapter batch). A lost answer gets one more call | Text groups at plan-done (text1 first, the rest on its first streamed chunk), the visual group at frames-done; ≤ `EXTRACTION_PARALLEL_BATCHES` in flight |
| Extraction (`EXTRACTION_PARALLEL=false`) | Sonnet 5.5 at effort low (`LLM_EXTRACTION_MODEL`) | 1, or one per chapter batch | After frames-done; batches one at a time |
| Synthesis | Fast | 1 | ∥ moment fill ∥ quiz, after the first tabs (before them when memory left the hero empty) |
| Quiz (enrichment) | gpt-4o-mini (`LLM_ENRICHMENT_MODEL`) | 0-1 (plan with a quiz host) | ∥ moment fill ∥ synthesis; split path: started once the groups holding its domains' fields landed |
| Faithfulness judge | Fast tier | 0-6 (`LANGFUSE_FAITHFULNESS_SAMPLE_RATE` of the extracted claims, ≤ 6) | Background, after extraction; never blocks |
| Translation | gpt-6-luna (`LLM_TRANSLATION_MODEL`) | Non-English only: output batches (≤ 2,500 chars, ≤ 100 strings) + the title; RAG transcript batches (`rag_translation`) | ≤ 8 output batches in flight; RAG in the background |
| Assembly | None (code; moment frame fill does frame I/O with heartbeats) | 0 | — |

**Total** for a video one extraction batch covers: description analysis, tier probe, plan, memory, 1-6 extraction group calls, synthesis and the quiz when planned — plus 0-5 vision batches and ≤ 6 sampled judge calls. A long video multiplies the group calls by its chunks; a non-English video adds the translation batches. A refused answer adds one call on the refused model's previous model ([ERROR-HANDLING.md](./ERROR-HANDLING.md#refusals)).
