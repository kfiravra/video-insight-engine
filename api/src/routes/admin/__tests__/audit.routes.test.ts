import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';

import { buildApp } from '../../../app.js';
import type { Container } from '../../../container.js';
import { config } from '../../../config.js';
import { AdminAuditRepository } from '../../../repositories/admin-audit.repository.js';
import { AuditService } from '../../../services/audit.service.js';
import { adminActorRequest, createMockContainer, TEST_ADMIN_USER_ID, type MockContainer } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';

const URL = '/api/admin/audit';
const OTHER_ADMIN = 'b1b2c3d4e5f60718293a4b5c';
const USER_A = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const USER_B = 'bbbbbbbbbbbbbbbbbbbbbbbb';

describe('GET /api/admin/audit', () => {
  let app: FastifyInstance;
  let mocks: MockContainer;
  let now: Date;
  let audit: AuditService;

  beforeAll(async () => {
    mocks = createMockContainer();
    const silent = { info: () => undefined, warn: () => undefined, error: () => undefined } as unknown as FastifyBaseLogger;
    audit = new AuditService(new AdminAuditRepository(getTestDb()), silent, () => now);
    app = await buildApp({ logger: false, container: { ...mocks, auditService: audit } as unknown as Partial<Container> });
    await app.ready();
  });

  beforeEach(() => {
    mocks.userRepository.hasPermission.mockResolvedValue(true);
  });

  afterAll(async () => {
    await app?.close();
  });

  async function seed(actorId: string, targetId: string, minute: number): Promise<void> {
    now = new Date(Date.UTC(2026, 10, 1, 10, minute));
    await audit.record({ actorId, action: 'billing.credits.grant', targetType: 'user', targetId, reason: `at ${minute}` });
  }

  const get = (query = '', request = adminActorRequest()) =>
    app.inject({ method: 'GET', url: `${URL}${query}`, headers: request.headers });

  it('should answer 401 without credentials', async () => {
    const res = await app.inject({ method: 'GET', url: URL });

    expect(res.statusCode).toBe(401);
  });

  it('should answer 403 to an admin without audit:read', async () => {
    mocks.userRepository.hasPermission.mockResolvedValue(false);

    const res = await get();

    expect(res.statusCode).toBe(403);
  });

  it('should check the actor against audit:read', async () => {
    await get();

    expect(mocks.userRepository.hasPermission).toHaveBeenCalledWith(TEST_ADMIN_USER_ID, 'audit:read');
  });

  it('should be readable with the shared machine key', async () => {
    const res = await app.inject({ method: 'GET', url: URL, headers: { 'x-admin-key': config.ADMIN_API_KEY } });

    expect(res.statusCode).toBe(200);
  });

  it('should list entries newest first as views', async () => {
    await seed(TEST_ADMIN_USER_ID, USER_A, 0);
    await seed(TEST_ADMIN_USER_ID, USER_A, 5);

    const { entries } = (await get()).json();

    expect(entries.map((entry: { reason: string }) => entry.reason)).toEqual(['at 5', 'at 0']);
    expect(entries[0]).toMatchObject({ id: expect.stringMatching(/^[a-f0-9]{24}$/), createdAt: '2026-11-01T10:05:00.000Z' });
  });

  it('should filter by target to give one user\'s history', async () => {
    await seed(TEST_ADMIN_USER_ID, USER_A, 0);
    await seed(TEST_ADMIN_USER_ID, USER_B, 1);

    const { entries } = (await get(`?targetId=${USER_B}`)).json();

    expect(entries.map((entry: { targetId: string }) => entry.targetId)).toEqual([USER_B]);
  });

  it('should filter by actor', async () => {
    await seed(TEST_ADMIN_USER_ID, USER_A, 0);
    await seed(OTHER_ADMIN, USER_A, 1);

    const { entries } = (await get(`?actorId=${OTHER_ADMIN}`)).json();

    expect(entries.map((entry: { actorId: string }) => entry.actorId)).toEqual([OTHER_ADMIN]);
  });

  it('should honour the limit and hand back a cursor when more remain', async () => {
    await seed(TEST_ADMIN_USER_ID, USER_A, 0);
    await seed(TEST_ADMIN_USER_ID, USER_A, 1);
    await seed(TEST_ADMIN_USER_ID, USER_A, 2);

    const page = (await get('?limit=2')).json();

    expect(page.entries).toHaveLength(2);
    expect(page.nextBefore).toBe('2026-11-01T10:01:00.000Z');
  });

  it('should page older rows with the cursor and end with a null cursor', async () => {
    await seed(TEST_ADMIN_USER_ID, USER_A, 0);
    await seed(TEST_ADMIN_USER_ID, USER_A, 1);
    await seed(TEST_ADMIN_USER_ID, USER_A, 2);
    const first = (await get('?limit=2')).json();

    const second = (await get(`?limit=2&before=${encodeURIComponent(first.nextBefore)}`)).json();

    expect(second.entries.map((entry: { reason: string }) => entry.reason)).toEqual(['at 0']);
    expect(second.nextBefore).toBeNull();
  });

  it.each([['limit=0'], ['limit=201'], ['limit=abc'], ['before=yesterday']])('should reject %s with 400', async (query) => {
    const res = await get(`?${query}`);

    expect(res.statusCode).toBe(400);
  });
});
