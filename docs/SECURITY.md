# Security

Authentication, authorization, and protection mechanisms.

---

## Authentication Flow

### JWT Token Strategy

```
┌──────────────────────────────────────────────────────────────┐
│                    TOKEN ARCHITECTURE                        │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Access Token                    Refresh Token               │
│  ├── Lifetime: 15 minutes        ├── Lifetime: 7 days       │
│  ├── Stored: Memory only         ├── Stored: HttpOnly cookie│
│  ├── Signed: JWT_SECRET          ├── Signed: JWT_REFRESH_   │
│  │                               │           SECRET          │
│  ├── Contains: userId, email,    ├── Contains: userId,      │
│  │   type: "access"              │   type: "refresh"        │
│  └── Used: API requests          └── Used: Get new access   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Endpoints

```
POST /api/auth/register
POST /api/auth/login
POST /api/auth/refresh    ← New
POST /api/auth/logout     ← New
GET  /api/auth/me
```

### Login Response

```json
{
  "accessToken": "eyJhbG...",
  "expiresIn": 900,
  "user": {
    "id": "...",
    "email": "...",
    "name": "..."
  }
}
```

Refresh token set as HttpOnly cookie:

```
Set-Cookie: refreshToken=xxx; HttpOnly; Secure; SameSite=Strict; Path=/api/auth/refresh
```

### Refresh Flow

```
┌─────────┐                    ┌─────────┐
│ Frontend│                    │ vie-api │
└────┬────┘                    └────┬────┘
     │                              │
     │  Access token expired        │
     │  (401 response)              │
     │                              │
     │  POST /auth/refresh          │
     │  Cookie: refreshToken=xxx    │
     │─────────────────────────────►│
     │                              │
     │  Verify refresh token        │
     │  Generate new access token   │
     │                              │
     │  { accessToken, expiresIn }  │
     │◄─────────────────────────────│
     │                              │
     │  Retry original request      │
     │─────────────────────────────►│
```

### Token Type Claim & Secret Separation

Access and refresh tokens are cryptographically distinct — neither can stand in
for the other:

- **Access tokens** are signed with `JWT_SECRET` and carry `type: "access"`.
- **Refresh tokens** are signed with `JWT_REFRESH_SECRET` (a separate
  `@fastify/jwt` namespace, `fastify.jwt.refresh`) and carry `type: "refresh"`.
- `POST /api/auth/refresh` verifies against `JWT_REFRESH_SECRET` **and**
  requires `type === "refresh"` — a stolen 15-minute access token cannot be
  replayed against the refresh endpoint to self-renew.
- The `authenticate` preHandler (and WebSocket auth) rejects any token with
  `type: "refresh"` — refresh tokens never authenticate API requests.
- Refresh-token **rotation is not implemented** (deferred decision): a refresh
  call returns a new access token only; the refresh cookie is unchanged.

**Migration window (legacy tokens):** tokens issued before the `type` claim
existed carry no `type` and are still accepted as access tokens by the
`authenticate` preHandler. This backward-compat window closes naturally once
every pre-rollout token has expired (15 minutes for access tokens, 7 days for
refresh tokens). Legacy refresh tokens were signed with `JWT_SECRET`, so they
fail verification at `/api/auth/refresh` — those users re-login once.

### Implementation

```typescript
// api/src/plugins/jwt.ts — two registrations, two secrets
await fastify.register(fastifyJwt, {
  secret: config.JWT_SECRET,
  sign: { expiresIn: config.JWT_EXPIRES_IN },
});
await fastify.register(fastifyJwt, {
  secret: config.JWT_REFRESH_SECRET,
  namespace: "refresh",
  sign: { expiresIn: config.JWT_REFRESH_EXPIRES_IN },
});

// api/src/routes/auth.routes.ts

// Login/register - returns access token + sets refresh cookie
const accessToken = fastify.jwt.sign(
  { userId: user.id, email: user.email, type: "access" },
  { expiresIn: config.JWT_EXPIRES_IN }
);

const refreshToken = fastify.jwt.refresh.sign(
  { userId: user.id, type: "refresh" },
  { expiresIn: config.JWT_REFRESH_EXPIRES_IN }
);

