import { FastifyInstance, FastifyRequest } from 'fastify';
import fp from 'fastify-plugin';
import rateLimit from '@fastify/rate-limit';
import { Redis } from 'ioredis';
import { config } from '../config.js';

export interface RateLimitPluginOptions {
  /** Injected Redis client (tests). Outside test env the plugin creates its
   *  own dedicated connection from `config.REDIS_URL`. */
  redis?: Redis;
  /** Key-prefix override so tests can isolate their counters in shared Redis. */
  nameSpace?: string;
}

/** The RATE_LIMITED envelope every @fastify/rate-limit 429 carries. */
export interface RateLimitedBody {
  error: 'RATE_LIMITED';
  message: string;
  statusCode: 429;
  /** Whole seconds until the window reopens (at least 1); clients localize their copy from it. */
  retryAfterSec: number;
}

/**
 * Build the 429 body for an `errorResponseBuilder`. `ttlMs` is the
 * milliseconds the plugin reports until the window resets (`context.ttl`).
 * Route-level builders use this too, so no RATE_LIMITED answer lacks the wait.
 */
export function rateLimitedBody(message: string, ttlMs: number): RateLimitedBody {
  return {
    error: 'RATE_LIMITED',
    message,
    statusCode: 429,
    retryAfterSec: Math.max(1, Math.ceil(ttlMs / 1000)),
  };
}

/**
 * Per-user rate-limit key. Runs at `preHandler` (see plugin options below),
 * AFTER the route's own auth preHandlers, so `req.user` is populated for
 * authenticated routes and limits are genuinely user-keyed — including across
 * IPs and across API processes (Redis store). Pre-auth routes (login,
 * register, refresh, health) never populate `req.user` and stay IP-keyed,
 * which preserves per-IP brute-force protection on auth endpoints.
 *
 * Prefixes prevent a userId from ever colliding with an IP-shaped key.
 */
function rateLimitKey(req: FastifyRequest): string {
  return req.user?.userId ? `user:${req.user.userId}` : `ip:${req.ip}`;
}

/**
 * Reconnect forever with linear backoff capped at 2s. Never returns null:
 * a null return permanently ends the ioredis client, and with
 * `skipOnError: true` every later store call fails fast — i.e. one ~30s
 * Redis outage would silently disable rate limiting for the remainder of
 * the process lifetime. Exported for regression tests.
 */
export function rateLimitRedisRetryStrategy(times: number): number {
  return Math.min(times * 200, 2000);
}

/**
 * Dedicated Redis connection for the rate-limit store. Separate from the
 * `redisPlugin` client because that plugin registers after this one in
 * app.ts, and a separate connection also keeps INCR bursts from competing
 * with the dispatch-guard's tight latency budget. Fail-fast tuning mirrors
 * `plugins/redis.ts` — combined with `skipOnError: true` below, a Redis
 * outage means unlimited-but-served requests (fail-open, same philosophy as
 * DispatchGuardService), never 500s or hung requests. Crucially the outage
 * must HEAL: see rateLimitRedisRetryStrategy above.
 */
function createRateLimitRedis(fastify: FastifyInstance): Redis {
  const client = new Redis(config.REDIS_URL, {
    lazyConnect: false,
    maxRetriesPerRequest: 0,
    enableOfflineQueue: false,
    commandTimeout: 500,
    retryStrategy: rateLimitRedisRetryStrategy,
  });

  client.on('error', (err) => {
    // Warn, not error — skipOnError makes this non-fatal for user flows.
    fastify.log.warn({ err }, 'rate-limit Redis client error');
  });

  fastify.addHook('onClose', async () => {
    try {
      await client.quit();
    } catch (err) {
      fastify.log.debug({ err }, 'rate-limit Redis quit error (ignored)');
    }
  });

  return client;
}

async function rateLimitSetup(fastify: FastifyInstance, opts: RateLimitPluginOptions) {
  // Test runs share the dev Redis with concurrent suites, and a persistent
  // store would leak 24h-window counters across runs — so tests default to
  // the in-memory store unless they inject a client explicitly. Key semantics
  // (user vs IP) are identical for both stores.
  const redis = opts.redis
    ?? (config.NODE_ENV === 'test' ? undefined : createRateLimitRedis(fastify));

  await fastify.register(rateLimit, {
    global: true,
    max: config.RATE_LIMIT_MAX,
    timeWindow: config.RATE_LIMIT_WINDOW,
    // `preHandler` is the fix for the "per-user limits are fictional" defect:
    // the default `onRequest` hook runs before JWT verification, so
    // `req.user` was always undefined and every limit silently degraded to
    // IP-keyed. The plugin appends its check to the END of each route's
    // preHandler list, i.e. after `fastify.authenticate` /
    // `fastify.authenticateInternal` have populated `req.user`.
    hook: 'preHandler',
    keyGenerator: rateLimitKey,
    // Fail-open on store errors (Redis down/slow) — dropping user submissions
    // over a rate-limit backend outage would be worse than briefly unlimited
    // traffic. Matches the dispatch-guard's fail-open contract.
    skipOnError: true,
    ...(redis ? { redis, nameSpace: opts.nameSpace ?? 'vie:api:rate-limit:' } : {}),
    errorResponseBuilder: (_req, context) => rateLimitedBody('Too many requests', context.ttl),
  });
}

export const rateLimitPlugin = fp(rateLimitSetup);
