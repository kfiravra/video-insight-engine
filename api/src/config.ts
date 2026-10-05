import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { z } from 'zod';

// ─── Canonical pipeline version ───────────────────────────────────────
// Single-sourced from packages/shared/src/config/pipeline-version.json,
// shared with the summarizer (which namespaces its Redis response cache by
// it and stamps it as `pipelineVersion` on every persisted Mongo summary
// doc). Here it feeds idempotency request hashes, the content-addressed
// `videoSummaryCache.dedupKey`, and the stale-doc regen check in
// video.service.ts. Deliberately NOT env-overridable — a divergent env
// value would resurrect the two-independent-knobs drift this replaces.
function loadPipelineVersion(): string {
  const candidates = [
    // Docker: mounted next to domains.json (see docker-compose.yml volumes).
    '/app/shared/pipeline-version.json',
    // Local dev + tests: repo-relative (api/src or api/dist → repo root).
    join(
      dirname(fileURLToPath(import.meta.url)),
      '../../packages/shared/src/config/pipeline-version.json',
    ),
  ];
  for (const path of candidates) {
    try {
      const parsed = JSON.parse(readFileSync(path, 'utf-8')) as { version?: unknown };
      if (typeof parsed.version === 'string' && parsed.version.length > 0) {
        return parsed.version;
      }
    } catch {
      // Candidate missing/unreadable — fall through to the next location.
    }
  }
  throw new Error(
    `pipeline-version.json not found or invalid (tried: ${candidates.join(', ')})`,
  );
}

// Well-known dev placeholder secrets (config defaults, .env.example, and
// docker-compose fallbacks). Booting production with any of these is a
// full-compromise misconfiguration, so the schema refuses to start.
const DEV_DEFAULT_SECRETS: Record<string, string[]> = {
  JWT_SECRET: [
    'your-super-secret-jwt-key-change-this-in-production',
    'dev-jwt-secret-change-in-production',
  ],
  JWT_REFRESH_SECRET: [
    'your-refresh-secret-key-change-this-in-production',
    'dev-refresh-secret-change-in-production',
  ],
  INTERNAL_SECRET: [
    'dev-internal-secret-change-me',
    'your-internal-secret-generate-with-openssl-rand',
  ],
  ADMIN_API_KEY: ['dev-admin-key-change-me'],
};

