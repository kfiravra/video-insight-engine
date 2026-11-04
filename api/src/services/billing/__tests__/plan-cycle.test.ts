import { describe, it, expect } from 'vitest';
import { PLANS } from '@vie/types';
import { AccountDraft } from '../account-draft.js';
import { repayDebt } from '../debt.js';
import { planAvailable, rollContext, rollPlanCycle } from '../plan-cycle.js';
import { makeAccountDoc } from './billing-fixtures.js';
import type { BillingAccountDocument } from '../../../repositories/billing.repository.js';

const TERM_START = new Date('2026-01-15T00:00:00Z');

/** A manual Pro account in its first cycle with `used` of 20,000 tokens spent and `debt` owed. */
function proAccount(used: number, overrides: Partial<BillingAccountDocument['plan']> = {}, debt = 0): BillingAccountDocument {
  return makeAccountDoc({
    plan: { id: 'pro', status: 'active', source: 'manual', periodStart: TERM_START, periodEnd: new Date('2027-01-15T00:00:00Z'), ...overrides },
    buckets: {
      plan: { cycleStart: TERM_START, cycleEnd: new Date('2026-02-15T00:00:00Z'), tokensGranted: 20_000, tokensUsed: used },
      credit: { tokensAvailable: 0 },
      debt: { tokens: debt },
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
    const draft = new AccountDraft(proAccount(100), [], new Date('2026-02-14T23:59:59Z'));

    expect([rollPlanCycle(draft), draft.dirty]).toEqual([null, false]);
  });

  it('should reset the plan to PLANS.pro.periodTokens at the next cycle with no rollover', () => {
    const draft = rollAt(proAccount(100), '2026-02-15T00:00:00Z');

    expect(PLANS.pro.periodTokens).toBe(20_000);
    expect(draft.account.buckets.plan).toEqual({
      cycleStart: new Date('2026-02-15T00:00:00Z'),
      cycleEnd: new Date('2026-03-15T00:00:00Z'),
      tokensGranted: 20_000,
      tokensUsed: 0,
    });
    expect(planAvailable(draft.account, draft.now)).toBe(20_000);
  });

  it('should expire the unused remainder of the ended cycle in the ledger, in tokens', () => {
    const draft = rollAt(proAccount(100), '2026-02-15T00:00:00Z');

    expect(draft.entries.map((e) => [e.type, e.bucket, e.unit, e.tokens])).toEqual([
      ['expire', 'plan', 'tokens', -19_900],
      ['grant', 'plan', 'tokens', 20_000],
    ]);
  });

  it('should jump straight to the cycle containing now after idle months, granting once', () => {
    const draft = rollAt(proAccount(0), '2026-06-20T00:00:00Z');

    expect(draft.account.buckets.plan.cycleStart).toEqual(new Date('2026-06-15T00:00:00Z'));
    expect(draft.entries.filter((e) => e.type === 'grant').map((e) => e.tokens)).toEqual([20_000]);
  });

  it('should return the new cycle\'s grant key, and key each transition by its cycle', () => {
    const account = proAccount(100);
    const keys = (draft: AccountDraft) => draft.entries.map((e) => e.idempotencyKey);
    const draft = new AccountDraft(account, [], new Date('2026-02-15T00:00:00Z'));

    const grantKey = rollPlanCycle(draft);

    const id = account._id.toHexString();
    expect(grantKey).toBe(`roll:${id}:grant:2026-02-15T00:00:00.000Z`);
    expect(keys(draft)).toEqual([`roll:${id}:expire:2026-01-15T00:00:00.000Z`, grantKey]);
    expect(keys(rollAt(account, '2026-02-20T00:00:00Z'))).toEqual(keys(draft));
  });

  it('should clip the last cycle to the end of the term', () => {
    const draft = rollAt(proAccount(0, { periodEnd: new Date('2026-03-01T00:00:00Z') }), '2026-02-20T00:00:00Z');

    expect(draft.account.buckets.plan.cycleEnd).toEqual(new Date('2026-03-01T00:00:00Z'));
  });

  it('should revert to free and expire the remainder when the term ends, granting nothing', () => {
    const draft = new AccountDraft(proAccount(50, { periodEnd: new Date('2026-02-01T00:00:00Z') }), [], new Date('2026-02-01T00:00:00Z'));

    expect(rollPlanCycle(draft)).toBeNull();
    expect(draft.account.plan.id).toBe('free');
    expect(planAvailable(draft.account, draft.now)).toBe(0);
    expect(draft.entries.map((e) => [e.type, e.tokens])).toEqual([['expire', -19_950]]);
  });

  it('should not touch a free account', () => {
    expect(rollAt(makeAccountDoc(), '2030-01-01T00:00:00Z').dirty).toBe(false);
  });

  it('should let the new cycle repay debt first (as BillingUnits rolls)', () => {
    const draft = new AccountDraft(proAccount(20_000, {}, 1_500), [], new Date('2026-02-15T00:00:00Z'));

    const grantKey = rollPlanCycle(draft);
    if (grantKey) repayDebt(draft, grantKey, rollContext(draft));

    expect(draft.account.buckets.debt.tokens).toBe(0);
    expect(planAvailable(draft.account, draft.now)).toBe(18_500);
    expect(draft.entries.filter((e) => e.type === 'repay').map((e) => [e.bucket, e.tokens, e.idempotencyKey])).toEqual([
      ['plan', -1_500, `repay:${grantKey}:plan`],
      ['debt', 1_500, `repay:${grantKey}`],
    ]);
  });
});

describe('planAvailable', () => {
  it('should be 0 on free, after the cycle ended, and never negative', () => {
    const overdrawn = proAccount(25_000);

    expect([
      planAvailable(makeAccountDoc(), TERM_START),
      planAvailable(proAccount(0), new Date('2026-02-15T00:00:00Z')),
      planAvailable(overdrawn, TERM_START),
    ]).toEqual([0, 0, 0]);
  });
});
