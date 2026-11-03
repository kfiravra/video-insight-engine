import { describe, it, expect } from 'vitest';
import { ObjectId } from 'mongodb';
import { SIGNUP_GRANT_TOKENS } from '@vie/types';
import { AccountDraft } from '../account-draft.js';
import {
  applyLotExpiry,
  applyRecurringRun,
  applySignupGrant,
  lotExpiryKey,
  recurringRunAt,
  signupGrantKey,
  staleHoldAction,
} from '../maintenance-ops.js';
import { HOUR_MS, makeAccountDoc, makeLotDoc } from './billing-fixtures.js';
import type { RecurringGrantDocument } from '../../../repositories/billing.repository.js';

const NOW = new Date('2026-11-01T12:00:00Z');
const MAX_AGE = 3 * HOUR_MS;

function grant(createdAt: string, tokens = 10_000): RecurringGrantDocument {
  return {
    _id: new ObjectId(), accountId: new ObjectId(), tokens, every: 'month', active: true,
    nextRunAt: new Date(createdAt), lastRunAt: null, actorId: 'a', auditId: new ObjectId(), reason: 'demo',
    createdAt: new Date(createdAt), updatedAt: new Date(createdAt), deletedAt: null,
  };
}

/** An empty account that owes `debt` tokens. */
function owing(debt: number) {
  return makeAccountDoc({
    buckets: {
      plan: { cycleStart: null, cycleEnd: null, tokensGranted: 0, tokensUsed: 0 },
      credit: { tokensAvailable: 0 },
      debt: { tokens: debt },
    },
  });
}

describe('staleHoldAction', () => {
  const run = (status: 'pending' | 'processing' | 'completed' | 'failed', ageMs = 0) => ({
    status, updatedAt: new Date(NOW.getTime() - ageMs),
  });

  const HELD_AT = new Date(NOW.getTime() - 4 * HOUR_MS);

  it.each([
    ['a missing or unstamped run', null, 'release'],
    ['a run completed after the hold', run('completed', HOUR_MS), 'settle'],
    ['a row completed before the hold — an older run (W2)', run('completed', 5 * HOUR_MS), 'release'],
    ['a failed run', run('failed'), 'release'],
    ['a run still processing', run('processing', HOUR_MS), 'keep'],
    ['a run pending with no write for 3 h (B13)', run('pending', MAX_AGE), 'release'],
  ] as const)('should %s → %s', (_label, state, action) => {
    expect(staleHoldAction(state, NOW, MAX_AGE, HELD_AT)).toBe(action);
  });
});

describe('recurringRunAt', () => {
  it('should grant the first lot at creation, expiring one month later', () => {
    const run = recurringRunAt(grant('2026-01-31T00:00:00Z'), new Date('2026-01-31T00:00:00Z'));

    expect([run.runAt.toISOString(), run.expiresAt.toISOString()]).toEqual(['2026-01-31T00:00:00.000Z', '2026-02-28T00:00:00.000Z']);
  });

  it('should anchor every month on the creation date, so a month-end grant never drifts', () => {
    const run = recurringRunAt(grant('2026-01-31T00:00:00Z'), new Date('2026-03-31T00:00:00Z'));

    expect(run.runAt.toISOString()).toBe('2026-03-31T00:00:00.000Z');
  });

  it('should keep the next run on the month boundary and let the lot outlive it by the grace (I3)', () => {
    const run = recurringRunAt(grant('2026-01-15T00:00:00Z'), new Date('2026-01-15T00:00:00Z'), 600_000);

    expect([run.nextRunAt.toISOString(), run.expiresAt.toISOString()]).toEqual(['2026-02-15T00:00:00.000Z', '2026-02-15T00:10:00.000Z']);
  });

  it('should skip idle months: one lot for the month containing now', () => {
    const g = grant('2026-01-15T00:00:00Z');
    const run = recurringRunAt(g, new Date('2026-06-20T00:00:00Z'));

    expect([run.runAt.toISOString(), run.expiresAt.toISOString()]).toEqual(['2026-06-15T00:00:00.000Z', '2026-07-15T00:00:00.000Z']);
    expect(run.idempotencyKey).toBe(`recurring:${g._id.toHexString()}:2026-06-15T00:00:00.000Z`);
  });
});

