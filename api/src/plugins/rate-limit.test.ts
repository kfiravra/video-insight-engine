import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import Fastify, { FastifyInstance } from 'fastify';
import { Redis } from 'ioredis';
import { randomUUID } from 'node:crypto';
import { rateLimitPlugin, type RateLimitPluginOptions } from './rate-limit.js';
import { jwtPlugin } from './jwt.js';
import { config } from '../config.js';

/**
 * Builds a standalone app around the REAL rateLimitPlugin (not an inline
 * re-registration) so these tests exercise production semantics: the
 * `preHandler` hook ordering, the user-vs-IP keyGenerator, and per-route
 * config merging — exactly what POST /api/videos' daily cap relies on.
 */
async function buildRateLimitedApp(opts: RateLimitPluginOptions = {}): Promise<FastifyInstance> {
  const app = Fastify({ logger: false });
  await app.register(jwtPlugin);
  await app.register(rateLimitPlugin, opts);

  // Authenticated route with a tight per-route limit (per-minute window).
  app.get('/user-limited', {
    preHandler: [app.authenticate],
    config: { rateLimit: { max: 2, timeWindow: '1 minute' } },
  }, async () => ({ status: 'ok' }));

  // Authenticated route with a 24-hour window — the exact config shape of the
  // daily video cost cap on POST /api/videos.
  app.post('/daily-capped', {
    preHandler: [app.authenticate],
    config: { rateLimit: { max: 2, timeWindow: '24 hours' } },
  }, async () => ({ status: 'ok' }));

  // Pre-auth route (login/register shape): no authenticate preHandler, tight
  // per-route brute-force limit.
  app.get('/public-limited', {
    config: { rateLimit: { max: 2, timeWindow: '1 minute' } },
  }, async () => ({ status: 'ok' }));

  // Public route on the global limit, for header assertions.
  app.get('/test', async () => ({ status: 'ok' }));

  await app.ready();
  return app;
}

function bearerFor(app: FastifyInstance, userId: string): string {
  return `Bearer ${app.jwt.sign({ userId, email: `${userId}@example.com` })}`;
}

/** Random RFC1918 address so parallel/repeated runs never share an IP key. */
function randomIp(): string {
  const octet = () => Math.floor(Math.random() * 254) + 1;
  return `10.${octet()}.${octet()}.${octet()}`;
}