const envSchema = z.object({
  PORT: z.string().default('3000').transform(Number),
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  MONGODB_URI: z.string(),
  SUMMARIZER_URL: z.string().default('http://vie-summarizer:8000'),
  JWT_SECRET: z.string().min(32),
  JWT_REFRESH_SECRET: z.string().min(32),
  JWT_EXPIRES_IN: z.string().default('15m'),
  JWT_REFRESH_EXPIRES_IN: z.string().default('7d'),
  FRONTEND_URL: z.string().default('http://localhost:5173'),
  // Additional CORS origins (comma-separated, optional)
  // Combined with FRONTEND_URL to form the full list of allowed origins
  CORS_ADDITIONAL_ORIGINS: z.string().default(''),
  // Assistant service URL (HTTP API + SSE)
  ASSISTANT_URL: z.string().default('http://vie-assistant:8001'),
  // Shared secret for internal service-to-service auth
  INTERNAL_SECRET: z.string().min(16).default('dev-internal-secret-change-me'),
  // Configurable rate limits
  RATE_LIMIT_MAX: z.string().default('300').transform(Number),
  RATE_LIMIT_WINDOW: z.string().default('1 minute'),
  // Playlist-specific rate limits (higher in test mode to avoid test interference)
  PLAYLIST_PREVIEW_RATE_LIMIT: z.string().default('30').transform(Number),
  PLAYLIST_IMPORT_RATE_LIMIT: z.string().default('5').transform(Number),
  // Video creation daily limit (0 = unlimited for admins/dev)
  VIDEO_DAILY_LIMIT: z.string().default('100').transform(Number),
  // Self-service signup. 'false' closes POST /api/auth/register (403) and the
  // web app hides its signup links; accounts are then created out-of-band
  // (scripts/create-admin.ts). Any other value keeps registration open.
  ALLOW_REGISTRATION: z.string().default('true').transform(v => v.trim().toLowerCase() !== 'false'),
  // Demo mode. With both set, POST /api/auth/login accepts { demo: true } and
  // signs in this account server-side; either one blank keeps demo off. The
  // email is normalized like a login email because the lookup is exact-match.
  DEMO_USER_EMAIL: z.string().default('').transform(v => v.trim().toLowerCase()),
  DEMO_USER_PASSWORD: z.string().default(''),
  // Payment (Paddle)
  PADDLE_WEBHOOK_SECRET: z.string().default(''),
  PADDLE_PRO_PRICE_ID: z.string().default(''),
  PADDLE_TEAM_PRICE_ID: z.string().default(''),
  // Cost monitoring
  COST_DAILY_LIMIT: z.string().default('50').transform(Number),
  // Per-user daily cost limits in USD (0 disables the limit; -1 means unlimited)
  USER_COST_LIMIT_FREE: z.string().default('2').transform(Number),
  USER_COST_LIMIT_PRO: z.string().default('20').transform(Number),
  USER_COST_LIMIT_TEAM: z.string().default('-1').transform(Number),
  // ─── RabbitMQ job queue ─────────────────────────────────────────────
  // amqplib connection URL. The credentials are supplied via docker-compose env
  // (RABBITMQ_DEFAULT_USER / PASS) so this default works in the dev compose.
  RABBITMQ_URL: z.string().default('amqp://vie:vie-dev@vie-rabbitmq:5672/'),
  // Publisher confirms are always enabled; this caps the unacknowledged outflight
  // window. Conservative because the API publishes one message per request.
  RABBITMQ_PUBLISH_TIMEOUT_MS: z.string().default('5000').transform(Number),
  // Flip ON to route POST /api/videos through RabbitMQ. Behind a flag so shadow
  // testing and quick rollback are both one env-var away.
  USE_QUEUE_PIPELINE: z.string().default('false').transform(v => v === 'true'),
  // Admin endpoints for /api/admin/queue/* and DLQ replay. Different from
  // INTERNAL_SECRET so a leaked admin key cannot impersonate the summarizer.
  ADMIN_API_KEY: z.string().min(8).default('dev-admin-key-change-me'),
  // ─── Idempotency ────────────────────────────────────────────────────
  // NOTE: PIPELINE_VERSION is intentionally absent here — it is single-
  // sourced from pipeline-version.json (see loadPipelineVersion above) and
  // merged into the exported config object, never read from env.
  // TTL window for an idempotency hit. 24h is long enough for accidental
  // double-submits and short enough that "I want to retry tomorrow" still works.
  IDEMPOTENCY_TTL_SECONDS: z.string().default('86400').transform(Number),
  // ─── Redis (dispatch guard + future caches) ─────────────────────────
  // MUST point at the same Redis instance the summarizer uses (defined in
  // services/summarizer/src/config.py:PIPELINE_LOCK_TTL_SECONDS context).
  // The dispatch guard is API-side only — it prevents publishing duplicate
  // queue messages for the same video_summary_id — but lives in the same
  // namespace as the summarizer's pipeline lock for operational coherence.
  REDIS_URL: z.string().default('redis://vie-redis:6379'),
  // TTL on the per-(video_summary_id) dispatch guard. Must exceed the
  // summarizer's PIPELINE_LOCK_TTL_SECONDS (default 600s) so a slow run
  // doesn't drop the guard mid-pipeline and let a parallel publish slip
  // through. 900s = 50% headroom. internal.routes releases the guard on
  // terminal FAILED status so the user-initiated retry doesn't wait this out.
  DISPATCH_GUARD_TTL_SECONDS: z.string().default('900').transform(Number),
  // Kill switch for the one-shot `videoSummaryCache.dedupKey` backfill that
  // runs in `plugins/mongodb.ts`. Defaults OFF because the very first prod
  // deploy on a large collection would otherwise scan-the-world during the
  // onReady hook and stall the health check. Flip ON explicitly for the
  // intentional deploy that performs the migration, then turn it back off.
  // The backfill ALSO now runs off the ready path (setImmediate) so even
  // when enabled it doesn't block readiness.
  RUN_DEDUP_BACKFILL: z.string().default('false').transform(v => v === 'true'),
  // ─── Sentry ─────────────────────────────────────────────────────────
  // Empty DSN disables the SDK entirely — the init helper no-ops and the
  // plugin's hooks become cheap pass-throughs. Lets dev/CI run without
  // provisioning a project.
  SENTRY_DSN: z.string().default(''),
  SENTRY_ENVIRONMENT: z.string().optional(),
  // Set by CI to the deploy SHA so events are linkable to releases.
  SENTRY_RELEASE: z.string().optional(),
  // 0 → performance tracing off (errors still captured). 0.1 in prod is a
  // good starting point; 1.0 in dev to see every transaction while iterating.
  SENTRY_TRACES_SAMPLE_RATE: z.string().default('0').transform(Number),
  // ─── S3 — frame URL re-signing on the read path ──────────────────────
  // Empty bucket → re-signer is a no-op (passes saved URLs through). Set
  // these to the same values the summarizer uses so re-signed URLs land on
  // the same bucket/endpoint as the originals.
  S3_BUCKET: z.string().default(''),
  AWS_REGION: z.string().default('us-east-1'),
  AWS_ACCESS_KEY_ID: z.string().default(''),
  AWS_SECRET_ACCESS_KEY: z.string().default(''),
  // LocalStack endpoint. MUST be browser-reachable (e.g. http://localhost:4566)
  // — the frontend loads images directly from this URL, so a docker-internal
  // hostname will fail to resolve from the browser.
  AWS_ENDPOINT_URL: z.string().default(''),
  // 6h gives a comfortable session window without making links durably shareable.
  FRAME_URL_TTL_SECONDS: z.string().default('21600').transform(Number),
  // ─── HTTP server timeouts ───────────────────────────────────────────
  // Slow-loris protection: max time for a client to deliver the ENTIRE
  // request (headers + body). Applies to the request phase only, so
  // long-lived streaming RESPONSES (SSE) are unaffected. 0 disables.
  HTTP_REQUEST_TIMEOUT_MS: z.string().default('30000').transform(Number),
  // Socket inactivity ceiling (Node's `server.timeout`): a connection with no
  // bytes flowing in either direction for this long is destroyed. SSE routes
  // opt out per-socket via disableSocketInactivityTimeout(); WebSocket
  // sockets are exempted by `ws` itself (setSocket calls setTimeout(0)).
  HTTP_CONNECTION_TIMEOUT_MS: z.string().default('60000').transform(Number),
  // ─── Trust proxy ────────────────────────────────────────────────────
  // OFF by default — `req.ip` returns the direct-TCP-connection address.
  // Behind a CDN / load balancer / ingress, that's the proxy's IP and ALL
  // visitors collapse to one identity (breaks share-view dedup and
  // IP-keyed rate limits). Flip ON in deployments where the upstream is a
  // trusted proxy that sets `X-Forwarded-For`; Fastify will then derive
  // `req.ip` from the rightmost-untrusted hop.
  //
  // Accepts: "true"/"false" (trust any/no upstream), an integer hop count
  // ("1" → trust last hop only), or a comma-separated CIDR list
  // ("10.0.0.0/8,172.16.0.0/12") for a stricter allowlist.
  TRUST_PROXY: z.string().default('false'),
}).superRefine((env, ctx) => {
  if (env.NODE_ENV !== 'production') return;

  const secrets: Array<{ key: string; value: string }> = [
    { key: 'JWT_SECRET', value: env.JWT_SECRET },
    { key: 'JWT_REFRESH_SECRET', value: env.JWT_REFRESH_SECRET },
    { key: 'INTERNAL_SECRET', value: env.INTERNAL_SECRET },
    { key: 'ADMIN_API_KEY', value: env.ADMIN_API_KEY },
  ];
  for (const { key, value } of secrets) {
    if (DEV_DEFAULT_SECRETS[key].includes(value)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: [key],
        message: `${key} is set to a well-known dev default — generate a real secret (openssl rand -base64 32) before deploying to production`,
      });
    }
  }

  // Without a secret the webhook route would accept unverified Paddle events,
  // letting anyone flip user tiers with a curl. Dev/test may skip it; prod may not.
  if (!env.PADDLE_WEBHOOK_SECRET) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['PADDLE_WEBHOOK_SECRET'],
      message: 'PADDLE_WEBHOOK_SECRET is required in production — unsigned webhooks must never be accepted',
    });
  }
});

