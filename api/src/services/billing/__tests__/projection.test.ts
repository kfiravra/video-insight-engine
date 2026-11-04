import { describe, it, expect } from 'vitest';
import { ObjectId } from 'mongodb';
import { applySetPlan } from '../admin-ops.js';
import { applyExecutionCharge } from '../hold-ops.js';
import { checkProjection } from '../projection.js';
import { adminAudit } from './billing-fixtures.js';
import { grantTokens, holdVideo, hoursAfterT0, releaseRun, settleRun, signedUpLedger, T0 } from './pure-ledger.js';
import type { PureLedger } from './pure-ledger.js';
import type { ProjectionCheck } from '../projection.js';
import type {
  BillingAccountDocument,
  CreditLotDocument,
  LedgerEntryDocument,
} from '../../../repositories/billing.repository.js';

interface Projection {
  account: BillingAccountDocument;
  lots: CreditLotDocument[];
  entries: LedgerEntryDocument[];
}

/**
 * Pro plan, a signup lot and an admin lot, run-1 held and settled above its
 * hold (mirrored + overrun debits, into debt), run-2 held and released, a
 * chat turn charged, and run-3 still open.
 */
function busyLedger(): PureLedger {
  const ledger = signedUpLedger();
  ledger.step(T0, (draft) => applySetPlan(draft, { planId: 'pro' }, adminAudit()));
  grantTokens(ledger, T0, 500);
  holdVideo(ledger, T0, 'run-1', 600);
  holdVideo(ledger, T0, 'run-2', 600);
  holdVideo(ledger, T0, 'run-3', 600);
  releaseRun(ledger, hoursAfterT0(1), 'run-2');
  settleRun(ledger, hoursAfterT0(1), 'run-1', 7.5);
  ledger.step(hoursAfterT0(2), (draft) =>
    applyExecutionCharge(draft, { executionId: 'ex-1', kind: 'chat', requestId: 'req-1', userId: draft.original.ownerId, tokens: 120, costUsd: 0.04 }));
  return ledger;
}

/** Check a fresh busy ledger after `mutate` changed it. */
function checkAfter(mutate: (projection: Projection) => void): ProjectionCheck {
  const ledger = busyLedger();
  const projection: Projection = { account: ledger.account, lots: ledger.lots, entries: [...ledger.entries] };
  mutate(projection);
  return checkProjection(projection.account, projection.lots, projection.entries);
}

const fields = (check: ProjectionCheck): string[] => check.drift.map((d) => d.field);

describe('checkProjection', () => {
  it('should find a busy account equal to its ledger', () => {
    const ledger = busyLedger();

    expect(ledger.account.buckets.debt.tokens).toBeGreaterThan(0);
    expect(ledger.check()).toEqual({ ok: true, drift: [] });
  });

  describe('drift', () => {
    it('should report a plan bucket that diverges', () => {
      expect(fields(checkAfter(({ account }) => { account.buckets.plan.tokensUsed += 1; }))).toEqual(['buckets.plan (granted − used)']);
    });

    it('should report a credit bucket that diverges from its lots', () => {
      expect(fields(checkAfter(({ account }) => { account.buckets.credit.tokensAvailable += 5; }))).toEqual(['buckets.credit.tokensAvailable']);
    });

    it('should report a lot that diverges, by id', () => {
      let lotId = '';
      const check = checkAfter(({ lots }) => {
        lots[0].tokensRemaining += 7;
        lotId = lots[0]._id.toHexString();
      });

      expect(fields(check)).toEqual(['Σ creditLots.tokensRemaining', `lot:${lotId}`]);
    });

    it('should report a lot the ledger knows but the collection lacks', () => {
      const check = checkAfter((p) => { p.lots = p.lots.slice(1); });

      expect(check.drift).toContainEqual(expect.objectContaining({ actual: 'missing' }));
    });

    it('should report a debt bucket that diverges', () => {
      expect(fields(checkAfter(({ account }) => { account.buckets.debt.tokens += 1; }))).toEqual(['buckets.debt.tokens']);
    });

    it('should report a negative debt even when the ledger agrees with it', () => {
      const check = checkAfter(({ account, entries }) => {
        const owed = account.buckets.debt.tokens;
        account.buckets.debt.tokens = -10;
        entries.push({
          _id: new ObjectId(), accountId: account._id, userId: account.ownerId, type: 'adjust', bucket: 'debt', unit: 'tokens',
          tokens: owed + 10, source: { kind: 'admin', ref: 'a1' }, idempotencyKey: 'admin:a1', createdAt: T0,
        });
      });

      expect(fields(check)).toEqual(['buckets.debt.tokens ≥ 0']);
    });

    it('should report a hold the account lost, and a hold with no ledger entries', () => {
      const lost = checkAfter(({ account }) => { account.holds = []; });
      const phantom = checkAfter(({ account }) => { account.holds.push({ ...account.holds[0], requestId: 'run-ghost' }); });

      expect([lost.drift, phantom.drift]).toEqual([
        [{ field: 'holds', expected: 'run-3', actual: '' }],
        [{ field: 'holds', expected: 'run-3', actual: 'run-3,run-ghost' }],
      ]);
    });
  });

  describe('what counts', () => {
    it('should leave hold-mirroring debits out (counting one would double-charge the hold)', () => {
      const check = checkAfter(({ entries }) => {
        const mirrored = entries.find((e) => e.mirrorsHold);
        if (mirrored) delete mirrored.mirrorsHold;
      });

      expect(check.ok).toBe(false);
    });

    it('should count overrun debits', () => {
      const check = checkAfter((p) => { p.entries = p.entries.filter((e) => !e.idempotencyKey.startsWith('debit:run-1:extra:')); });

      expect(check.ok).toBe(false);
    });

    it('should count execution debits, and never take them for an open hold', () => {
      const without = checkAfter((p) => { p.entries = p.entries.filter((e) => e.executionId !== 'ex-1'); });

      expect(without.ok).toBe(false);
      expect(busyLedger().check().drift).toEqual([]);
    });

    it('should ignore minute entries and minute holds from before the token migration', () => {
      const check = checkAfter(({ account, entries }) => {
        const base = { accountId: account._id, userId: account.ownerId, createdAt: T0 };
        entries.push(
          { ...base, _id: new ObjectId(), type: 'grant', bucket: 'credit', minutes: 300, source: { kind: 'admin', ref: 'a1' }, idempotencyKey: 'admin:a1', lotId: new ObjectId() },
          { ...base, _id: new ObjectId(), type: 'hold', bucket: 'free', minutes: -10, source: { kind: 'run', ref: 'old-run' }, idempotencyKey: 'hold:old-run:free' },
        );
        account.holds.push({
          requestId: 'old-run', userId: account.ownerId, youtubeId: 'x', durationSec: 600, tokens: 0,
          split: { plan: 0, credit: 0, lots: [] }, planCycleStart: null, createdAt: T0,
        });
      });

      expect(check).toEqual({ ok: true, drift: [] });
    });
  });
});
