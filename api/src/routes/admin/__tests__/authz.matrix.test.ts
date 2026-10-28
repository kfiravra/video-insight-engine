import { createHash } from 'node:crypto';
import net from 'node:net';
import type { AddressInfo } from 'node:net';
import { describe, it, expect, beforeAll, beforeEach, afterAll, afterEach, vi } from 'vitest';
import type { FastifyInstance, HTTPMethods, RouteOptions } from 'fastify';
import { READ_PERMISSIONS } from '@vie/types';
import type { Permission } from '@vie/types';

import { buildApp } from '../../../app.js';
import type { Container } from '../../../container.js';
import { config } from '../../../config.js';
import type { AdminGuard } from '../../../plugins/admin-auth.js';
import { signActorHeader } from '../../../utils/admin-actor.js';
import { NotFoundError } from '../../../utils/errors.js';
import { adminActorRequest, createMockContainer, TEST_ADMIN_USER_ID, type MockContainer } from '../../../test/helpers.js';

/**
 * Authorization matrix for every `/api/admin/*` route. The routes are not
 * listed here: they are collected from Fastify as they are registered, so a
 * route added without `requireAdmin` fails these tests the day it is added.
 */

interface AdminRoute {
  method: string;
  /** Registered path with its `:params`. */
  path: string;
  /** Path with every `:param` replaced by a well-formed placeholder. */
  url: string;
  /** What `requireAdmin` was called with; undefined for a route that does not use it. */
  permission: Permission | undefined;
}

const PLACEHOLDER_ID = 'a'.repeat(24);
const ADMIN_PREFIX = '/api/admin/';

function guardPermission(preHandler: RouteOptions['preHandler']): Permission | undefined {
  const hooks = Array.isArray(preHandler) ? preHandler : preHandler ? [preHandler] : [];
  const guard = hooks.find((hook): hook is AdminGuard => 'requiredPermission' in hook);
  return guard?.requiredPermission;
}

function toAdminRoutes(registered: RouteOptions[]): AdminRoute[] {
  return registered
    .filter((route) => route.url.startsWith(ADMIN_PREFIX))
    .flatMap((route) => (Array.isArray(route.method) ? route.method : [route.method]).map((method) => ({ route, method })))
    // Fastify adds a HEAD twin to every GET; it shares the GET route's guard.
    .filter(({ method }) => method !== 'HEAD')
    .map(({ route, method }) => ({
      method,
      path: route.url,
      url: route.url.replace(/:\w+/g, PLACEHOLDER_ID),
      permission: guardPermission(route.preHandler),
    }));
}

const label = (route: AdminRoute): string => `${route.method} ${route.path}`;
const isMutation = (route: AdminRoute): boolean => route.method !== 'GET';
const sha256Hex = (text: string): string => createHash('sha256').update(text).digest('hex');
const nowSeconds = (): number => Math.floor(Date.now() / 1000);

/** A request the test sends in place of the route's own well-formed one. */
interface RequestPlan {
  headers?: Record<string, string>;
  payload?: string;
  url?: string;
  method?: HTTPMethods;
}

/** Spellings of an admin URL that Fastify routes the same but whose raw text differs. */
const OBFUSCATED_ADMIN_PREFIXES = ['/api/%61dmin/', '/api/%41dmin/', '/api//admin/', '/api/./admin/', '/%61pi/admin/'];

/**
 * A header an attacker could hold: valid for `method` + `target` and for an
 * EMPTY body, i.e. what a captured read looks like. Sent with a body, it must
 * never authorise anything.
 */
function emptyBodyHeaders(method: string, target: string): Record<string, string> {
  const actor = signActorHeader(
    config.ADMIN_SESSION_SECRET,
    TEST_ADMIN_USER_ID,
    { method, target, bodySha256: sha256Hex('') },
    nowSeconds(),
  );
  return { 'x-admin-key': config.ADMIN_API_KEY, 'x-admin-actor': actor, 'content-type': 'application/json' };
}

