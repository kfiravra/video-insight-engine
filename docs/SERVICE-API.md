# Service: vie-api

Node.js backend service. REST API + WebSocket + SSE.

---

## Tech Stack

| Technology                | Purpose           |
| ------------------------- | ----------------- |
| Node.js 20                | Runtime           |
| Fastify 4                 | Web framework     |
| TypeScript                | Language          |
| Zod                       | Validation        |
| @fastify/jwt              | Authentication    |
| @fastify/websocket        | Real-time updates |
| mongodb                   | Database driver   |
| Vitest                    | Testing           |

---

## Project Structure

```
api/
├── Dockerfile
├── package.json
├── tsconfig.json
└── src/
    ├── index.ts                  # Entry point
    ├── app.ts                    # App builder with DI
    ├── config.ts                 # Environment config
    ├── container.ts              # Dependency injection container
    │
    ├── plugins/
    │   ├── mongodb.ts            # Database connection + indexes
    │   ├── jwt.ts                # Authentication
    │   ├── cors.ts               # CORS configuration
    │   ├── websocket.ts          # Real-time updates
    │   ├── rate-limit.ts         # Rate limiting (tier-aware)
    │   ├── tier.ts               # Tier decoration per request
    │   ├── request-id.ts         # X-Request-ID propagation
    │   └── sentry.ts             # Sentry init + PII filter
    │
    ├── repositories/
    │   ├── video.repository.ts
    │   ├── share.repository.ts
    │   ├── user.repository.ts
    │   └── user-deletion.repository.ts
    │
    ├── routes/
    │   ├── admin/                # Admin-only endpoints (queue, users)
    │   ├── auth.routes.ts
    │   ├── folders.routes.ts
    │   ├── videos.routes.ts
    │   ├── playlists.routes.ts
    │   ├── assistant.routes.ts   # /api/videos/:id/chat + /action (single-video RAG)
    │   ├── assistant-library.routes.ts # /api/assistant/library/{chat,search}
    │   ├── stream.routes.ts      # SSE proxy to summarizer
    │   ├── share.routes.ts       # Share creation + public access
    │   ├── ssr.routes.ts         # /s/:slug server-rendered share pages
    │   ├── override.routes.ts    # Category override
    │   ├── internal.routes.ts    # Internal service-to-service calls
    │   ├── internal-assistant.routes.ts # /internal/assistant/* — assistant→api library callbacks (authenticateInternal)
    │   ├── preferences.routes.ts # User preferences
    │   └── users.routes.ts       # User profile + GDPR deletion
    │
    ├── schemas/
    │   ├── video.schema.ts
    │   └── share.schema.ts
    │
    ├── services/
    │   ├── auth.service.ts
    │   ├── folder.service.ts
    │   ├── video.service.ts
    │   ├── playlist.service.ts
    │   ├── summarizer-client.ts  # HTTP client for summarizer
    │   ├── assistant-client.ts   # HTTP client for assistant (RAG chat)
    │   ├── share.service.ts
    │   ├── og-image.service.ts
    │   ├── cost-monitor.service.ts
    │   └── billing/payment-provider.ts, burst-limit.ts  # payment adapter (noop until P4); shared submit burst limit
    │
    ├── templates/
    │   └── share-page.ts         # HTML template for /s/:slug (v1.4)
    │
    ├── utils/
    │   ├── errors.ts             # Custom error classes (+ 8 new, v1.4)
    │   └── cors.ts               # CORS utilities
    │
    └── test/
        ├── setup.ts              # Test setup
        └── helpers.ts            # Test utilities & mocks
```

---

## Architecture Patterns

### Dependency Injection Container

All services and repositories are created in a central container and injected into the Fastify instance:

```typescript
// src/container.ts
export interface Container {
  videoRepository: VideoRepository;
  shareRepository: ShareRepository;      // v1.4
  videoService: VideoService;
  folderService: FolderService;
  authService: AuthService;
  playlistService: PlaylistService;
  assistantClient: AssistantClient;
  summarizerClient: SummarizerClient;
  shareService: ShareService;            // v1.4
  ogImageService: OgImageService;        // v1.4
  costMonitorService: CostMonitorService; // v1.4
}

export function createContainer(db: Db): Container {
  const videoRepository = new VideoRepository(db);
  // ... create all dependencies
  return { videoRepository, ... };
}
```

### App Builder Pattern

The app is built via `buildApp()` which allows dependency overrides for testing:

```typescript
// src/app.ts
export async function buildApp(options?: {
  logger?: boolean;
  container?: Partial<Container>;
}): Promise<FastifyInstance> {
  const fastify = Fastify({ logger: options?.logger ?? true });

  // Register plugins
  await fastify.register(corsSetup);
  await fastify.register(mongodbPlugin);
  await fastify.register(jwtPlugin);

  // Create container with optional overrides
  const container = {
    ...createContainer(fastify.db),
    ...options?.container,
  };

  fastify.decorate('container', container);

  // Register routes
  await fastify.register(authRoutes, { prefix: '/api/auth' });
  await fastify.register(videosRoutes, { prefix: '/api/videos' });
  // ...

  return fastify;
}
```

### Repository Pattern

Data access is abstracted into repository classes:

```typescript
// src/repositories/video.repository.ts
export class VideoRepository {
  constructor(private readonly db: Db) {}

  async userHasAccessToSummary(userId: string, videoSummaryId: string): Promise<boolean> {
    const video = await this.userVideosCollection.findOne({
      userId: new ObjectId(userId),
      videoSummaryId: new ObjectId(videoSummaryId),
    });
    return !!video;
  }

  async userOwnsVideo(userId: string, youtubeId: string): Promise<boolean> {
    const video = await this.userVideosCollection.findOne({
      userId: new ObjectId(userId),
      youtubeId,
    });
    return !!video;
  }
}
```

---

## Error Handling

Custom error classes for typed error responses:

```typescript
// src/utils/errors.ts
export class AppError extends Error {
  constructor(
    public readonly code: string,
    public readonly statusCode: number,
    message: string
  ) {
    super(message);
  }
}

export class VideoNotFoundError extends AppError {
  constructor() {
    super('VIDEO_NOT_FOUND', 404, 'Video not found');
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = 'Unauthorized') {
    super('UNAUTHORIZED', 401, message);
  }
}
```

---

## Authorization

All protected routes verify resource ownership before operations:

```typescript
// src/routes/assistant.routes.ts
export async function assistantRoutes(fastify: FastifyInstance) {
  const { assistantClient, videoRepository } = fastify.container;

  fastify.post('/chat', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const userId = req.user.userId;

    // Authorization check — verify user has access to the video
    const hasAccess = await videoRepository.userHasAccessToSummary(
      userId,
      req.body.videoSummaryId,
    );
    if (!hasAccess) {
      throw new VideoNotFoundError();
    }

    return assistantClient.chat({ ...req.body, userId });
  });
}
```

### Internal authentication (`authenticateInternal`)

The `/internal/assistant/*` routes are **service-to-service** callbacks the
vie-assistant uses to mutate the caller's library (folders, videos, generate).
They are not user-JWT protected — instead they use the `authenticateInternal`
preHandler:

```typescript
// src/routes/internal-assistant.routes.ts
fastify.get('/folders', {
  preHandler: [fastify.authenticateInternal],   // validates X-Internal-Secret + binds X-User-Id
}, async (req) => folderService.list(req.user.userId));
```

- `isValidInternalSecret()` (`src/utils/internal-auth.ts`) compares the
  `X-Internal-Secret` header to `config.INTERNAL_SECRET` in **constant time**
  via `node:crypto.timingSafeEqual` (length-checked first).