const parsedConfig = envSchema.parse(process.env);

// Validate URL format
function isValidUrl(url: string): boolean {
  try {
    new URL(url);
    return true;
  } catch {
    return false;
  }
}

// Build allowed origins list from FRONTEND_URL and any additional origins
function buildAllowedOrigins(): string[] {
  const origins = new Set<string>();

  // Always include FRONTEND_URL
  origins.add(parsedConfig.FRONTEND_URL);

  // In development, always allow localhost:5173
  if (parsedConfig.NODE_ENV === 'development') {
    origins.add('http://localhost:5173');
  }

  // Add any additional origins from env (with validation)
  if (parsedConfig.CORS_ADDITIONAL_ORIGINS) {
    const additionalOrigins = parsedConfig.CORS_ADDITIONAL_ORIGINS.split(',')
      .map(origin => origin.trim())
      .filter(Boolean);

    for (const origin of additionalOrigins) {
      if (!isValidUrl(origin)) {
        throw new Error(`Invalid CORS origin URL: ${origin}`);
      }
      origins.add(origin);
    }
  }

  return Array.from(origins);
}

// Test mode rate limit multiplier to avoid test interference
const TEST_RATE_LIMIT_MULTIPLIER = 20;

// Convert TRUST_PROXY env string into the value Fastify's `trustProxy` option
// accepts. We support boolean/integer/string forms with the same precedence as
// Fastify itself so deployments can opt in via the simplest form that works.
function parseTrustProxy(raw: string): boolean | number | string[] {
  const lowered = raw.trim().toLowerCase();
  if (lowered === '' || lowered === 'false' || lowered === '0') return false;
  if (lowered === 'true') return true;
  // Pure integer → number of hops to trust (Fastify accepts this directly).
  if (/^\d+$/.test(lowered)) return Number(lowered);
  // Comma-separated list of CIDRs / IPs → allowlist of trusted upstreams.
  return raw.split(',').map((s) => s.trim()).filter(Boolean);
}