describe('applyRecurringRun', () => {
  it('should grant the month\'s lot in tokens and repay debt from it first', () => {
    const account = owing(400);
    const g = grant('2026-11-01T12:00:00Z');
    const run = recurringRunAt(g, NOW);
    const draft = new AccountDraft(account, [], NOW);

    const lotId = applyRecurringRun(draft, g, run);

    expect(draft.newLots).toEqual([expect.objectContaining({ _id: lotId, tokensGranted: 10_000, tokensRemaining: 9_600, expiresAt: run.expiresAt })]);
    expect(draft.entries.map((e) => [e.type, e.bucket, e.tokens, e.idempotencyKey])).toEqual([
      ['grant', 'credit', 10_000, run.idempotencyKey],
      ['repay', 'credit', -400, `repay:${run.idempotencyKey}:lot:${lotId.toHexString()}`],
      ['repay', 'debt', 400, `repay:${run.idempotencyKey}`],
    ]);
    expect([draft.account.buckets.debt.tokens, draft.account.buckets.credit.tokensAvailable]).toEqual([0, 9_600]);
  });
});

describe('applySignupGrant', () => {
  it('should write one never-expiring lot of SIGNUP_GRANT_TOKENS keyed by the owner and stamp the account', () => {
    const account = makeAccountDoc();
    const draft = new AccountDraft(account, [], NOW);

    const lotId = applySignupGrant(draft);

    expect(SIGNUP_GRANT_TOKENS).toBe(1000);
    expect(draft.newLots).toEqual([expect.objectContaining({
      _id: lotId, tokensGranted: 1000, tokensRemaining: 1000, expiresAt: null, source: { kind: 'system', ref: 'signup' },
    })]);
    expect(draft.entries).toEqual([expect.objectContaining({
      type: 'grant', bucket: 'credit', unit: 'tokens', tokens: 1000, lotId,
      idempotencyKey: signupGrantKey(account.ownerId), source: { kind: 'system', ref: 'signup' },
    })]);
    expect(signupGrantKey(account.ownerId)).toBe(`grant:signup:${account.ownerId.toHexString()}`);
    expect([draft.account.signupGrantAt, draft.account.buckets.credit.tokensAvailable]).toEqual([NOW, 1000]);
  });

  it('should repay debt first on an account that owes (a migrated one)', () => {
    const draft = new AccountDraft(owing(250), [], NOW);

    applySignupGrant(draft);

    expect(draft.account.buckets.debt.tokens).toBe(0);
    expect(draft.newLots[0].tokensRemaining).toBe(750);
  });
});

describe('applyLotExpiry', () => {
  it('should expire only open lots past their expiry, and none already expired', () => {
    const account = makeAccountDoc({
      buckets: {
        plan: { cycleStart: null, cycleEnd: null, tokensGranted: 0, tokensUsed: 0 },
        credit: { tokensAvailable: 900 },
        debt: { tokens: 0 },
      },
    });
    const due = makeLotDoc(account._id, { tokensRemaining: 400, expiresAt: new Date(NOW.getTime() - 1) });
    const done = makeLotDoc(account._id, { tokensRemaining: 200, expiresAt: new Date(NOW.getTime() - 1) });
    const live = makeLotDoc(account._id, { tokensRemaining: 300, expiresAt: new Date(NOW.getTime() + HOUR_MS) });
    const draft = new AccountDraft(account, [due, done, live], NOW);

    const expired = applyLotExpiry(draft, new Set([lotExpiryKey(done._id)]));

    expect(expired).toBe(400);
    expect(draft.entries.map((e) => [e.type, e.tokens, e.idempotencyKey])).toEqual([['expire', -400, lotExpiryKey(due._id)]]);
    expect(draft.account.buckets.credit.tokensAvailable).toBe(500);
  });
});
