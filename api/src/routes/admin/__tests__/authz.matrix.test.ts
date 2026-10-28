import { describe, it, expect, beforeAll, beforeEach, afterAll, afterEach, vi } from 'vitest';
import type { FastifyInstance, RouteOptions } from 'fastify';
import { READ_PERMISSIONS } from '@vie/types';
import type { Permission } from '@vie/types';

import { buildApp } from '../../../app.js';
import type { Container } from '../../../container.js';
import { config } from '../../../config.js';
import type { AdminGuard } from '../../../plugins/admin-auth.js';
import { NotFoundError } from '../../../utils/errors.js';
import { adminActorRequest, createMockContainer, type MockContainer } from '../../../test/helpers.js';

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
    request: (route: AdminRoute) => { headers?: Record<string, string>; payload?: string },
    expected: (status: number, route: AdminRoute) => boolean,
    only: (route: AdminRoute) => boolean = () => true,
  ): Promise<string[]> {
    const failures: string[] = [];
    for (const route of routes.filter(only)) {
      const res = await app.inject({ method: route.method as 'GET', url: route.url, ...request(route) });
      if (!expected(res.statusCode, route)) failures.push(`${label(route)} answered ${res.statusCode}`);
    }
    return failures;
  }

  const machineKey = () => ({ headers: { 'x-admin-key': config.ADMIN_API_KEY } });
  /** A GET carries no body, so vie-admin signs it over the empty body; mutations carry a JSON body. */
  const signedAdmin = (route: AdminRoute) => (isMutation(route) ? adminActorRequest({}) : adminActorRequest());

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
    const stale = () => adminActorRequest({}, { nowSeconds: Math.floor(Date.now() / 1000) - 301 });

    expect(await violations(stale, (status) => status === 401)).toEqual([]);
  });
});
