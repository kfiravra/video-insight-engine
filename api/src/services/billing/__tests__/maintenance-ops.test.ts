import { describe, it, expect } from 'vitest';
import { ObjectId } from 'mongodb';
import { AccountDraft } from '../account-draft.js';
import { applyLotExpiry, lotExpiryKey, recurringRunAt, staleHoldAction } from '../maintenance-ops.js';
import { HOUR_MS, makeAccountDoc, makeLotDoc } from './billing-fixtures.js';
import type { RecurringGrantDocument } from '../../../repositories/billing.repository.js';

const NOW = new Date('2026-11-01T12:00:00Z');
const MAX_AGE = 3 * HOUR_MS;

describe('staleHoldAction', () => {
  const run = (status: 'pending' | 'processing' | 'completed' | 'failed', ageMs = 0) => ({
    status, updatedAt: new Date(NOW.getTime() - ageMs),
  });

  it.each([
    ['a missing or unstamped run', null, 'release'],
    ['a completed run', run('completed'), 'settle'],
    ['a failed run', run('failed'), 'release'],
    ['a run still processing', run('processing', HOUR_MS), 'keep'],
    ['a run pending with no write for 3 h (B13)', run('pending', MAX_AGE), 'release'],
  ] as const)('should %s → %s', (_label, state, action) => {
    expect(staleHoldAction(state, NOW, MAX_AGE)).toBe(action);
  });
});

describe('recurringRunAt', () => {
  const grant = (createdAt: string): RecurringGrantDocument => ({
    _id: new ObjectId(), accountId: new ObjectId(), minutes: 300, every: 'month', active: true,
    nextRunAt: new Date(createdAt), lastRunAt: null, actorId: 'a', auditId: new ObjectId(), reason: 'demo',
    createdAt: new Date(createdAt), updatedAt: new Date(createdAt), deletedAt: null,
  });

  it('should grant the first lot at creation, expiring one month later', () => {
    const run = recurringRunAt(grant('2026-01-31T00:00:00Z'), new Date('2026-01-31T00:00:00Z'));

    expect([run.runAt.toISOString(), run.expiresAt.toISOString()]).toEqual(['2026-01-31T00:00:00.000Z', '2026-02-28T00:00:00.000Z']);
  });

  it('should anchor every month on the creation date, so a month-end grant never drifts', () => {
    const run = recurringRunAt(grant('2026-01-31T00:00:00Z'), new Date('2026-03-31T00:00:00Z'));

    expect(run.runAt.toISOString()).toBe('2026-03-31T00:00:00.000Z');
  });

  it('should skip idle months: one lot for the month containing now', () => {
    const run = recurringRunAt(grant('2026-01-15T00:00:00Z'), new Date('2026-06-20T00:00:00Z'));

    expect([run.runAt.toISOString(), run.expiresAt.toISOString()]).toEqual(['2026-06-15T00:00:00.000Z', '2026-07-15T00:00:00.000Z']);
  });
});

describe('applyLotExpiry', () => {
  it('should expire only open lots past their expiry, and none already expired', () => {
    const account = makeAccountDoc({ buckets: { free: { recentFreeVideoAt: [] }, plan: { cycleStart: null, cycleEnd: null, minutesGranted: 0, minutesUsed: 0 }, credit: { minutesAvailable: 90 } } });
    const due = makeLotDoc(account._id, { minutesRemaining: 40, expiresAt: new Date(NOW.getTime() - 1) });
    const done = makeLotDoc(account._id, { minutesRemaining: 20, expiresAt: new Date(NOW.getTime() - 1) });
    const live = makeLotDoc(account._id, { minutesRemaining: 30, expiresAt: new Date(NOW.getTime() + HOUR_MS) });
    const draft = new AccountDraft(account, [due, done, live], NOW);

    const expired = applyLotExpiry(draft, new Set([lotExpiryKey(done._id)]));

    expect(expired).toBe(40);
    expect(draft.entries.map((e) => [e.type, e.minutes, e.idempotencyKey])).toEqual([['expire', -40, lotExpiryKey(due._id)]]);
    expect(draft.account.buckets.credit.minutesAvailable).toBe(50);
  });
});
