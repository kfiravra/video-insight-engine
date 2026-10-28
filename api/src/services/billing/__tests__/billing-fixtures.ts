import { vi } from 'vitest';
import { ObjectId } from 'mongodb';
import { mockLogger } from '../../../test/setup.js';
import { BillingRepository } from '../../../repositories/billing.repository.js';
import { BillingShadowRepository } from '../../../repositories/billing-shadow.repository.js';
import { LlmUsageRepository } from '../../../repositories/llm-usage.repository.js';
import { UserRepository } from '../../../repositories/user.repository.js';
import { BillingGate } from '../billing-gate.js';
import { BillingShadowService } from '../billing-shadow.service.js';
import { BillingService } from '../billing.service.js';
import { LedgerEnforcement } from '../ledger-enforcement.js';
import { ShadowRunner } from '../shadow-runner.js';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import type { BillingAccountDocument, CreditLotDocument } from '../../../repositories/billing.repository.js';
import type { BillingShadowDocument } from '../../../repositories/billing-shadow.repository.js';
import type { DispatchStampReader } from '../billing-shadow.service.js';

/** A free account with empty buckets; override any part. */
export function makeAccountDoc(overrides: Partial<BillingAccountDocument> = {}): BillingAccountDocument {
  const now = new Date('2026-11-01T00:00:00Z');
  return {
    _id: new ObjectId(),
    ownerType: 'user',
    ownerId: new ObjectId(),
    plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
    buckets: {
      free: { recentFreeVideoAt: [] },
      plan: { cycleStart: null, cycleEnd: null, minutesGranted: 0, minutesUsed: 0 },
      credit: { minutesAvailable: 0 },
    },
    holds: [],
    caps: {},
    version: 0,
    createdAt: now,
    updatedAt: now,
    ...overrides,
  };
}

export function makeLotDoc(accountId: ObjectId, overrides: Partial<CreditLotDocument> = {}): CreditLotDocument {
  return {
    _id: new ObjectId(),
    accountId,
    minutesGranted: 100,
    minutesRemaining: 100,
    source: { kind: 'admin', ref: new ObjectId().toHexString() },
    expiresAt: null,
    createdAt: new Date('2026-10-01T00:00:00Z'),
    ...overrides,
  };
}

/** A settable clock for services under test. */
export interface TestClock {
  now: () => Date;
  set: (iso: string | Date) => void;
  advance: (ms: number) => void;
}

export function makeClock(start: string): TestClock {
  let current = new Date(start);
  return {
    now: () => new Date(current.getTime()),
    set: (iso) => {
      current = new Date(iso);
    },
    advance: (ms) => {
      current = new Date(current.getTime() + ms);
    },
  };
}

/** Insert a bare user row so the admin API's user check passes. */
export async function insertUser(db: Db): Promise<string> {
  const _id = new ObjectId();
  await db.collection('users').insertOne({
    _id,
    email: `user-${_id.toHexString()}@example.com`,
    name: 'Test User',
    createdAt: new Date(),
    updatedAt: new Date(),
  });
  return _id.toHexString();
}

export const HOUR_MS = 60 * 60 * 1000;
export const DAY_MS = 24 * HOUR_MS;

/** A real, enabled shadow ledger on `db` with stubbed metadata and dispatch stamps. */
export interface ShadowHarness {
  shadow: BillingShadowService;
  billing: BillingService;
  gate: BillingGate;
  runner: ShadowRunner;
  metadata: { getVideoMetadata: ReturnType<typeof vi.fn> };
  /** videoSummaryId → the requestId its dispatch stamped on the cache row. */
  stamps: Map<string, string>;
  rowOf: (requestId: string) => Promise<BillingShadowDocument | null>;
}

export interface ShadowHarnessOptions {
  enabled?: boolean;
  clock?: () => Date;
  /** Read stamps from here (e.g. a real VideoRepository) instead of the `stamps` map. */
  dispatchStamps?: DispatchStampReader;
}

export function makeShadowHarness(db: Db, options: ShadowHarnessOptions = {}): ShadowHarness {
  const { enabled = true, clock = () => new Date() } = options;
  const logger = mockLogger as unknown as FastifyBaseLogger;
  const billing = new BillingService(new BillingRepository(db), new UserRepository(db), logger, clock);
  const metadata = { getVideoMetadata: vi.fn().mockResolvedValue({ duration: 600 }) };
  const stamps = new Map<string, string>();
  const runner = new ShadowRunner(logger);
  const rows = new BillingShadowRepository(db);
  const gate = new BillingGate(billing, metadata);
  const shadow = new BillingShadowService({
    enabled,
    gate,
    billing,
    rows,
    llmUsage: new LlmUsageRepository(db),
    dispatchStamps: options.dispatchStamps ?? {
      findCacheById: async (id: string) => (stamps.has(id) ? { requestId: stamps.get(id) } : null),
    },
    runner,
    logger,
  });
  return { shadow, billing, gate, runner, metadata, stamps, rowOf: (requestId) => rows.findByRequestId(requestId) };
}

/** BILLING_LEDGER_ENFORCE on, over the harness's billing service and gate; stamps from `dispatchStamps` or the map. */
export function makeEnforcement(db: Db, h: ShadowHarness, dispatchStamps?: DispatchStampReader): LedgerEnforcement {
  return new LedgerEnforcement({
    billing: h.billing,
    gate: h.gate,
    llmUsage: new LlmUsageRepository(db),
    dispatchStamps: dispatchStamps ?? {
      findCacheById: async (id: string) => (h.stamps.has(id) ? { requestId: h.stamps.get(id) } : null),
    },
    logger: mockLogger as unknown as FastifyBaseLogger,
  });
}