/** Send one HTTP/1.1 request with a hand-written request line (inject() would normalise it) and return the status. */
function rawStatus(port: number, requestLine: string, headers: Record<string, string>, body: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1');
    let received = '';
    socket.setEncoding('utf8');
    socket.on('data', (chunk: string) => {
      received += chunk;
    });
    socket.on('error', reject);
    socket.on('close', () => {
      const status = /^HTTP\/1\.1 (\d{3})/.exec(received)?.[1];
      if (status === undefined) reject(new Error(`no status line in: ${received.slice(0, 80)}`));
      else resolve(Number(status));
    });
    const head = Object.entries({ host: `127.0.0.1:${port}`, connection: 'close', 'content-length': String(Buffer.byteLength(body)), ...headers })
      .map(([name, value]) => `${name}: ${value}`);
    socket.write([requestLine, ...head, '', body].join('\r\n'));
  });
}

describe('admin authorization matrix', () => {
  let app: FastifyInstance;
  let mocks: MockContainer;
  let routes: AdminRoute[];

  beforeAll(async () => {
    const registered: RouteOptions[] = [];
    mocks = createMockContainer();
    mocks.userDeletionService.requestDeletion.mockResolvedValue({
      userId: PLACEHOLDER_ID,
      scheduledHardDeleteAt: new Date(),
      graceDays: 30,
    });
    // The one route that reaches a service with a well-formed placeholder id.
    const billingService = { getAccountDetail: vi.fn().mockRejectedValue(new NotFoundError('User')) };
    app = await buildApp({
      logger: false,
      container: { ...mocks, billingService } as unknown as Partial<Container>,
      onRoute: (route) => registered.push(route),
    });
    await app.ready();
    routes = toAdminRoutes(registered);
  });

  beforeEach(() => {
    mocks.userRepository.hasPermission.mockResolvedValue(true);
    mocks.adminReplayGuard.claim.mockResolvedValue(true);
    // queue/stats and queue/dlq call RabbitMQ's management API; nothing is listening in tests.
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  afterAll(async () => {
    await app?.close();
  });

  /** Send one request to every admin route and list the routes whose status breaks `expected`. */
  async function violations(
    request: (route: AdminRoute) => RequestPlan,
    expected: (status: number, route: AdminRoute) => boolean,
    only: (route: AdminRoute) => boolean = () => true,
  ): Promise<string[]> {
    const failures: string[] = [];
    for (const route of routes.filter(only)) {
      const res = await app.inject({ method: route.method as HTTPMethods, url: route.url, ...request(route) });
      if (!expected(res.statusCode, route)) failures.push(`${label(route)} answered ${res.statusCode}`);
    }
    return failures;
  }

  const machineKey = () => ({ headers: { 'x-admin-key': config.ADMIN_API_KEY } });
  /** What vie-admin sends: signed over the method, the exact target and the body (GETs carry none, mutations a JSON one). */
  const signedAdmin = (route: AdminRoute, options: { nowSeconds?: number } = {}) =>
    adminActorRequest(route.method, route.url, isMutation(route) ? {} : undefined, options);

  it('should find the admin routes this matrix is meant to guard', () => {
    const prefixes = ['queue', 'users', 'videos', 'billing', 'audit'];

    expect(prefixes.filter((prefix) => !routes.some((route) => route.path.startsWith(`${ADMIN_PREFIX}${prefix}`)))).toEqual([]);
    expect(routes.length).toBeGreaterThanOrEqual(10);
  });

  it('should guard every admin route with requireAdmin(<permission>)', () => {
    expect(routes.filter((route) => route.permission === undefined).map(label)).toEqual([]);
  });

  it('should let the shared key reach exactly the read routes: GETs use read permissions, mutations never do', () => {
    const offenders = routes
      .filter((route) => route.permission !== undefined && READ_PERMISSIONS.has(route.permission) === isMutation(route))
      .map((route) => `${label(route)} requires ${route.permission}`);

    expect(offenders).toEqual([]);
  });

  it('should answer 401 to a request with no credentials', async () => {
    expect(await violations(() => ({}), (status) => status === 401)).toEqual([]);
  });

  it('should answer 401 to a wrong admin key', async () => {
    expect(await violations(() => ({ headers: { 'x-admin-key': 'not-the-key' } }), (status) => status === 401)).toEqual([]);
  });

  it('should answer 401 to a signed actor header sent without the admin key', async () => {
    const withoutKey = (route: AdminRoute) => {
      const { headers, payload } = signedAdmin(route);
      const { 'x-admin-key': _key, ...rest } = headers;
      return { headers: rest, payload };
    };

    expect(await violations(withoutKey, (status) => status === 401)).toEqual([]);
  });

  it('should answer 403 to the shared machine key on every mutation', async () => {
    expect(await violations(machineKey, (status) => status === 403, isMutation)).toEqual([]);
  });

  it('should let the shared machine key read: neither 401 nor 403 on a GET', async () => {
    expect(await violations(machineKey, (status) => status !== 401 && status !== 403, (route) => !isMutation(route))).toEqual([]);
  });

  it('should let a signed admin through: neither 401 nor 403', async () => {
    expect(await violations(signedAdmin, (status) => status !== 401 && status !== 403)).toEqual([]);
  });

  it('should answer 403 to a signed admin who no longer holds the permission', async () => {
    mocks.userRepository.hasPermission.mockResolvedValue(false);

    expect(await violations(signedAdmin, (status) => status === 403)).toEqual([]);
  });

  it('should check each route\'s own permission for the signed admin', async () => {
    const asked: string[] = [];
    mocks.userRepository.hasPermission.mockImplementation(async (_userId: string, permission: string) => {
      asked.push(permission);
      return true;
    });

    await violations(signedAdmin, () => true);

    expect(asked.sort()).toEqual(routes.map((route) => route.permission as string).sort());
  });

  it('should answer 401 to a stale actor header', async () => {
    const stale = (route: AdminRoute) => signedAdmin(route, { nowSeconds: nowSeconds() - 301 });

    expect(await violations(stale, (status) => status === 401)).toEqual([]);
  });

  describe('a signature is bound to one request', () => {
    it('should answer 401 to a header signed for a read of the same URL, on every mutation', async () => {
      const readHeaders = (route: AdminRoute) => ({ headers: adminActorRequest('GET', route.url).headers });

      expect(await violations(readHeaders, (status) => status === 401, isMutation)).toEqual([]);
    });

    it('should answer 401 to a header signed for another route', async () => {
      const otherRoute = (route: AdminRoute) => ({ headers: adminActorRequest(route.method, `${route.url}/other`).headers });

      expect(await violations(otherRoute, (status) => status === 401)).toEqual([]);
    });

    it('should answer 401 when a query string is added after signing, on every route', async () => {
      const withQuery = (route: AdminRoute) => ({ ...signedAdmin(route), url: `${route.url}?immediate=true` });

      expect(await violations(withQuery, (status) => status === 401)).toEqual([]);
    });

    it('should answer 401 when the body is changed after signing, on every mutation', async () => {
      const swapped = (route: AdminRoute) => ({
        headers: signedAdmin(route).headers,
        payload: JSON.stringify({ minutes: 99999, reason: 'swapped' }),
      });

      expect(await violations(swapped, (status) => status === 401, isMutation)).toEqual([]);
    });

    it('should answer 401 when a body is added to a request signed without one', async () => {
      const added = (route: AdminRoute) => ({
        headers: { ...adminActorRequest(route.method, route.url).headers, 'content-type': 'application/json' },
        payload: JSON.stringify({ minutes: 99999 }),
      });

      expect(await violations(added, (status) => status === 401, isMutation)).toEqual([]);
    });

    it('should answer 401 to a mutation whose signature was already used', async () => {
      mocks.adminReplayGuard.claim.mockResolvedValue(false);

      expect(await violations(signedAdmin, (status) => status === 401, isMutation)).toEqual([]);
    });

    it('should not spend a signature on a read: GETs never ask the replay guard', async () => {
      await violations(signedAdmin, () => true, (route) => !isMutation(route));

      expect(mocks.adminReplayGuard.claim).not.toHaveBeenCalled();
    });

    it('should answer 503 to a mutation when the replay guard is unavailable, never letting it through', async () => {
      const { ServiceUnavailableError } = await import('../../../utils/errors.js');
      mocks.adminReplayGuard.claim.mockRejectedValue(new ServiceUnavailableError('replay guard unavailable'));

      expect(await violations(signedAdmin, (status) => status === 503, isMutation)).toEqual([]);
    });
  });

  describe('a different spelling of the URL does not skip the check', () => {
    const spelled = (route: AdminRoute, prefix: string): string => route.url.replace(ADMIN_PREFIX, prefix);

    it.each(OBFUSCATED_ADMIN_PREFIXES)('should never authorise a body on a mutation sent to %s with an empty-body signature', async (prefix) => {
      const smuggled = (route: AdminRoute) => {
        const url = spelled(route, prefix);
        return { url, headers: emptyBodyHeaders(route.method, url), payload: JSON.stringify({ minutes: 99999 }) };
      };

      const failures = await violations(smuggled, (status) => status === 401 || status === 404, isMutation);

      expect(failures).toEqual([]);
    });

    it.each(OBFUSCATED_ADMIN_PREFIXES)('should never let a read header act as a mutation on %s', async (prefix) => {
      const replayed = (route: AdminRoute) => {
        const url = spelled(route, prefix);
        return { url, headers: adminActorRequest('GET', url).headers };
      };

      expect(await violations(replayed, (status) => status === 401 || status === 404, isMutation)).toEqual([]);
    });

    describe('over a real socket (absolute-form request target)', () => {
      let port: number;

      beforeAll(async () => {
        await app.listen({ port: 0, host: '127.0.0.1' });
        port = (app.server.address() as AddressInfo).port;
      });

      it('should answer 401 to a body sent with an empty-body signature, on every mutation', async () => {
        const failures: string[] = [];
        for (const route of routes.filter(isMutation)) {
          const target = `http://127.0.0.1:${port}${route.url}`;
          const body = JSON.stringify({ minutes: 99999 });

          const status = await rawStatus(port, `${route.method} ${target} HTTP/1.1`, emptyBodyHeaders(route.method, target), body);

          if (status !== 401) failures.push(`${label(route)} answered ${status}`);
        }
        expect(failures).toEqual([]);
      });

      it('should answer 401 to a header signed for the bare path when the target arrives in absolute form', async () => {
        const failures: string[] = [];
        for (const route of routes.filter(isMutation)) {
          const signed = adminActorRequest(route.method, route.url, {});
          const target = `http://127.0.0.1:${port}${route.url}`;

          const status = await rawStatus(port, `${route.method} ${target} HTTP/1.1`, signed.headers, signed.payload ?? '');

          if (status !== 401) failures.push(`${label(route)} answered ${status}`);
        }
        expect(failures).toEqual([]);
      });

      it('should accept a request signed over the absolute target it actually sent', async () => {
        const [route] = routes.filter(isMutation);
        const target = `http://127.0.0.1:${port}${route.url}`;
        const signed = adminActorRequest(route.method, target, {});

        const status = await rawStatus(port, `${route.method} ${target} HTTP/1.1`, signed.headers, signed.payload ?? '');

        expect([401, 403]).not.toContain(status);
      });
    });
  });
});
