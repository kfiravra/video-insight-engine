import { describe, it, expect } from 'vitest';
import { applyAdjust, applySetPlan } from '../admin-ops.js';
import { applyExecutionCharge } from '../hold-ops.js';
import { adminAudit, DAY_MS } from './billing-fixtures.js';
import {
  grantTokens,
  holdVideo,
  hoursAfterT0,
  PureLedger,
  releaseRun,
  settleRun,
  signedUpLedger,
  T0,
} from './pure-ledger.js';

const TEN_MIN = 600; // 840 tokens

function signupLotId(ledger: PureLedger): string {
  return ledger.lots[0]._id.toHexString();
}

/** A Pro account with `planLeft` of its 20,000 cycle tokens and the signup lot. */
function proLedger(planLeft: number): PureLedger {
  const ledger = signedUpLedger();
  ledger.step(T0, (draft) => applySetPlan(draft, { planId: 'pro' }, adminAudit()));
  if (planLeft < 20_000) ledger.step(T0, (draft) => applyAdjust(draft, { bucket: 'plan', tokens: planLeft - 20_000 }, adminAudit()));
  return ledger;
}

describe('hold-ops (pure transitions)', () => {
  describe('applyHold', () => {
    it('should take the estimate from the signup lot and key the entry by run and lot', () => {
      const ledger = signedUpLedger();
      const lot = signupLotId(ledger);

      const hold = holdVideo(ledger, T0, 'run-1', TEN_MIN);

      expect(hold).toMatchObject({ requestId: 'run-1', tokens: 840, durationSec: 600, planCycleStart: null });
      expect(ledger.rows('run-1')).toEqual([['hold', 'credit', -840, `hold:run-1:lot:${lot}`, false]]);
      expect(ledger.entries.at(-1)).toMatchObject({ unit: 'tokens', executionKind: 'video', youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });
      expect(ledger.lot(lot)?.tokensRemaining).toBe(160);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should take the plan first and remember the cycle it came from', () => {
      const ledger = proLedger(300);

      const hold = holdVideo(ledger, T0, 'run-1', TEN_MIN);

      expect(hold?.split).toMatchObject({ plan: 300, credit: 540 });
      expect(hold?.planCycleStart).toEqual(ledger.account.buckets.plan.cycleStart);
      expect(ledger.rows('run-1').map((row) => row[3])).toEqual(['hold:run-1:plan', `hold:run-1:lot:${signupLotId(ledger)}`]);
      expect(ledger.check().drift).toEqual([]);
    });
  });

  describe('applySettleActual — the run cost less than its hold', () => {
    it('should debit the cost from the hold (mirrorsHold) and give the rest back to its lot', () => {
      const ledger = signedUpLedger();
      const lot = signupLotId(ledger);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      const result = settleRun(ledger, hoursAfterT0(1), 'run-1', 0.2);

      expect(result).toEqual({ charged: 600, released: 240, debt: 0 });
      expect(ledger.rows('run-1').slice(1)).toEqual([
        ['debit', 'credit', -600, `debit:run-1:lot:${lot}`, true],
        ['release', 'credit', 240, `release:run-1:lot:${lot}`, false],
      ]);
      expect(ledger.entries.find((e) => e.type === 'debit')?.costUsd).toBe(0.2);
      expect([ledger.lot(lot)?.tokensRemaining, ledger.account.holds]).toEqual([400, []]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should debit along the hold\'s own order (plan, then its lots) and give back what is left of each', () => {
      const ledger = proLedger(300);
      const lot = signupLotId(ledger);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      settleRun(ledger, hoursAfterT0(1), 'run-1', 0.15);

      expect(ledger.rows('run-1').slice(2)).toEqual([
        ['debit', 'plan', -300, 'debit:run-1:plan', true],
        ['debit', 'credit', -150, `debit:run-1:lot:${lot}`, true],
        ['release', 'credit', 390, `release:run-1:lot:${lot}`, false],
      ]);
      expect(ledger.lot(lot)?.tokensRemaining).toBe(850);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should release nothing when the cost is exactly the estimate', () => {
      const ledger = signedUpLedger();
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      expect(settleRun(ledger, hoursAfterT0(1), 'run-1', 0.28)).toEqual({ charged: 840, released: 0, debt: 0 });
      expect(ledger.rows('run-1').map((row) => row[0])).toEqual(['hold', 'debit']);
    });

    it('should give everything back on a free run (cost 0)', () => {
      const ledger = signedUpLedger();
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      expect(settleRun(ledger, hoursAfterT0(1), 'run-1', 0)).toEqual({ charged: 0, released: 840, debt: 0 });
      expect(ledger.balanceAt(hoursAfterT0(1))).toBe(1000);
    });
  });

  describe('applySettleActual — the run cost more than its hold', () => {
    it('should mirror the whole hold and take the excess from the plan, then lots FIFO', () => {
      const ledger = proLedger(1000);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      const result = settleRun(ledger, hoursAfterT0(1), 'run-1', 0.5);

      expect(result).toEqual({ charged: 1500, released: 0, debt: 0 });
      expect(ledger.rows('run-1').slice(1)).toEqual([
        ['debit', 'plan', -840, 'debit:run-1:plan', true],
        ['debit', 'plan', -160, 'debit:run-1:extra:plan', false],
        ['debit', 'credit', -500, `debit:run-1:extra:lot:${signupLotId(ledger)}`, false],
      ]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should run into debt once the plan and every lot are empty', () => {
      const ledger = signedUpLedger();
      const signup = signupLotId(ledger);
      const extra = grantTokens(ledger, T0, 500);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      const result = settleRun(ledger, hoursAfterT0(1), 'run-1', 0.6);

      expect(result).toEqual({ charged: 1800, released: 0, debt: 300 });
      expect(ledger.rows('run-1').slice(1)).toEqual([
        ['debit', 'credit', -840, `debit:run-1:lot:${signup}`, true],
        ['debit', 'credit', -160, `debit:run-1:extra:lot:${signup}`, false],
        ['debit', 'credit', -500, `debit:run-1:extra:lot:${extra}`, false],
        ['debit', 'debt', -300, 'debit:run-1:extra:debt', false],
      ]);
      expect([ledger.account.buckets.debt.tokens, ledger.balanceAt(hoursAfterT0(1))]).toEqual([300, -300]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should share the run cost across the mirrored and the extra debits', () => {
      const ledger = signedUpLedger();
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      settleRun(ledger, hoursAfterT0(1), 'run-1', 0.6);

      const costs = ledger.entries.filter((e) => e.type === 'debit').map((e) => e.costUsd ?? 0);
      expect(costs.reduce((a, b) => a + b, 0)).toBeCloseTo(0.6, 6);
      expect(costs[0]).toBeCloseTo(0.28, 6);
    });
  });

  describe('a lot or plan cycle that ended during the run', () => {
    it('should let a lot that expired mid-run pay its part and expire the unused rest at once', () => {
      const ledger = signedUpLedger();
      const expiring = grantTokens(ledger, T0, 1000, hoursAfterT0(2));
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      settleRun(ledger, hoursAfterT0(3), 'run-1', 0.1);

      expect(ledger.rows('run-1').slice(1)).toEqual([
        ['debit', 'credit', -300, `debit:run-1:lot:${expiring}`, true],
        ['release', 'credit', 540, `release:run-1:lot:${expiring}`, false],
        ['expire', 'credit', -540, `release-expire:run-1:lot:${expiring}`, false],
      ]);
      expect([ledger.lot(expiring)?.tokensRemaining, ledger.balanceAt(hoursAfterT0(3))]).toEqual([160, 1000]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should take an overrun from the lots still spendable, not the expired one', () => {
      const ledger = signedUpLedger();
      const expiring = grantTokens(ledger, T0, 1000, hoursAfterT0(2));
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      settleRun(ledger, hoursAfterT0(3), 'run-1', 0.5);

      expect(ledger.rows('run-1').map((row) => row[3])).toEqual([
        `hold:run-1:lot:${expiring}`,
        `debit:run-1:lot:${expiring}`,
        `debit:run-1:extra:lot:${signupLotId(ledger)}`,
      ]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should expire unused plan tokens whose cycle ended during the run', () => {
      const ledger = proLedger(20_000);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);
      const later = new Date(T0.getTime() + 31 * DAY_MS);

      settleRun(ledger, later, 'run-1', 0.1);

      expect(ledger.rows('run-1').slice(1)).toEqual([
        ['debit', 'plan', -300, 'debit:run-1:plan', true],
        ['release', 'plan', 540, 'release:run-1:plan', false],
        ['expire', 'plan', -540, 'release-expire:run-1:plan', false],
      ]);
      expect(ledger.balanceAt(later)).toBe(21_000);
      expect(ledger.check().drift).toEqual([]);
    });
  });

  describe('applyRelease', () => {
    it('should give every slice back to exactly where it came from, with the reason', () => {
      const ledger = proLedger(300);
      const lot = signupLotId(ledger);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);

      expect(releaseRun(ledger, hoursAfterT0(1), 'run-1', 'cached')).toBe(840);

      expect(ledger.rows('run-1').slice(2)).toEqual([
        ['release', 'plan', 300, 'release:run-1:plan', false],
        ['release', 'credit', 540, `release:run-1:lot:${lot}`, false],
      ]);
      expect(ledger.entries.at(-1)?.reason).toBe('cached');
      expect([ledger.balanceAt(hoursAfterT0(1)), ledger.account.holds]).toEqual([1300, []]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should refill a lot the hold emptied', () => {
      const ledger = new PureLedger();
      const lot = grantTokens(ledger, T0, 840);
      holdVideo(ledger, T0, 'run-1', TEN_MIN);
      expect(ledger.lot(lot)?.tokensRemaining).toBe(0);

      releaseRun(ledger, hoursAfterT0(1), 'run-1');

      expect(ledger.lot(lot)?.tokensRemaining).toBe(840);
      expect(ledger.check().drift).toEqual([]);
    });
  });

  describe('repaying debt with what comes back (D7)', () => {
    /** run-1 holds 840 of the signup lot; run-2 then costs $0.60 and leaves 640 owed. */
    function inDebtWithOpenHold(): PureLedger {
      const ledger = signedUpLedger();
      holdVideo(ledger, T0, 'run-1', TEN_MIN);
      grantTokens(ledger, T0, 1000);
      holdVideo(ledger, T0, 'run-2', TEN_MIN);
      settleRun(ledger, hoursAfterT0(1), 'run-2', 0.6);
      expect(ledger.account.buckets.debt.tokens).toBe(640);
      return ledger;
    }

    it('should repay the debt from a released hold before the tokens are spendable', () => {
      const ledger = inDebtWithOpenHold();
      const signup = signupLotId(ledger);

      releaseRun(ledger, hoursAfterT0(2), 'run-1');

      expect(ledger.rows('run-1').slice(1)).toEqual([
        ['release', 'credit', 840, `release:run-1:lot:${signup}`, false],
        ['repay', 'credit', -640, `repay:release:run-1:lot:${signup}`, false],
        ['repay', 'debt', 640, 'repay:release:run-1', false],
      ]);
      expect([ledger.account.buckets.debt.tokens, ledger.balanceAt(hoursAfterT0(2))]).toEqual([0, 200]);
      expect(ledger.check().drift).toEqual([]);
    });

    it('should repay part of the debt from a settle\'s unused remainder', () => {
      const ledger = inDebtWithOpenHold();

      settleRun(ledger, hoursAfterT0(2), 'run-1', 0.1);

      expect(ledger.entries.filter((e) => e.type === 'repay').map((e) => e.idempotencyKey)).toEqual([
        `repay:release:run-1:lot:${signupLotId(ledger)}`,
        'repay:release:run-1',
      ]);
      expect([ledger.account.buckets.debt.tokens, ledger.balanceAt(hoursAfterT0(2))]).toEqual([100, -100]);
      expect(ledger.check().drift).toEqual([]);
    });
  });

  describe('applyExecutionCharge', () => {
    it('should charge a chat turn from the plan, then lots, then debt, keyed by its execution id', () => {
      const ledger = proLedger(300);
      const lot = signupLotId(ledger);

      const split = ledger.step(hoursAfterT0(1), (draft) =>
        applyExecutionCharge(draft, {
          executionId: 'ex-1', kind: 'chat', requestId: 'req-1', userId: draft.original.ownerId, tokens: 1500, costUsd: 0.5,
        }));

      expect(split).toEqual({ plan: 300, credit: 1000, lots: [{ lotId: lot, tokens: 1000 }], debt: 200 });
      expect(ledger.rows('req-1')).toEqual([
        ['debit', 'plan', -300, 'debit:exec:ex-1:plan', false],
        ['debit', 'credit', -1000, `debit:exec:ex-1:lot:${lot}`, false],
        ['debit', 'debt', -200, 'debit:exec:ex-1:debt', false],
      ]);
      const entries = ledger.entries.filter((e) => e.source.ref === 'req-1');
      expect(entries.every((e) => e.executionKind === 'chat' && e.executionId === 'ex-1' && e.source.kind === 'run')).toBe(true);
      expect(entries.reduce((sum, e) => sum + (e.costUsd ?? 0), 0)).toBeCloseTo(0.5, 6);
      expect(ledger.check().drift).toEqual([]);
    });
  });
});
