import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { ObjectId } from 'mongodb';
import { applyAdjust, applySetPlan } from '../admin-ops.js';
import { applyExecutionCharge } from '../hold-ops.js';
import { applyLotExpiry, applyRecurringRun, recurringRunAt } from '../maintenance-ops.js';
import { tokensForUsd } from '../tokens.js';
import { adminAudit, HOUR_MS } from './billing-fixtures.js';
import { grantTokens, holdVideo, releaseRun, settleRun, signedUpLedger, T0 } from './pure-ledger.js';
import type { PureLedger } from './pure-ledger.js';
import type { RecurringGrantDocument } from '../../../repositories/billing.repository.js';

// Draft-level properties: every transition, committed the way BillingUnits
// commits it, keeps the account equal to Σ ledger, and debt only ever sits
// on an account with nothing spendable (D7).

const costArb = fc.integer({ min: 0, max: 2_000_000 }).map((micros) => micros / 1e6);

/** debt ≥ 0, debt > 0 ⇔ balance < 0, and the projection check passes. */
function expectInvariants(ledger: PureLedger, now: Date): void {
  expect(ledger.check().drift).toEqual([]);
  const debt = ledger.account.buckets.debt.tokens;
  expect(debt).toBeGreaterThanOrEqual(0);
  expect(debt > 0).toBe(ledger.balanceAt(now) < 0);
}

/** Σ tokens of a run's debits (mirrored + overrun) — what the run was charged. */
function chargedFor(ledger: PureLedger, runId: string): number {
  return ledger.entries.filter((e) => e.type === 'debit' && e.source.ref === runId).reduce((sum, e) => sum - (e.tokens ?? 0), 0);
}

describe('settleActual properties (pure)', () => {
  const scenarioArb = fc.record({
    pro: fc.boolean(),
    planLeft: fc.integer({ min: 0, max: 20_000 }),
    lots: fc.array(
      fc.record({ tokens: fc.integer({ min: 1, max: 3000 }), expiresInHours: fc.option(fc.integer({ min: 1, max: 96 }), { nil: null }) }),
      { maxLength: 4 },
    ),
    minutes: fc.tuple(fc.integer({ min: 0, max: 180 }), fc.integer({ min: 0, max: 180 })),
    waitHours: fc.oneof(fc.integer({ min: 0, max: 100 }), fc.integer({ min: 24 * 28, max: 24 * 40 })),
    costs: fc.tuple(costArb, costArb),
  });

  it('should charge exactly tokensForUsd(cost), keep the projection exact and owe only with nothing spendable', () => {
    fc.assert(
      fc.property(scenarioArb, ({ pro, planLeft, lots, minutes, waitHours, costs }) => {
        const ledger = signedUpLedger();
        if (pro) {
          ledger.step(T0, (draft) => applySetPlan(draft, { planId: 'pro' }, adminAudit()));
          if (planLeft < 20_000) ledger.step(T0, (draft) => applyAdjust(draft, { bucket: 'plan', tokens: planLeft - 20_000 }, adminAudit()));
        }
        for (const lot of lots) {
          grantTokens(ledger, T0, lot.tokens, lot.expiresInHours === null ? null : new Date(T0.getTime() + lot.expiresInHours * HOUR_MS));
        }
        const holds = minutes.map((m, n) => holdVideo(ledger, T0, `run-${n}`, m * 60));
        const settleAt = new Date(T0.getTime() + waitHours * HOUR_MS);

        // Settle the second run first, so the first one's remainder can meet its debt.
        for (const n of [1, 0]) {
          const hold = holds[n];
          if (!hold) continue;
          const result = settleRun(ledger, settleAt, hold.requestId, costs[n]);
          const tokens = tokensForUsd(costs[n]);
          expect(result.charged).toBe(tokens);
          expect(chargedFor(ledger, hold.requestId)).toBe(tokens);
          expect(result.released).toBe(Math.max(hold.tokens - tokens, 0));
          if (result.debt > 0) expect(ledger.spendableAt(settleAt)).toBe(0);
          expectInvariants(ledger, settleAt);
        }
        expect(ledger.account.holds).toEqual([]);
      }),
      { numRuns: 400 },
    );
  });
});

