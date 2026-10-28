import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import Fastify from 'fastify';
import type { FastifyInstance } from 'fastify';

import { adminAuthPlugin } from './admin-auth.js';
import type { Container } from '../container.js';
import { config } from '../config.js';
import { sha256Hex, signActorHeader } from '../utils/admin-actor.js';
import { resolveErrorEnvelope } from '../utils/error-envelope.js';

const USER_ID = 'a1b2c3d4e5f60718293a4b5c';
const BODY = { max: 5 };

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function actorHeader(body: string, options: { ts?: number; secret?: string; userId?: string } = {}): string {
  const { ts = nowSeconds(), secret = config.ADMIN_SESSION_SECRET, userId = USER_ID } = options;
  return signActorHeader(secret, userId, sha256Hex(body), ts);
}

/** `key: null` sends no `x-admin-key` at all. */
function headers(actor?: string, key: string | null = config.ADMIN_API_KEY): Record<string, string> {
  return {
    ...(key === null ? {} : { 'x-admin-key': key }),
    ...(actor === undefined ? {} : { 'x-admin-actor': actor }),
  };
}

describe('admin-auth plugin', () => {
  let app: FastifyInstance;
  const hasPermission = vi.fn();

  beforeAll(async () => {
    app = Fastify({ logger: false });
    app.decorate('container', { userRepository: { hasPermission } } as unknown as Container);
    app.setErrorHandler((error, _request, reply) => {
      const { statusCode, body } = resolveErrorEnvelope(error);
      return reply.status(statusCode).send(body);
    });
    await app.register(adminAuthPlugin);

    app.get('/api/admin/read', { preHandler: [app.requireAdmin('queue:read')] }, async (req) => ({
      actor: req.adminActor,
    }));
    app.post('/api/admin/replay', { preHandler: [app.requireAdmin('queue:replay')] }, async (req) => ({
      actor: req.adminActor,
      body: req.body,
    }));
    app.delete('/api/admin/purge', { preHandler: [app.requireAdmin('videos:purge')] }, async (req) => ({
      actor: req.adminActor,
      body: req.body,
    }));
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    hasPermission.mockReset();
    hasPermission.mockResolvedValue(true);
  });

  async function post(payload: string, actor?: string, key?: string | null) {
    return app.inject({
      method: 'POST',
      url: '/api/admin/replay',
      headers: { 'content-type': 'application/json', ...headers(actor, key) },
      payload,
    });
  }

  describe('a signed actor header', () => {
    it('should make the request that user\'s, with their parsed body intact', async () => {
      const payload = JSON.stringify(BODY);

      const res = await post(payload, actorHeader(payload));

      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ actor: { kind: 'user', userId: USER_ID }, body: BODY });
    });

    it('should check the actor still holds the route\'s permission', async () => {
      const payload = JSON.stringify(BODY);

      await post(payload, actorHeader(payload));

      expect(hasPermission).toHaveBeenCalledWith(USER_ID, 'queue:replay');
    });

    it('should work for a DELETE that carries a JSON body, as the purge proxy sends it', async () => {
      const payload = JSON.stringify({ reason: 'duplicate upload' });

      const res = await app.inject({
        method: 'DELETE',
        url: '/api/admin/purge',
        headers: { 'content-type': 'application/json', ...headers(actorHeader(payload)) },
        payload,
      });

      expect(res.json()).toMatchObject({ actor: { kind: 'user', userId: USER_ID }, body: { reason: 'duplicate upload' } });
    });

    it('should work for a GET with no body, signed over the empty body', async () => {
      const res = await app.inject({ method: 'GET', url: '/api/admin/read', headers: headers(actorHeader('')) });

      expect(res.json()).toEqual({ actor: { kind: 'user', userId: USER_ID } });
    });

    it('should be refused when the actor was demoted since vie-admin signed it', async () => {
      hasPermission.mockResolvedValue(false);
      const payload = JSON.stringify(BODY);

      const res = await post(payload, actorHeader(payload));

      expect(res.statusCode).toBe(403);
    });

    it('should be refused with 401 when it is more than five minutes old', async () => {
      const payload = JSON.stringify(BODY);

      const res = await post(payload, actorHeader(payload, { ts: nowSeconds() - 301 }));

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when signed with another secret', async () => {
      const payload = JSON.stringify(BODY);

      const res = await post(payload, actorHeader(payload, { secret: config.ADMIN_API_KEY }));

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when the body was changed after signing', async () => {
      const signedFor = JSON.stringify({ max: 5 });

      const res = await post(JSON.stringify({ max: 500 }), actorHeader(signedFor));

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when its user id was swapped', async () => {
      const payload = JSON.stringify(BODY);
      const [, ts, signature] = actorHeader(payload).split('.');

      const res = await post(payload, `${'0'.repeat(24)}.${ts}.${signature}`);

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when malformed', async () => {
      const res = await post(JSON.stringify(BODY), 'not-a-header');

      expect(res.statusCode).toBe(401);
    });

    it('should not reach the permission check when the signature is bad', async () => {
      await post(JSON.stringify(BODY), actorHeader('{}', { secret: 'wrong' }));

      expect(hasPermission).not.toHaveBeenCalled();
    });

    it('should be refused with 401 when a body is sent that Fastify never parses', async () => {
      // No content-type: the app sees no body, so a header signed over one cannot match.
      const payload = JSON.stringify({ reason: 'x' });

      const res = await app.inject({
        method: 'DELETE',
        url: '/api/admin/purge',
        headers: headers(actorHeader(payload)),
        payload,
      });

      expect(res.statusCode).toBe(401);
    });

    it('should not stand in for the shared key', async () => {
      const payload = JSON.stringify(BODY);

      const res = await post(payload, actorHeader(payload), null);

      expect(res.statusCode).toBe(401);
    });
  });

  describe('the shared machine key', () => {
    it('should read: 200 on a read permission, acting as the machine', async () => {
      const res = await app.inject({ method: 'GET', url: '/api/admin/read', headers: headers() });

      expect(res.json()).toEqual({ actor: { kind: 'machine' } });
    });

    it('should not mutate: 403 on a write permission', async () => {
      const res = await post(JSON.stringify(BODY));

      expect(res.statusCode).toBe(403);
    });

    it('should not mutate: 403 on a DELETE', async () => {
      const res = await app.inject({ method: 'DELETE', url: '/api/admin/purge', headers: headers() });

      expect(res.statusCode).toBe(403);
    });

    it('should never consult the database for the machine actor', async () => {
      await app.inject({ method: 'GET', url: '/api/admin/read', headers: headers() });

      expect(hasPermission).not.toHaveBeenCalled();
    });

    it('should be refused with 401 when the key is wrong', async () => {
      const res = await app.inject({ method: 'GET', url: '/api/admin/read', headers: headers(undefined, 'wrong') });

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when no key is sent', async () => {
      const res = await app.inject({ method: 'GET', url: '/api/admin/read' });

      expect(res.statusCode).toBe(401);
    });
  });
});