describe('Rate Limit plugin', () => {
  describe('per-user keying', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      app = await buildRateLimitedApp();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should give user B a fresh quota when user A on the SAME IP is exhausted', async () => {
      // This is the regression test for the audit finding "per-user limits
      // are fictional": with the old onRequest-hooked keyGenerator, req.user
      // was never populated, both users collapsed onto the shared IP key,
      // and user B's first request below would have been 429.
      const userA = `user-${randomUUID()}`;
      const userB = `user-${randomUUID()}`;
      const sharedIp = randomIp();

      for (let i = 0; i < 2; i++) {
        const ok = await app.inject({
          method: 'GET',
          url: '/user-limited',
          headers: { authorization: bearerFor(app, userA) },
          remoteAddress: sharedIp,
        });
        expect(ok.statusCode).toBe(200);
      }

      const exhausted = await app.inject({
        method: 'GET',
        url: '/user-limited',
        headers: { authorization: bearerFor(app, userA) },
        remoteAddress: sharedIp,
      });
      expect(exhausted.statusCode).toBe(429);

      const freshUser = await app.inject({
        method: 'GET',
        url: '/user-limited',
        headers: { authorization: bearerFor(app, userB) },
        remoteAddress: sharedIp,
      });
      expect(freshUser.statusCode).toBe(200);
      // Fresh quota, not the tail end of A's: remaining must be max - 1.
      expect(freshUser.headers['x-ratelimit-remaining']).toBe('1');
    });

    it('should keep counting the same user across DIFFERENT IPs', async () => {
      // User-keyed means rotating IPs no longer resets the quota.
      const userA = `user-${randomUUID()}`;

      for (let i = 0; i < 2; i++) {
        const ok = await app.inject({
          method: 'GET',
          url: '/user-limited',
          headers: { authorization: bearerFor(app, userA) },
          remoteAddress: randomIp(),
        });
        expect(ok.statusCode).toBe(200);
      }

      const thirdIp = await app.inject({
        method: 'GET',
        url: '/user-limited',
        headers: { authorization: bearerFor(app, userA) },
        remoteAddress: randomIp(),
      });
      expect(thirdIp.statusCode).toBe(429);
    });

    it('should key the 24-hour daily video cap per user, not per IP', async () => {
      // Same config shape as POST /api/videos' daily cost cap.
      const userA = `user-${randomUUID()}`;
      const userB = `user-${randomUUID()}`;
      const sharedIp = randomIp();

      for (let i = 0; i < 2; i++) {
        const ok = await app.inject({
          method: 'POST',
          url: '/daily-capped',
          headers: { authorization: bearerFor(app, userA) },
          remoteAddress: sharedIp,
        });
        expect(ok.statusCode).toBe(200);
      }

      const capped = await app.inject({
        method: 'POST',
        url: '/daily-capped',
        headers: { authorization: bearerFor(app, userA) },
        remoteAddress: sharedIp,
      });
      expect(capped.statusCode).toBe(429);

      const otherUser = await app.inject({
        method: 'POST',
        url: '/daily-capped',
        headers: { authorization: bearerFor(app, userB) },
        remoteAddress: sharedIp,
      });
      expect(otherUser.statusCode).toBe(200);
    });
  });

  describe('IP keying for pre-auth routes', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      app = await buildRateLimitedApp();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should return 429 when one IP exceeds a pre-auth route limit', async () => {
      const ip = randomIp();

      for (let i = 0; i < 2; i++) {
        const ok = await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });
        expect(ok.statusCode).toBe(200);
      }

      const blocked = await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });
      expect(blocked.statusCode).toBe(429);
    });

    it('should give a different IP its own quota on pre-auth routes', async () => {
      const ipA = randomIp();
      const ipB = randomIp();

      for (let i = 0; i < 3; i++) {
        await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ipA });
      }

      const otherIp = await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ipB });
      expect(otherIp.statusCode).toBe(200);
    });
  });

  describe('global config and headers', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      app = await buildRateLimitedApp();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should allow requests within the global rate limit', async () => {
      const response = await app.inject({ method: 'GET', url: '/test', remoteAddress: randomIp() });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ status: 'ok' });
    });

    it('should expose the configured global max in rate limit headers', async () => {
      const response = await app.inject({ method: 'GET', url: '/test', remoteAddress: randomIp() });

      expect(response.headers).toHaveProperty('x-ratelimit-remaining');
      const limit = parseInt(String(response.headers['x-ratelimit-limit']), 10);
      expect(limit).toBe(config.RATE_LIMIT_MAX);
    });
  });

  describe('error response format', () => {
    let app: FastifyInstance;

    beforeAll(async () => {
      app = await buildRateLimitedApp();
    });

    afterAll(async () => {
      await app.close();
    });

    it('should return the standardized error body when rate limited', async () => {
      const ip = randomIp();
      for (let i = 0; i < 2; i++) {
        await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });
      }

      const response = await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });

      expect(response.statusCode).toBe(429);
      expect(response.json()).toEqual({
        error: 'RATE_LIMITED',
        message: 'Too many requests',
        statusCode: 429,
        retryAfterSec: expect.any(Number),
      });
    });

    it('should tell the client how many whole seconds are left in the window', async () => {
      const ip = randomIp();
      for (let i = 0; i < 2; i++) {
        await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });
      }

      const response = await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });

      const { retryAfterSec } = response.json();
      expect(Number.isInteger(retryAfterSec) && retryAfterSec >= 1 && retryAfterSec <= 60).toBe(true);
    });

    it('should send the same seconds a 24 hour window has left', async () => {
      const token = bearerFor(app, `retry-after-${randomUUID()}`);
      for (let i = 0; i < 2; i++) {
        await app.inject({ method: 'POST', url: '/daily-capped', headers: { authorization: token } });
      }

      const response = await app.inject({ method: 'POST', url: '/daily-capped', headers: { authorization: token } });

      expect(response.json().retryAfterSec).toBeGreaterThan(23 * 60 * 60);
    });

    it('should include a retry-after header when rate limited', async () => {
      const ip = randomIp();
      for (let i = 0; i < 2; i++) {
        await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });
      }

      const response = await app.inject({ method: 'GET', url: '/public-limited', remoteAddress: ip });

      expect(response.headers).toHaveProperty('retry-after');
    });
  });

  describe('Redis store', () => {
    let app: FastifyInstance;
    let redis: Redis;

    beforeAll(async () => {
      // Unique namespace so counters never collide with other suites/runs
      // sharing the dev Redis; 1-minute-window keys expire on their own.
      redis = new Redis(config.REDIS_URL);
      app = await buildRateLimitedApp({
        redis,
        nameSpace: `vie:test:rate-limit:${randomUUID()}:`,
      });
    });

    afterAll(async () => {
      await app.close();
      await redis.quit();
    });

    it('should enforce per-user limits through the Redis store', async () => {
      const userA = `user-${randomUUID()}`;
      const userB = `user-${randomUUID()}`;
      const sharedIp = randomIp();

      for (let i = 0; i < 2; i++) {
        const ok = await app.inject({
          method: 'GET',
          url: '/user-limited',
          headers: { authorization: bearerFor(app, userA) },
          remoteAddress: sharedIp,
        });
        expect(ok.statusCode).toBe(200);
      }

      // A 429 proves the Redis store actually counted (the fail-open path
      // can never produce a 429).
      const exhausted = await app.inject({
        method: 'GET',
        url: '/user-limited',
        headers: { authorization: bearerFor(app, userA) },
        remoteAddress: sharedIp,
      });
      expect(exhausted.statusCode).toBe(429);

      const freshUser = await app.inject({
        method: 'GET',
        url: '/user-limited',
        headers: { authorization: bearerFor(app, userB) },
        remoteAddress: sharedIp,
      });
      expect(freshUser.statusCode).toBe(200);
    });

    it('should fail open and serve requests when Redis is unreachable', async () => {
      // Client aimed at a closed port with the offline queue disabled: every
      // command rejects immediately, exercising skipOnError.
      const deadRedis = new Redis('redis://localhost:6399', {
        lazyConnect: true,
        maxRetriesPerRequest: 0,
        enableOfflineQueue: false,
        retryStrategy: () => null,
      });
      // Swallow connection errors — the fail-open contract makes them expected.
      deadRedis.on('error', () => undefined);

      const failOpenApp = await buildRateLimitedApp({
        redis: deadRedis,
        nameSpace: `vie:test:rate-limit:${randomUUID()}:`,
      });

      try {
        const userA = `user-${randomUUID()}`;
        const ip = randomIp();

        // Well past max (2) — every request must still be served.
        for (let i = 0; i < 4; i++) {
          const response = await failOpenApp.inject({
            method: 'GET',
            url: '/user-limited',
            headers: { authorization: bearerFor(failOpenApp, userA) },
            remoteAddress: ip,
          });
          expect(response.statusCode).toBe(200);
        }
      } finally {
        await failOpenApp.close();
        deadRedis.disconnect();
      }
    });
  });

  describe('Redis retry strategy', () => {
    it('should never abort reconnection — an outage must heal instead of disabling rate limiting for the process lifetime', async () => {
      const { rateLimitRedisRetryStrategy } = await import('./rate-limit.js');

      // Regression: `times > 20 → null` permanently ended the client, and
      // with skipOnError:true every later store call failed fast — one ~30s
      // Redis outage silently turned rate limiting off until restart.
      for (const times of [1, 10, 21, 500, 10_000]) {
        const delay = rateLimitRedisRetryStrategy(times);
        expect(delay).toBeGreaterThan(0);
        expect(delay).toBeLessThanOrEqual(2000);
      }
    });
  });
});
