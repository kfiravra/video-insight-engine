import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import Fastify from 'fastify';
import type { FastifyInstance } from 'fastify';

import { adminAuthPlugin } from './admin-auth.js';
import type { Container } from '../container.js';
import { config } from '../config.js';
import { sha256Hex, signActorHeader } from '../utils/admin-actor.js';
import { ServiceUnavailableError } from '../utils/errors.js';
import { resolveErrorEnvelope } from '../utils/error-envelope.js';

const USER_ID = 'a1b2c3d4e5f60718293a4b5c';
const BODY = { max: 5 };

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

interface Signing {
  method: string;
  target: string;
  body?: string;
  ts?: number;
  secret?: string;
  userId?: string;
}

function actorHeader({ method, target, body = '', ts = nowSeconds(), secret = config.ADMIN_SESSION_SECRET, userId = USER_ID }: Signing): string {
  return signActorHeader(secret, userId, { method, target, bodySha256: sha256Hex(body) }, ts);
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
  const claim = vi.fn();
  const handler = vi.fn();

  beforeAll(async () => {
    app = Fastify({ logger: false });
    app.decorate('container', {
      userRepository: { hasPermission },
      adminReplayGuard: { claim },
    } as unknown as Container);
    app.setErrorHandler((error, _request, reply) => {
      const { statusCode, body } = resolveErrorEnvelope(error);
      return reply.status(statusCode).send(body);
    });
    await app.register(adminAuthPlugin);

    // Hides the actor header from preParsing and restores it for the preHandler, so a body is
    // parsed that the plugin never got to hash: the fail-closed path.
    app.addHook('onRequest', async (req) => {
      if (req.headers['x-test-hide-actor'] !== undefined) {
        req.headers['x-test-held-actor'] = req.headers['x-admin-actor'];
        delete req.headers['x-admin-actor'];
      }
    });
    app.addHook('preValidation', async (req) => {
      if (req.headers['x-test-held-actor'] !== undefined) req.headers['x-admin-actor'] = req.headers['x-test-held-actor'];
    });

    const echo = async (req: { adminActor: unknown; body: unknown }) => {
      handler();
      return { actor: req.adminActor, body: req.body };
    };
    app.get('/api/admin/read', { preHandler: [app.requireAdmin('queue:read')] }, echo);
    app.post('/api/admin/replay', { preHandler: [app.requireAdmin('queue:replay')] }, echo);
    app.delete('/api/admin/purge', { preHandler: [app.requireAdmin('videos:purge')] }, echo);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    hasPermission.mockReset();
    hasPermission.mockResolvedValue(true);
    claim.mockReset();
    claim.mockResolvedValue(true);
    handler.mockReset();
  });

  async function post(payload: string, actor?: string, key?: string | null, url = '/api/admin/replay') {
    return app.inject({
      method: 'POST',
      url,
      headers: { 'content-type': 'application/json', ...headers(actor, key) },
      payload,
    });
  }

  const postActor = (payload: string, signing: Partial<Signing> = {}) =>
    post(payload, actorHeader({ method: 'POST', target: '/api/admin/replay', body: payload, ...signing }));

  describe('a signed actor header', () => {
    it('should make the request that user\'s, with their parsed body intact', async () => {
      const payload = JSON.stringify(BODY);

      const res = await postActor(payload);

      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ actor: { kind: 'user', userId: USER_ID }, body: BODY });
    });

    it('should check the actor still holds the route\'s permission', async () => {
      await postActor(JSON.stringify(BODY));

      expect(hasPermission).toHaveBeenCalledWith(USER_ID, 'queue:replay');
    });

    it('should work for a DELETE that carries a JSON body, as the purge proxy sends it', async () => {
      const payload = JSON.stringify({ reason: 'duplicate upload' });

      const res = await app.inject({
        method: 'DELETE',
        url: '/api/admin/purge',
        headers: { 'content-type': 'application/json', ...headers(actorHeader({ method: 'DELETE', target: '/api/admin/purge', body: payload })) },
        payload,
      });

      expect(res.json()).toMatchObject({ actor: { kind: 'user', userId: USER_ID }, body: { reason: 'duplicate upload' } });
    });

    it('should work for a GET with no body, signed over the empty body', async () => {
      const res = await app.inject({
        method: 'GET',
        url: '/api/admin/read',
        headers: headers(actorHeader({ method: 'GET', target: '/api/admin/read' })),
      });

      expect(res.json()).toEqual({ actor: { kind: 'user', userId: USER_ID } });
    });

    it('should be refused when the actor was demoted since vie-admin signed it', async () => {
      hasPermission.mockResolvedValue(false);

      const res = await postActor(JSON.stringify(BODY));

      expect(res.statusCode).toBe(403);
    });

    it('should be refused with 401 when it is more than five minutes old', async () => {
      const res = await postActor(JSON.stringify(BODY), { ts: nowSeconds() - 301 });

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when signed with another secret', async () => {
      const res = await postActor(JSON.stringify(BODY), { secret: config.ADMIN_API_KEY });

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when the body was changed after signing', async () => {
      const signedFor = JSON.stringify({ max: 5 });

      const res = await post(JSON.stringify({ max: 500 }), actorHeader({ method: 'POST', target: '/api/admin/replay', body: signedFor }));

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when its user id was swapped', async () => {
      const payload = JSON.stringify(BODY);
      const [, ts, signature] = actorHeader({ method: 'POST', target: '/api/admin/replay', body: payload }).split('.');

      const res = await post(payload, `${'0'.repeat(24)}.${ts}.${signature}`);

      expect(res.statusCode).toBe(401);
    });

    it('should be refused with 401 when malformed', async () => {
      const res = await post(JSON.stringify(BODY), 'not-a-header');

      expect(res.statusCode).toBe(401);
    });

    it('should not reach the permission check when the signature is bad', async () => {
      await postActor(JSON.stringify(BODY), { secret: 'wrong' });

      expect(hasPermission).not.toHaveBeenCalled();
    });

    it('should be refused with 401 when a body is sent that Fastify never parses', async () => {
      // No content-type: the app sees no body, so a header signed over one cannot match.
      const payload = JSON.stringify({ reason: 'x' });

      const res = await app.inject({
        method: 'DELETE',
        url: '/api/admin/purge',
        headers: headers(actorHeader({ method: 'DELETE', target: '/api/admin/purge', body: payload })),
        payload,
      });

      expect(res.statusCode).toBe(401);
    });

    it('should not stand in for the shared key', async () => {
      const payload = JSON.stringify(BODY);

      const res = await post(payload, actorHeader({ method: 'POST', target: '/api/admin/replay', body: payload }), null);

      expect(res.statusCode).toBe(401);
    });
  });

  describe('bound to the request it was signed for', () => {
    it('should refuse a header signed for a GET when it arrives on a POST', async () => {
      const captured = actorHeader({ method: 'GET', target: '/api/admin/replay' });

      const res = await post(JSON.stringify(BODY), captured);

      expect(res.statusCode).toBe(401);
      expect(handler).not.toHaveBeenCalled();
    });

    it('should refuse a header signed for one path when it arrives on another', async () => {
      const captured = actorHeader({ method: 'GET', target: '/api/admin/read' });

      const res = await app.inject({ method: 'DELETE', url: '/api/admin/purge', headers: headers(captured) });

      expect(res.statusCode).toBe(401);
      expect(handler).not.toHaveBeenCalled();
    });

    it('should refuse a header signed without a query string when one is added', async () => {
      const captured = actorHeader({ method: 'GET', target: '/api/admin/read' });

      const res = await app.inject({ method: 'GET', url: '/api/admin/read?immediate=true', headers: headers(captured) });

      expect(res.statusCode).toBe(401);
    });

    it('should accept a header signed for the query string it arrives with', async () => {
      const target = '/api/admin/read?targetId=abc&limit=2';

      const res = await app.inject({ method: 'GET', url: target, headers: headers(actorHeader({ method: 'GET', target })) });

      expect(res.statusCode).toBe(200);
    });

    it('should refuse a header signed for the canonical path on its percent-encoded twin', async () => {
      // Fastify decodes %61 and routes the request to the same handler; the signature must not follow.
      const payload = JSON.stringify(BODY);
      const captured = actorHeader({ method: 'POST', target: '/api/admin/replay', body: payload });

      const res = await post(payload, captured, undefined, '/api/%61dmin/replay');

      expect(res.statusCode).toBe(401);
      expect(handler).not.toHaveBeenCalled();
    });

    it('should hash the body whatever form of the URL carries it', async () => {
      // A request legitimately signed for the encoded target is accepted with its real body ...
      const payload = JSON.stringify(BODY);
      const target = '/api/%61dmin/replay';
      const ok = await post(payload, actorHeader({ method: 'POST', target, body: payload }), undefined, target);
      // ... and the same header does not cover a different body on that URL.
      const tampered = await post(JSON.stringify({ max: 500 }), actorHeader({ method: 'POST', target, body: payload }), undefined, target);

      expect([ok.statusCode, tampered.statusCode]).toEqual([200, 401]);
    });

    it('should fail closed when a body was parsed that the plugin never hashed', async () => {
      const payload = JSON.stringify(BODY);
      const res = await app.inject({
        method: 'POST',
        url: '/api/admin/replay',
        headers: {
          'content-type': 'application/json',
          'x-test-hide-actor': '1',
          ...headers(actorHeader({ method: 'POST', target: '/api/admin/replay', body: payload })),
        },
        payload,
      });

      expect(res.statusCode).toBe(401);
      expect(handler).not.toHaveBeenCalled();
    });
  });

  describe('single use of a mutation signature', () => {
    it('should claim the signature of a POST once, with its remaining validity as the TTL', async () => {
      const payload = JSON.stringify(BODY);
      const header = actorHeader({ method: 'POST', target: '/api/admin/replay', body: payload });

      await post(payload, header);

      expect(claim).toHaveBeenCalledTimes(1);
      const [signature, ttl] = claim.mock.calls[0] as [string, number];
      expect(signature).toBe(header.split('.')[2]);
      expect(ttl).toBeGreaterThanOrEqual(300);
      expect(ttl).toBeLessThanOrEqual(302);
    });

    it('should keep the record for the full window of a header dated in the future', async () => {
      const payload = JSON.stringify(BODY);

      await postActor(payload, { ts: nowSeconds() + 250 });

      const [, ttl] = claim.mock.calls[0] as [string, number];
      expect(ttl).toBeGreaterThanOrEqual(550);
    });

    it('should claim a DELETE signature too', async () => {
      const payload = JSON.stringify({ reason: 'x' });

      await app.inject({
        method: 'DELETE',
        url: '/api/admin/purge',
        headers: { 'content-type': 'application/json', ...headers(actorHeader({ method: 'DELETE', target: '/api/admin/purge', body: payload })) },
        payload,
      });

      expect(claim).toHaveBeenCalledTimes(1);
    });

    it('should not claim a GET: reads may repeat', async () => {
      await app.inject({
        method: 'GET',
        url: '/api/admin/read',
        headers: headers(actorHeader({ method: 'GET', target: '/api/admin/read' })),
      });

      expect(claim).not.toHaveBeenCalled();
    });

    it('should refuse a replayed signature with 401 and run nothing', async () => {
      claim.mockResolvedValue(false);

      const res = await postActor(JSON.stringify(BODY));

      expect(res.statusCode).toBe(401);
      expect(handler).not.toHaveBeenCalled();
    });

    it('should fail closed with 503 when the replay record cannot be kept', async () => {
      claim.mockRejectedValue(new ServiceUnavailableError('Replay protection'));

      const res = await postActor(JSON.stringify(BODY));

      expect(res.statusCode).toBe(503);
      expect(handler).not.toHaveBeenCalled();
    });

    it('should not claim a signature that failed verification', async () => {
      await postActor(JSON.stringify(BODY), { secret: 'wrong' });

      expect(claim).not.toHaveBeenCalled();
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

    it('should never consult the database or the replay record for the machine actor', async () => {
      await app.inject({ method: 'GET', url: '/api/admin/read', headers: headers() });

      expect([hasPermission.mock.calls.length, claim.mock.calls.length]).toEqual([0, 0]);
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