- `X-User-Id` is the only trusted source of the userId — every operation is
  scoped to it; a body-supplied userId is ignored. See [SECURITY.md](./SECURITY.md#service-to-service-auth).

---

## Input Validation

All request input is validated with Zod schemas with appropriate limits:

```typescript
// src/routes/assistant.routes.ts
const chatBodySchema = z.object({
  videoSummaryId: z.string().min(1),
  message: z.string().min(1).max(10000),  // Max length to prevent abuse
  chatHistory: z.array(z.object({
    role: z.enum(['user', 'assistant']),
    content: z.string(),
  })).optional(),
});

// In route handler
const parsed = chatBodySchema.safeParse(req.body);
if (!parsed.success) {
  return reply.status(400).send({
    error: 'Bad Request',
    message: parsed.error.errors[0]?.message || 'Invalid request body',
  });
}
```

---

## Testing

### Test Setup

Tests use Vitest with MongoDB Memory Server for isolation:

```typescript
// src/test/helpers.ts
export interface MockContainer {
  videoRepository: {
    userHasAccessToSummary: ReturnType<typeof vi.fn>;
    userOwnsVideo: ReturnType<typeof vi.fn>;
  };
  // ... other mocked services
}

export function createMockContainer(): MockContainer {
  return {
    videoRepository: {
      userHasAccessToSummary: vi.fn().mockResolvedValue(true),
      userOwnsVideo: vi.fn().mockResolvedValue(true),
    },
    // ...
  };
}

export async function buildTestApp(mockContainer?: Partial<MockContainer>): Promise<FastifyInstance> {
  return buildApp({
    logger: false,
    container: mockContainer as Partial<Container>,
  });
}
```

### Route Testing Pattern

```typescript
// src/routes/assistant.routes.test.ts
describe('assistant routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let authHeader: string;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    authHeader = await getAuthHeader(app);
  });

  it('should return 404 when user does not have access to video', async () => {
    mockContainer.videoRepository.userHasAccessToSummary.mockResolvedValue(false);

    const response = await app.inject({
      method: 'POST',
      url: '/api/assistant/chat',
      headers: { authorization: authHeader },
      payload: { videoSummaryId: 'video123', message: 'hello' },
    });

    expect(response.statusCode).toBe(404);
    expect(response.json()).toHaveProperty('error', 'VIDEO_NOT_FOUND');
  });
});
```

---

## Environment Variables

```bash
PORT=3000
MONGODB_URI=mongodb://vie-mongodb:27017/video-insight-engine
SUMMARIZER_URL=http://vie-summarizer:8000
ASSISTANT_URL=http://vie-assistant:8001
JWT_SECRET=your-secret-here
JWT_REFRESH_SECRET=your-refresh-secret-here
JWT_EXPIRES_IN=15m
JWT_REFRESH_EXPIRES_IN=7d
FRONTEND_URL=http://localhost:5173
CORS_ADDITIONAL_ORIGINS=
INTERNAL_SECRET=dev-internal-secret-change-me   # Shared secret for /internal/assistant/* (X-Internal-Secret); must match vie-assistant

# Submit burst guard: submissions per user per hour, one bucket for POST /api/videos,
# the assistant's generate route and playlist import (0 = off)
SUBMIT_BURST_LIMIT_PER_HOUR=20
# Chat messages a free account may send per rolling 24 h, both chat routes (0 = off)
CHAT_FREE_MESSAGES_PER_DAY=50
# Minutes-ledger shadow writes beside the old gate, never deciding (needs the replica set)
BILLING_SHADOW_ENABLED=true
# Billing repair job: stale holds, lot expiry, recurring grants, drift check,
# llm_usage.user_id after 90 days, GDPR hard-delete sweep (needs the replica set)
BILLING_REPAIR_ENABLED=true
BILLING_REPAIR_INTERVAL_SECONDS=300
BILLING_HOLD_MAX_AGE_HOURS=3
```

---

## Commands

```bash
# Development
npm run dev

# Build
npm run build

# Start
npm start

# Test
npm test

# Type check
npm run typecheck

# Lint
npm run lint
```

---

## Dockerfile

```dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

EXPOSE 3000

CMD ["node", "dist/index.js"]
```
