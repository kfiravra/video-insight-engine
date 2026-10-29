import { beforeAll, beforeEach, afterAll, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger, FastifyInstance, LightMyRequestResponse } from 'fastify';
import type { Document, WithId } from 'mongodb';

import { buildApp } from '../../../app.js';
import type { Container } from '../../../container.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { AdminAuditRepository } from '../../../repositories/admin-audit.repository.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { AuditService } from '../../../services/audit.service.js';
import { BillingService } from '../../../services/billing/billing.service.js';
import { insertUser } from '../../../services/billing/__tests__/billing-fixtures.js';
import { adminActorRequest, createMockContainer, type MockContainer } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';

/**
 * The app the admin billing route suites share: the real BillingService, audit
 * service and in-memory database behind the real routes, with the permission
 * check and the replay guard mocked. Call `useBillingRoutesApp()` inside a
 * `describe`; it registers the hooks that build and tear the app down.
 */

export type Method = 'GET' | 'POST' | 'DELETE';

export const BASE = '/api/admin/billing';

export interface BillingRoutesContext {
  /** Built in `beforeAll`: read it from a hook or a test, not while the suite is being declared. */
  readonly app: FastifyInstance;
  readonly billing: BillingService;
  /** A fresh user for each test, set in `beforeEach`. */
  readonly userId: string;
  readonly mocks: MockContainer;
  readonly logger: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn>; error: ReturnType<typeof vi.fn>; debug: ReturnType<typeof vi.fn> };
  /** Send `method path` the way vie-admin does: the shared key plus an actor header signed for exactly this request. */
  send: (method: Method, path: string, body?: Record<string, unknown>, extraHeaders?: Record<string, string>) => Promise<LightMyRequestResponse>;
  post: (path: string, body: Record<string, unknown>, headers?: Record<string, string>) => Promise<LightMyRequestResponse>;
  auditRows: () => Promise<Array<WithId<Document>>>;
  ledgerFor: (auditId: string) => Promise<Array<WithId<Document>>>;
}

function createLogger() {
  return { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };
}

export function useBillingRoutesApp(): BillingRoutesContext {
  const mocks = createMockContainer();
  const logger = createLogger();
  let app: FastifyInstance;
  let billing: BillingService;
  let userId: string;

  beforeAll(async () => {
    const db = getTestDb();
    await createBillingIndexes(db);
    const log = logger as unknown as FastifyBaseLogger;
    billing = new BillingService(new BillingRepository(db), new UserRepository(db), log);
    const auditService = new AuditService(new AdminAuditRepository(db), log);
    app = await buildApp({
      logger: false,
      container: { ...mocks, billingService: billing, auditService } as unknown as Partial<Container>,
    });
    await app.ready();
  });

  beforeEach(async () => {
    mocks.userRepository.hasPermission.mockResolvedValue(true);
    mocks.adminReplayGuard.claim.mockResolvedValue(true);
    userId = await insertUser(getTestDb());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  afterAll(async () => {
    await app?.close();
  });

  const send: BillingRoutesContext['send'] = async (method, path, body, extraHeaders = {}) => {
    const url = `${BASE}${path}`;
    const request = adminActorRequest(method, url, body);
    return app.inject({ method, url, headers: { ...request.headers, ...extraHeaders }, payload: request.payload });
  };

  return {
    get app() {
      return app;
    },
    get billing() {
      return billing;
    },
    get userId() {
      return userId;
    },
    mocks,
    logger,
    send,
    post: (path, body, headers = {}) => send('POST', path, body, headers),
    auditRows: () => getTestDb().collection('adminAudit').find({}).toArray(),
    ledgerFor: (auditId) => getTestDb().collection('ledgerEntries').find({ 'source.ref': auditId }).toArray(),
  };
}