describe('ledger sequences (pure, D7)', () => {
  type Op =
    | { kind: 'hold'; minutes: number }
    | { kind: 'settle'; pick: number; costUsd: number }
    | { kind: 'release'; pick: number }
    | { kind: 'grant'; tokens: number; expiresInHours: number | null }
    | { kind: 'adjust'; tokens: number }
    | { kind: 'charge'; costUsd: number }
    | { kind: 'plan'; planId: 'free' | 'pro' }
    | { kind: 'expireLots' }
    | { kind: 'recurring' }
    | { kind: 'wait'; hours: number };

  const opArb: fc.Arbitrary<Op> = fc.oneof(
    { weight: 3, arbitrary: fc.record({ kind: fc.constant('hold' as const), minutes: fc.integer({ min: 0, max: 180 }) }) },
    { weight: 3, arbitrary: fc.record({ kind: fc.constant('settle' as const), pick: fc.nat(), costUsd: costArb }) },
    { weight: 1, arbitrary: fc.record({ kind: fc.constant('release' as const), pick: fc.nat() }) },
    {
      weight: 2,
      arbitrary: fc.record({
        kind: fc.constant('grant' as const),
        tokens: fc.integer({ min: 1, max: 3000 }),
        expiresInHours: fc.option(fc.integer({ min: 1, max: 48 }), { nil: null }),
      }),
    },
    { weight: 1, arbitrary: fc.record({ kind: fc.constant('adjust' as const), tokens: fc.integer({ min: 1, max: 2000 }) }) },
    { weight: 2, arbitrary: fc.record({ kind: fc.constant('charge' as const), costUsd: costArb }) },
    { weight: 1, arbitrary: fc.record({ kind: fc.constant('plan' as const), planId: fc.constantFrom('free' as const, 'pro' as const) }) },
    { weight: 1, arbitrary: fc.constant({ kind: 'expireLots' as const }) },
    { weight: 1, arbitrary: fc.constant({ kind: 'recurring' as const }) },
    {
      weight: 2,
      arbitrary: fc.record({
        kind: fc.constant('wait' as const),
        hours: fc.oneof(fc.integer({ min: 1, max: 30 }), fc.integer({ min: 24 * 25, max: 24 * 40 })),
      }),
    },
  );

  function recurringGrant(accountId: ObjectId, tokens = 500): RecurringGrantDocument {
    return {
      _id: new ObjectId(), accountId, tokens, every: 'month', active: true, nextRunAt: T0, lastRunAt: null,
      actorId: 'system:test', auditId: new ObjectId(), reason: 'demo', createdAt: T0, updatedAt: T0, deletedAt: null,
    };
  }

  /** Run one op at `now`; returns the clock after it. */
  function apply(ledger: PureLedger, op: Op, n: number, now: Date, open: string[], grant: RecurringGrantDocument): Date {
    switch (op.kind) {
      case 'hold':
        if (holdVideo(ledger, now, `run-${n}`, op.minutes * 60)) open.push(`run-${n}`);
        return now;
      case 'settle':
      case 'release': {
        if (open.length === 0) return now;
        const [runId] = open.splice(op.pick % open.length, 1);
        if (op.kind === 'settle') settleRun(ledger, now, runId, op.costUsd);
        else releaseRun(ledger, now, runId);
        return now;
      }
      case 'grant':
        grantTokens(ledger, now, op.tokens, op.expiresInHours === null ? null : new Date(now.getTime() + op.expiresInHours * HOUR_MS));
        return now;
      case 'adjust':
        ledger.step(now, (draft) => applyAdjust(draft, { bucket: 'credit', tokens: op.tokens }, adminAudit()));
        return now;
      case 'charge': {
        const tokens = tokensForUsd(op.costUsd);
        if (tokens === 0) return now;
        ledger.step(now, (draft) =>
          applyExecutionCharge(draft, {
            executionId: `ex-${n}`, kind: 'chat', requestId: `req-${n}`, userId: draft.original.ownerId, tokens, costUsd: op.costUsd,
          }));
        return now;
      }
      case 'plan':
        ledger.step(now, (draft) => applySetPlan(draft, { planId: op.planId }, adminAudit()));
        return now;
      case 'expireLots': {
        const done = new Set(ledger.entries.map((e) => e.idempotencyKey).filter((key) => key.startsWith('expire:lot:')));
        ledger.step(now, (draft) => applyLotExpiry(draft, done));
        return now;
      }
      case 'recurring': {
        const run = recurringRunAt(grant, now);
        if (!ledger.entries.some((e) => e.idempotencyKey === run.idempotencyKey)) {
          ledger.step(now, (draft) => applyRecurringRun(draft, grant, run));
        }
        return now;
      }
      case 'wait':
        return new Date(now.getTime() + op.hours * HOUR_MS);
    }
  }

  it('should keep Σ ledger = projection, debt ≥ 0 and debt > 0 ⇔ balance < 0 through any sequence', () => {
    fc.assert(
      fc.property(fc.array(opArb, { minLength: 1, maxLength: 30 }), (ops) => {
        const ledger = signedUpLedger();
        const grant = recurringGrant(ledger.account._id);
        const open: string[] = [];
        let now = T0;
        ops.forEach((op, n) => {
          now = apply(ledger, op, n, now, open, grant);
          if (op.kind !== 'wait') expectInvariants(ledger, now);
        });
        expect(ledger.account.holds.map((hold) => hold.requestId).sort()).toEqual([...open].sort());
      }),
      { numRuns: 400 },
    );
  });

  it('should repay debt from every kind of inflow before it becomes spendable', () => {
    const inflows: Array<[string, (ledger: PureLedger, now: Date) => void, string]> = [
      ['an admin lot', (l, now) => grantTokens(l, now, 5000), 'repay:admin:'],
      ['a positive adjustment', (l, now) => l.step(now, (d) => applyAdjust(d, { bucket: 'credit', tokens: 5000 }, adminAudit())), 'repay:admin:'],
      ['setPlan pro', (l, now) => l.step(now, (d) => applySetPlan(d, { planId: 'pro' }, adminAudit())), 'repay:admin:'],
      ['a recurring run', (l, now) => {
        const grant = recurringGrant(l.account._id, 5000);
        l.step(now, (d) => applyRecurringRun(d, grant, recurringRunAt(grant, now)));
      }, 'repay:recurring:'],
    ];
    for (const [, inflow, prefix] of inflows) {
      const ledger = signedUpLedger();
      holdVideo(ledger, T0, 'run-1', 600);
      settleRun(ledger, T0, 'run-1', 0.6);
      expect(ledger.account.buckets.debt.tokens).toBe(800);

      inflow(ledger, T0);

      const repays = ledger.entries.filter((e) => e.type === 'repay');
      expect(repays.every((e) => e.idempotencyKey.startsWith(prefix))).toBe(true);
      expect(repays.find((e) => e.bucket === 'debt')?.tokens).toBe(800);
      expect(ledger.account.buckets.debt.tokens).toBe(0);
      expectInvariants(ledger, T0);
    }
  });
});
