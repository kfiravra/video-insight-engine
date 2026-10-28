import { ObjectId } from 'mongodb';
import type { Db } from 'mongodb';
import type { BillingAccountDocument, CreditLotDocument } from '../../../repositories/billing.repository.js';

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
