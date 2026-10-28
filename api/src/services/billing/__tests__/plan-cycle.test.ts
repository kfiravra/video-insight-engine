import { describe, it, expect } from 'vitest';
import { AccountDraft } from '../account-draft.js';
import { planAvailable, rollPlanCycle } from '../plan-cycle.js';
import { makeAccountDoc } from './billing-fixtures.js';
import type { BillingAccountDocument } from '../../../repositories/billing.repository.js';

const TERM_START = new Date('2026-01-15T00:00:00Z');

/** A manual Pro account in its first cycle with `used` of 600 minutes spent. */
function proAccount(used: number, overrides: Partial<BillingAccountDocument['plan']> = {}): BillingAccountDocument {
  return makeAccountDoc({
    plan: { id: 'pro', status: 'active', source: 'manual', periodStart: TERM_START, periodEnd: new Date('2027-01-15T00:00:00Z'), ...overrides },
    buckets: {
      free: { recentFreeVideoAt: [] },
      plan: { cycleStart: TERM_START, cycleEnd: new Date('2026-02-15T00:00:00Z'), minutesGranted: 600, minutesUsed: used },
      credit: { minutesAvailable: 0 },
    },
  });
}

function rollAt(account: BillingAccountDocument, iso: string): AccountDraft {
  const draft = new AccountDraft(account, [], new Date(iso));
  rollPlanCycle(draft);
  return draft;
}

describe('rollPlanCycle', () => {
  it('should leave a current cycle alone', () => {
    const draft = rollAt(proAccount(100), '2026-02-14T23:59:59Z');

    expect(draft.dirty).toBe(false);
  });

  it('should reset the plan to 600 at the next cycle with no rollover (Q5)', () => {
    const draft = rollAt(proAccount(100), '2026-02-15T00:00:00Z');

    expect(draft.account.buckets.plan).toEqual({
      cycleStart: new Date('2026-02-15T00:00:00Z'),
      cycleEnd: new Date('2026-03-15T00:00:00Z'),
      minutesGranted: 600,
      minutesUsed: 0,
    });
    expect(planAvailable(draft.account, draft.now)).toBe(600);
  });

  it('should expire the unused remainder of the ended cycle in the ledger', () => {
    const draft = rollAt(proAccount(100), '2026-02-15T00:00:00Z');

    expect(draft.entries.map((e) => [e.type, e.bucket, e.minutes])).toEqual([
      ['expire', 'plan', -500],
      ['grant', 'plan', 600],
    ]);
  });

  it('should jump straight to the cycle containing now after idle months', () => {
    const draft = rollAt(proAccount(0), '2026-06-20T00:00:00Z');

    expect(draft.account.buckets.plan.cycleStart).toEqual(new Date('2026-06-15T00:00:00Z'));
    expect(draft.entries.filter((e) => e.type === 'grant')).toHaveLength(1);
  });

  it('should key each transition by its cycle so concurrent rollers write it once', () => {
    const account = proAccount(100);
    const keys = (draft: AccountDraft) => draft.entries.map((e) => e.idempotencyKey);

    expect(keys(rollAt(account, '2026-02-15T00:00:00Z'))).toEqual(keys(rollAt(account, '2026-02-20T00:00:00Z')));
  });

  it('should clip the last cycle to the end of the term', () => {
    const draft = rollAt(proAccount(0, { periodEnd: new Date('2026-03-01T00:00:00Z') }), '2026-02-20T00:00:00Z');

    expect(draft.account.buckets.plan.cycleEnd).toEqual(new Date('2026-03-01T00:00:00Z'));
  });

  it('should revert to free and expire the remainder when the term ends', () => {
    const draft = rollAt(proAccount(50, { periodEnd: new Date('2026-02-01T00:00:00Z') }), '2026-02-01T00:00:00Z');

    expect(draft.account.plan.id).toBe('free');
    expect(planAvailable(draft.account, draft.now)).toBe(0);
    expect(draft.entries.map((e) => [e.type, e.minutes])).toEqual([['expire', -550]]);
  });

  it('should not touch a free account', () => {
    expect(rollAt(makeAccountDoc(), '2030-01-01T00:00:00Z').dirty).toBe(false);
  });
});