export const config = {
  ...parsedConfig,
  // Canonical pipeline version — packages/shared/src/config/pipeline-version.json.
  PIPELINE_VERSION: loadPipelineVersion(),
  // Computed property: list of all allowed CORS origins
  ALLOWED_ORIGINS: buildAllowedOrigins(),
  // Computed property: Fastify-shaped trustProxy value
  TRUST_PROXY_VALUE: parseTrustProxy(parsedConfig.TRUST_PROXY),
  // Computed rate limits - higher in test mode to avoid interference
  RATE_LIMITS: {
    PLAYLIST_PREVIEW: parsedConfig.NODE_ENV === 'test'
      ? parsedConfig.PLAYLIST_PREVIEW_RATE_LIMIT * TEST_RATE_LIMIT_MULTIPLIER
      : parsedConfig.PLAYLIST_PREVIEW_RATE_LIMIT,
    PLAYLIST_IMPORT: parsedConfig.NODE_ENV === 'test'
      ? parsedConfig.PLAYLIST_IMPORT_RATE_LIMIT * TEST_RATE_LIMIT_MULTIPLIER
      : parsedConfig.PLAYLIST_IMPORT_RATE_LIMIT,
    // Video daily limit (0 = unlimited, useful for dev/admin)
    VIDEO_DAILY: parsedConfig.NODE_ENV === 'test'
      ? parsedConfig.VIDEO_DAILY_LIMIT * TEST_RATE_LIMIT_MULTIPLIER
      : parsedConfig.VIDEO_DAILY_LIMIT,
  },
  // Per-user daily LLM cost limits in USD by tier.
  //   -1 → unlimited (admin/team)
  //    0 → disabled (treated as unlimited)
  //   > 0 → enforced hard ceiling at UTC midnight reset
  COST_LIMITS_PER_TIER: {
    free: parsedConfig.USER_COST_LIMIT_FREE,
    pro: parsedConfig.USER_COST_LIMIT_PRO,
    team: parsedConfig.USER_COST_LIMIT_TEAM,
  } as Record<'free' | 'pro' | 'team', number>,
};