reply.setCookie("refreshToken", refreshToken, {
  httpOnly: true,
  secure: config.NODE_ENV === "production",
  sameSite: "strict",
  path: "/api/auth/refresh",
  maxAge: 7 * 24 * 60 * 60, // 7 days
});

// Refresh - validates refresh secret + type, returns new access token
fastify.post("/refresh", async (req, reply) => {
  const refreshToken = req.cookies.refreshToken;
  if (!refreshToken) {
    throw new UnauthorizedError("Refresh token expired");
  }

  try {
    const payload = fastify.jwt.refresh.verify<{ userId: string; type?: string }>(refreshToken);
    if (payload.type !== "refresh") {
      throw new UnauthorizedError("Refresh token expired");
    }

    const accessToken = fastify.jwt.sign(
      { userId: payload.userId, type: "access" },
      { expiresIn: config.JWT_EXPIRES_IN }
    );

    return { accessToken, expiresIn: 900 };
  } catch {
    reply.clearCookie("refreshToken", { path: "/api/auth/refresh" });
    throw new UnauthorizedError("Refresh token expired");
  }
});

// Logout - clears refresh token cookie
fastify.post("/logout", async (req, reply) => {
  reply.clearCookie("refreshToken", { path: "/api/auth/refresh" });
  return { success: true };
});
```

### Demo Account (Read-Mostly)

Every demo visitor ([DEPLOY.md](./DEPLOY.md#demo-mode)) holds a token for one shared account, so anything that mutates its library or spends its budget would hit every other visitor. The shared account is therefore read-mostly: the actions below return 403 `DEMO_RESTRICTED` and the underlying service is never called.

| Refused for the demo account | Routes |
|------------------------------|--------|
| Delete or move a video, change its category | `DELETE /videos/:id`, `PATCH /videos/:id/move`, `PATCH /videos/:id/override-category` |
| Rename, move or delete a folder | `PATCH /folders/:id`, `DELETE /folders/:id` |
| Force a paid re-run or choose providers | `POST /videos` and `POST /playlists/import` with `providers`; `POST /videos` with `bypassCache: true` or `cold: true` |
| Library-rewriting assistant actions | `POST /assistant/action` with `organize_library`, `rename_folder`, `move_folder`, `delete_folder`, `move_video`; and the matching `/internal/assistant/*` callbacks (`PATCH`/`DELETE` folders, `PATCH` video move) |
| Change the account's `displayName` or `username` | `PATCH /users/me/preferences` |
| Schedule the account's deletion | `DELETE /users/me` |

Still allowed: reading the library, creating folders, a plain `POST /videos` or playlist import, share links (additive and idempotent), notes, chat, and the theme preference.

The account is identified **by user id, not by the JWT `email` claim**. The claim is optional, and the assistant's `/internal/assistant/*` callbacks carry only an `X-User-Id`. `plugins/demo-guard.ts` resolves the id once from `DEMO_USER_EMAIL` and caches it (10 min when found, 60 s when missing, so an account created after boot is picked up); a blank `DEMO_USER_EMAIL` disables the guard with no database call. Routes opt in with `preHandler: [fastify.authenticate, fastify.requireNotDemo]` (or `authenticateInternal`); handlers that refuse only some bodies call `await fastify.requireNotDemo(req)` after parsing. A lookup failure fails the request rather than letting the demo account through. Non-demo users are unaffected on every route.

---

## Rate Limiting

### Limits by Endpoint

| Endpoint              | Limit | Window   | Scope |
| --------------------- | ----- | -------- | ----- |
| `POST /auth/register` | 5     | 1 hour   | IP    |
| `POST /auth/login`    | 10    | 15 min   | IP    |
| `POST /auth/refresh`  | 30    | 15 min   | IP    |
| `POST /videos`        | 10    | 24 hours | User  |
| `GET /explain/*`      | 60    | 1 hour   | User  |
| `POST /explain/chat`  | 100   | 1 hour   | User  |
| `POST /share/:id`     | 20    | 1 hour   | User  |
| `GET /share/:slug`    | 100   | 1 min    | User  |
| `POST /share/:slug/like` | 10 | 1 min   | IP    |
| `GET /s/:slug`        | 60    | 1 min    | IP    |
| `GET /s/:slug/og-image.png` | 30 | 1 min | IP    |
| `POST /videos`, `POST /internal/assistant/generate`, `POST /playlists/import` | 20 (`SUBMIT_BURST_LIMIT_PER_HOUR`) | 1 hour, fixed | User, one shared bucket (see below) |
| `* (default)`         | 100   | 1 min    | User  |

Demo logins (`POST /auth/login` with `{ "demo": true }`, see [DEPLOY.md](./DEPLOY.md#demo-mode)) are ordinary requests to the login route and share its 10 / 15 min per-IP limit. The demo account's credentials stay in vie-api's environment and are never sent to the browser. Because every visitor holds a token for that one account, it cannot schedule its own deletion: `DELETE /users/me` returns 403 `DEMO_RESTRICTED`. The rest of its read-mostly restrictions are listed under [Demo Account (Read-Mostly)](#demo-account-read-mostly).

### Implementation

```typescript
// api/src/plugins/rate-limit.ts
import rateLimit from "@fastify/rate-limit";

await fastify.register(rateLimit, {
  global: true,
  max: 100,
  timeWindow: "1 minute",
  keyGenerator: (req) => req.user?.id || req.ip,
});

// Per-route override for video submission
fastify.post(
  "/videos",
  {
    config: {
      rateLimit: {
        max: 10,
        timeWindow: "24 hours",
        keyGenerator: (req) => req.user.id,
      },
    },
  },
  handler
);

// Auth routes - limit by IP
fastify.post(
  "/auth/login",
  {
    config: {
      rateLimit: {
        max: 10,
        timeWindow: "15 minutes",
        keyGenerator: (req) => req.ip,
      },
    },
  },
  loginHandler
);
```

The store is Redis-backed (dedicated connection per concern:
`api/src/plugins/rate-limit.ts` for the limiter, `api/src/plugins/redis.ts`
for the dispatch guard) with `skipOnError: true` — a Redis outage fails open
(requests served unlimited) rather than 500ing. Both clients' retry strategy
reconnects forever with linear backoff capped at 2 s, so the fail-open window
lasts only as long as the outage and self-heals; a strategy that gives up
permanently ends the ioredis client and would silently disable rate limiting
for the remainder of the process lifetime.

### Submit burst limit

An abuse guard, not a billing limit: one Redis counter per user,
`vie:api:submit-burst:<userId>`, a **fixed one-hour window** that opens at the
user's first submission of the hour and holds `SUBMIT_BURST_LIMIT_PER_HOUR`
(default 20; `0` turns it off). `api/src/services/billing/burst-limit.ts` does
`INCR` and `EXPIRE` in one Lua script, so a crash cannot leave a counter without
a TTL. It is deliberately not `@fastify/rate-limit`, whose keys include the
route: `POST /api/videos`, the assistant's `POST /internal/assistant/generate`
and `POST /api/playlists/import` all draw from the same bucket, so a user cannot
triple the allowance by switching routes. A playlist import costs one, however
many videos it holds. A rejected attempt counts but never extends the window.

When the bucket is full the route answers `429`, error code `RATE_LIMITED`, with
`Too many submissions in the last hour. Try again in N min.` (N rounds the time
left up, minimum 1). The message never says "daily". Like the limiter above it
fails open: a Redis error logs a `submit_burst_check_failed` warning and lets
the submission through. The check runs before any work, after the Idempotency-Key
header check, so an idempotent replay counts like a new submission. The old
`VIDEO_DAILY_LIMIT` per-route limiter still runs alongside it until the dollar
gate is removed.

### Response on Limit

```json
{
  "error": "RATE_LIMITED",
  "message": "Too many requests. Try again in 3600 seconds.",
  "retryAfter": 3600
}
```

Headers:

```
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1704067200
Retry-After: 3600
```

---

## Input Validation

### YouTube URL Validation

```typescript
const youtubeUrlSchema = z
  .string()
  .url()
  .refine((url) => {
    const patterns = [
      /^https?:\/\/(www\.)?youtube\.com\/watch\?v=[\w-]{11}/,
      /^https?:\/\/youtu\.be\/[\w-]{11}/,
      /^https?:\/\/(www\.)?youtube\.com\/embed\/[\w-]{11}/,
    ];
    return patterns.some((p) => p.test(url));
  }, "Invalid YouTube URL");
```

### Password Requirements

```typescript
const passwordSchema = z
  .string()
  .min(8, "Password must be at least 8 characters")
  .regex(/[A-Z]/, "Password must contain uppercase letter")
  .regex(/[a-z]/, "Password must contain lowercase letter")
  .regex(/[0-9]/, "Password must contain number");
```

### Email Validation

```typescript
const emailSchema = z.string().email().toLowerCase().max(255);
```

---

## CORS Configuration

```typescript
// api/src/plugins/cors.ts
import cors from "@fastify/cors";

await fastify.register(cors, {
  origin: [
    "http://localhost:5173", // Dev
    process.env.FRONTEND_URL, // Prod
  ],
  credentials: true, // For cookies
  methods: ["GET", "POST", "PUT", "PATCH", "DELETE"],
  allowedHeaders: ["Content-Type", "Authorization"],
});
```

---

## Internal Service Security

### Network Isolation

```yaml
# docker-compose.yml
services:
  vie-summarizer:
    # No exposed ports - internal only
    networks:
      - vie-internal

  vie-assistant:
    # No exposed ports - internal only
    networks:
      - vie-internal

networks:
  vie-network: # External (frontend, API)
  vie-internal: # Internal only (workers)
```

### Webhook Authentication

No webhook endpoint exists today: the Paddle Classic stub and its
`PADDLE_WEBHOOK_SECRET` were removed (its events never matched a user), so
there is no payment secret to set in any environment. The payment adapter that
P4 adds (`PaymentProviderAdapter.verifyWebhook`, see
`api/src/services/billing/payment-provider.ts`; `NoopPaymentProvider` throws
`PROVIDER_NOT_CONFIGURED` until then) must meet these rules before a webhook
route is registered:

- **Mandatory:** the signature is verified over the exact raw body with a
  constant-time comparison; an unsigned or unverifiable event is never acted on,
  in any environment
- **Freshness:** a signature older than 5 minutes is rejected, bounding the
  replay window of a captured webhook
- **Idempotent:** the provider event id is the unique key of the `paymentEvents`
  collection, so a replayed event changes nothing

### WebSocket Authentication

The `/ws` endpoint authenticates via the `Sec-WebSocket-Protocol` header, not
the query string (query strings land verbatim in access logs and would leak
live JWTs):

```typescript
// Client (apps/web/src/hooks/use-websocket.ts)
new WebSocket(`${WS_URL}/ws`, ["vie-auth", accessToken]);

// Server (api/src/plugins/websocket.ts)
// - selects the "vie-auth" subprotocol via handleProtocols
// - extracts the JWT from the offered protocol list and verifies it
// - closes 4001 on missing/invalid tokens and on refresh-type tokens
```

### Admin API Key

Every `/api/admin/*` route (`DELETE /api/admin/users/:id[?immediate=true]`, `/api/admin/queue/*`,
`DELETE /api/admin/videos/:youtubeId`) is guarded by `fastify.requireAdmin(permission)`
(`api/src/plugins/admin-auth.ts`), which needs the shared key **and** resolves who is acting:

1. `x-admin-key` is compared against `ADMIN_API_KEY` with `crypto.timingSafeEqual`
   (`api/src/utils/admin-auth.ts`), the same constant-time pattern as `isValidInternalSecret`. No key, `401`.
2. **A signed `x-admin-actor` header** (`<userId>.<unixTs>.<hex hmac>`, sent by vie-admin for every call it
   makes for a named admin) makes the request that user's. The HMAC-SHA256 is keyed on `ADMIN_SESSION_SECRET`
   and covers `userId|unixTs|sha256(exact request body)`, so it cannot be replayed against another payload;
   it is compared in constant time and accepted for five minutes either way of now. Anything malformed,
   tampered, stale or signed with another secret is `401`. A valid header is still re-checked against the
   database (`UserRepository.hasPermission`): an admin demoted after vie-admin signed it gets `403`.
   `request.adminActor` is `{ kind: 'user', userId }`; the audit rows and deletion origins name that user.
3. **No actor header** is the shared machine key acting alone: it passes only `READ_PERMISSIONS` and gets
   `403` on every mutation. A client-supplied `x-admin-id` is no longer read anywhere.

Replaying a captured header with the same body inside its five minutes would repeat the action; the
admin network path is loopback or the SSH tunnel (Caddy `404`s `/api/admin/*` publicly). The format is
pinned by shared test vectors (`packages/types/test-vectors/admin-auth.json`) that both vie-admin's
signer (Python) and vie-api's verifier (TypeScript) are tested against.

### Admin Panel Login (vie-admin :8002)

`POST /auth/login` (`services/admin/src/routes/auth.py`) verifies email +
bcrypt password against the `users` collection and returns a stateless
HMAC-signed session token naming the user (`userId|email|expiry`, keyed on
`ADMIN_SESSION_SECRET`, 12 h TTL, accepted by `ApiKeyMiddleware`). Hardening:

- **Session secret separate from the machine key** — `ADMIN_API_KEY` stays the
  machine credential (raw bearer on vie-admin, `X-Admin-Key` to vie-api) and is
  never an HMAC key: whoever holds it cannot mint a session for an arbitrary
  user. A token HMAC-keyed on `ADMIN_API_KEY` is rejected. The machine key may
  only `GET`; any other method is `403`.
- **Sessions are re-validated** — the middleware checks on every request that
  the token's user is still an active admin (roles rule below, `deletedAt`
  unset), caching the verdict for 60 s per user. A demoted or soft-deleted
  admin is `401` within a minute, not at token expiry; if the users collection
  cannot be read the check fails closed (`503`). Logs name the user id, never
  the email.

- **Per-IP login throttle** — in-process sliding window, 5 attempts / 60 s,
  counted BEFORE the bcrypt verify (caps CPU-burn DoS as well as brute
  force); 429 on exceed. Memory is bounded at 1024 tracked clients with
  stale-window eviction.
- **No dummy-hash bypass** — accounts whose user doc lacks a string
  `passwordHash` (OAuth-only accounts, migration artifacts) are
  unauthenticatable; they still burn a bcrypt verify against a dummy hash so
  timing parity is preserved.

Trade-offs, by design for the direct-port deployment: a demotion takes up to 60 s
to reach a live session (rotating `ADMIN_SESSION_SECRET` kills every session at
once and logs every admin out; recreate vie-api too, it verifies the actor
header with the same value), and the throttle keys on the direct client IP (not
proxy-header-aware).

### Roles and Permissions

`users.roles` is an array of roles (`'admin'` today; the shape leaves room for a
read-only `'support'` role) and `users.roleSource` says where it came from
(`env` = reconciled from `ADMIN_EMAILS` at boot, `granted` = set by an admin or a
migration). Routes name a **permission**, never a role; the role-to-permission map
is `ROLE_PERMISSIONS` in `packages/types/src/roles.ts`, and
`UserRepository.hasPermission(userId, permission)` answers it. A malformed id, an
unknown user and a soft-deleted account all answer `false`. `roles` is
authoritative whenever it is an array (even empty); only a user with **no**
`roles` array and a legacy `role: 'admin'` reads as an admin (`effectiveRoles`),
until the billing migration unsets the legacy field. Demoting writes
`roles: []` and unsets `role`, so a leftover legacy value never revives access.

| Permission | Allows | `admin` | Machine key (`ADMIN_API_KEY`, no actor) |
| --- | --- | :---: | :---: |
| `billing:read` | read an account, its lots, holds and ledger | yes | yes |
| `audit:read` | read the `adminAudit` log | yes | yes |
| `queue:read` | `GET /api/admin/queue/stats`, `/dlq` | yes | yes |
| `billing:grant` | grant credit minutes, adjust balances, recurring grants | yes | no |
| `plan:set` | set a manual plan | yes | no |
| `caps:set` | per-account caps and block | yes | no |
| `users:delete` | `DELETE /api/admin/users/:id` | yes | no |
| `videos:purge` | `DELETE /api/admin/videos/:youtubeId` | yes | no |
| `queue:replay` | `POST /api/admin/queue/replay` | yes | no |
| `alerts:write` | change alert thresholds | yes | no |
| `roles:manage` | grant or revoke roles | yes | no |
| `cold:run` | `cold` benchmark runs on `POST /api/videos` (`UserRepository.mayRunCold`, with the eval account) | yes | no |

The machine key passes only `READ_PERMISSIONS` (`billing:read`, `audit:read`,
`queue:read`); every mutation needs a named human actor. `fastify.requireAdmin(permission)`
enforces this matrix on every `/api/admin/*` route (see "Admin API Key" above), and
`cold:run` is enforced through `mayRunCold`. vie-admin applies the same admin rule
(`src/roles.py`) at login and on every session request; the two implementations are
tested against shared cases (`packages/types/test-vectors/admin-auth.json`).

**Boot seeding (`ADMIN_EMAILS`).** vie-api reconciles the comma-separated
`ADMIN_EMAILS` list with the `users` collection on every boot
(`services/admin-seed.service.ts`): listed **existing** accounts are promoted
(`roleSource: 'env'`), `env` admins whose email left the list are demoted,
`granted` admins and legacy `role: 'admin'` users (read as `granted`) are never
touched, and users are never created from the list. The reconcile refuses, changing
nothing, if it would leave no active admin, and an empty list is a no-op, so a
missing env row cannot lock anyone out. The shared demo account and soft-deleted
accounts are never promoted. Emails are matched ignoring case and never logged in
full. Every change writes an audit row with actor `system:boot`.

**Admin audit trail.** Every admin mutation writes one append-only `adminAudit`
row (`AuditService.record`): actor, action, target, a before/after snapshot,
reason, request id and client ip. Today that covers admin user deletion (an audit
row at request time for the 30-day soft delete too), the global video purge, DLQ
replay and the boot role changes above. The actor is the admin the signed
`x-admin-actor` header names (`system:boot` for boot seeding); `system:admin-key` is
the shared key acting alone, which can only read and so writes no audit rows. The mutation has already happened when the row is
written, so a failed audit insert is logged at `error` and never turns the request
into a failure. Rows hold no secrets and no target email.

### Dev-Default Secret Refusal

`api/src/config.ts` refuses to start in production when `JWT_SECRET`,
`JWT_REFRESH_SECRET`, `INTERNAL_SECRET`, or `ADMIN_API_KEY` still equal any
well-known dev placeholder (config defaults, `.env.example` values, or
docker-compose fallbacks). `ADMIN_SESSION_SECRET` (which vie-api uses to verify the
actor header) also has to be at least 32 characters and differ from
`ADMIN_API_KEY` in production, the same rules as vie-admin's; the refusal names the
variable, never its value.

vie-admin does the same from its lifespan (`validate_secrets()` in
`services/admin/src/config.py`): outside a dev `ENVIRONMENT` (`""`,
`development`, `dev`, `test`, `local`; any other name, including a typo, counts
as production) it refuses to start when `ADMIN_API_KEY` or
`ADMIN_SESSION_SECRET` is empty or its dev default (`dev-admin-key-change-me` /
`dev-admin-session-secret-change-me` — the compose fallbacks and template
values), when `ADMIN_SESSION_SECRET` is shorter than 32 characters (no length
rule on `ADMIN_API_KEY`), or when the two are equal. The error names the
variable, never its value. Dev environments log a warning instead. `docker-compose.prod.yml` sets
`ENVIRONMENT=production` for vie-admin and requires both variables (`:?`).

### Service-to-Service Auth

The vie-assistant service calls back into vie-api to mutate the caller's library
(create/rename/move/delete folders, move videos, generate a video). These
`/internal/assistant/*` routes are guarded by the `authenticateInternal`
preHandler (`api/src/utils/internal-auth.ts`):

```bash
# .env (set on both vie-api and vie-assistant)
INTERNAL_SECRET=xxx

# Every /internal/assistant/* request must send BOTH headers:
X-Internal-Secret: xxx          # validated against INTERNAL_SECRET
X-User-Id: <userId>             # scopes all work; a body-supplied userId is never trusted
```

- **Constant-time comparison** — `isValidInternalSecret()` compares the secret
  via `node:crypto.timingSafeEqual` (a plain `!==` would leak the secret byte by
  byte through response timing). A length check runs first because
  `timingSafeEqual` throws on unequal-length buffers.
- **User scoping** — the preHandler binds `req.user.userId` from `X-User-Id`;
  routes scope every folder/video operation to that user, so the assistant can
  never act outside the authenticated caller's library.

---

## Security Headers

```typescript
// api/src/plugins/security.ts
import helmet from "@fastify/helmet";

await fastify.register(helmet, {
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      imgSrc: ["'self'", "img.youtube.com", "i.ytimg.com"],
      scriptSrc: ["'self'"],
    },
  },
  crossOriginEmbedderPolicy: false, // For YouTube thumbnails
});
```

---

## SSRF Protection

### OG Image Service (`og-image.service.ts`)

Thumbnail URLs from the database are user-influenced. Before fetching, the service validates the hostname against an allowlist:

```typescript
const allowedHosts = ['img.youtube.com', 'i.ytimg.com', 'i3.ytimg.com', 'i1.ytimg.com'];
const parsed = new URL(imageUrl);
if (!allowedHosts.includes(parsed.hostname)) {
  this.logger.warn({ slug, imageUrl }, 'Rejected non-YouTube thumbnail URL');
  return null;
}
```

### XSS Protection in Share Templates (`share-page.ts`)

All dynamic values in the share page template are escaped via `escapeHtml()`:
- Titles, creator names, and TLDRs use `escapeHtml()` for HTML content
- Thumbnail URLs use `escapeHtml()` in `<img src>` attributes
- JSON-LD uses `JSON.stringify().replace(/</g, '\\u003c')` to prevent script breakout

### MongoDB Field Allowlist (`mongodb_repository.py`)

`save_structured_result()` uses a field allowlist (`_ALLOWED_RESULT_KEYS`) to prevent pipeline injection of arbitrary fields like `_id` or `userId` into MongoDB documents.

### Internal Secret Validation (`config.py`)

The summarizer logs a warning at startup if `INTERNAL_SECRET` is using the default value in non-dev environments. This prevents accidental deployment with a well-known default.

---

## Secrets Management

### Required Secrets

| Secret                 | Where Used            | How to Generate           |
| ---------------------- | --------------------- | ------------------------- |
| `JWT_SECRET`           | vie-api               | `openssl rand -base64 32` |
| `JWT_REFRESH_SECRET`   | vie-api               | `openssl rand -base64 32` |
| `ANTHROPIC_API_KEY`    | summarizer, assistant | Anthropic Console         |
| `OPENAI_API_KEY`       | summarizer, assistant | OpenAI Platform           |
| `INTERNAL_SECRET`      | service-to-service    | `openssl rand -base64 32` |
| `ADMIN_API_KEY`        | vie-api, vie-admin    | `openssl rand -hex 32`    |
| `ADMIN_SESSION_SECRET` | vie-admin, vie-api (same value) | `openssl rand -hex 32` (≠ `ADMIN_API_KEY`) |

### Production Setup

```bash
# Never commit secrets
# Use environment variables or secrets manager

# Docker Swarm
docker secret create jwt_secret ./jwt_secret.txt

# Kubernetes
kubectl create secret generic vie-secrets \
  --from-literal=jwt-secret=xxx \
  --from-literal=anthropic-key=xxx
```

---

## Security Checklist

Before deploying:

- [ ] All secrets in environment variables (not hardcoded)
- [ ] JWT_SECRET and JWT_REFRESH_SECRET are different
- [ ] ADMIN_SESSION_SECRET and ADMIN_API_KEY are different (vie-admin and vie-api refuse to start otherwise), and vie-api and vie-admin hold the same ADMIN_SESSION_SECRET
- [ ] Rate limiting enabled on all routes
- [ ] CORS configured for specific origins (not `*`)
- [ ] Password hashing uses bcrypt with cost ≥ 10
- [ ] Refresh tokens stored in HttpOnly cookies
- [ ] Input validation on all user inputs
- [ ] SQL/NoSQL injection prevention (parameterized queries)
- [ ] Security headers enabled via helmet
