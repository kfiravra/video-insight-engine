import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { MAX_VIDEO_MINUTES } from '@vie/types';
import {
  allocate,
  balanceOf,
  chargeSplit,
  compareLotsFifo,
  isLotSpendable,
  maxVideoMinutes,
  spendableCredit,
} from '../allocation.js';
import type { AllocationInput, AllocationLot, SpendableState } from '../allocation.js';

const HOUR = 60 * 60 * 1000;
const NOW = new Date('2026-11-01T12:00:00Z');

function lot(id: string, tokensRemaining: number, overrides: Partial<AllocationLot> = {}): AllocationLot {
  return { lotId: id, tokensRemaining, expiresAt: null, createdAt: new Date(NOW.getTime() - 10 * HOUR), ...overrides };
}

/** A 10-minute video (840 tokens) on an account with nothing to spend; override any part. */
function input(overrides: Partial<AllocationInput> = {}): AllocationInput {
  return { tokens: 840, videoMinutes: 10, now: NOW, planAvailable: 0, lots: [], debt: 0, caps: {}, openHolds: 0, ...overrides };
}

const lotArb = fc.record({
  lotId: fc.hexaString({ minLength: 4, maxLength: 4 }),
  tokensRemaining: fc.integer({ min: 0, max: 3000 }),
  expiresAt: fc.option(fc.integer({ min: -48, max: 48 * 30 }).map((h) => new Date(NOW.getTime() + h * HOUR)), { nil: null }),
  createdAt: fc.integer({ min: 1, max: 24 * 365 }).map((h) => new Date(NOW.getTime() - h * HOUR)),
});

const inputArb: fc.Arbitrary<AllocationInput> = fc.record({
  tokens: fc.integer({ min: 660, max: 4500 }),
  videoMinutes: fc.integer({ min: 0, max: 240 }),
  now: fc.constant(NOW),
  planAvailable: fc.oneof(fc.constant(0), fc.integer({ min: 0, max: 20_000 })),
  lots: fc.uniqueArray(lotArb, { maxLength: 5, selector: (l) => l.lotId }),
  debt: fc.oneof(fc.constant(0), fc.constant(0), fc.integer({ min: 1, max: 2000 })),
  caps: fc.record(
    {
      maxVideoMinutes: fc.option(fc.integer({ min: 1, max: 180 }), { nil: null }),
      maxConcurrent: fc.option(fc.integer({ min: 1, max: 3 }), { nil: null }),
      blocked: fc.option(fc.boolean(), { nil: null }),
    },
    { requiredKeys: [] },
  ),
  openHolds: fc.integer({ min: 0, max: 4 }),
});

const spendable = (i: AllocationInput): AllocationLot[] => i.lots.filter((l) => isLotSpendable(l, NOW)).sort(compareLotsFifo);

/** The gate order, written out: blocked → too many runs → too long → insufficient → admitted. */
function expectedOutcome(i: AllocationInput): string {
  if (i.caps.blocked) return 'blocked';
  if (i.caps.maxConcurrent != null && i.openHolds >= i.caps.maxConcurrent) return 'too_many_runs';
  if (i.videoMinutes > Math.min(MAX_VIDEO_MINUTES, i.caps.maxVideoMinutes ?? Infinity)) return 'too_long';
  const credits = spendable(i).reduce((sum, l) => sum + l.tokensRemaining, 0);
  if (i.debt > 0 || i.planAvailable + credits - i.debt < i.tokens) return 'insufficient';
  return 'ok';
}

describe('allocate', () => {
  describe('properties', () => {
    it('should decide in the gate order: blocked, too many runs, too long, insufficient, then admit', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          expect(result.ok ? 'ok' : result.reason).toBe(expectedOutcome(i));
        }),
        { numRuns: 500 },
      );
    });

    it('should hold exactly the estimate when it admits a video, never more than a lot has', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (!result.ok) return;
          const { plan, credit, lots } = result.split;
          expect(plan + credit).toBe(i.tokens);
          expect(lots.reduce((sum, s) => sum + s.tokens, 0)).toBe(credit);
          for (const slice of lots) {
            const source = i.lots.find((l) => l.lotId === slice.lotId);
            expect(source && isLotSpendable(source, NOW)).toBe(true);
            expect(slice.tokens).toBeGreaterThan(0);
            expect(slice.tokens).toBeLessThanOrEqual(source?.tokensRemaining ?? 0);
          }
        }),
        { numRuns: 500 },
      );
    });

    it('should spend the plan first, then lots FIFO (a later lot only once every earlier one is empty)', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (!result.ok) return;
          if (result.split.credit > 0) expect(result.split.plan).toBe(i.planAvailable);
          const order = spendable(i);
          const used = result.split.lots.map((s) => s.lotId);
          expect(used).toEqual(order.slice(0, used.length).map((l) => l.lotId));
          result.split.lots.slice(0, -1).forEach((slice, n) => expect(slice.tokens).toBe(order[n].tokensRemaining));
        }),
        { numRuns: 500 },
      );
    });

    it('should report needed = the estimate and available = the balance (debt included) when it refuses for tokens', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (result.ok || result.reason !== 'insufficient') return;
          expect(result.needed).toBe(i.tokens);
          expect(result.available).toBe(balanceOf(i));
        }),
        { numRuns: 500 },
      );
    });
  });

  describe('spend order', () => {
    it('should split at the exact plan boundary: plan 300 left, estimate 840 → 300 plan + 540 credit', () => {
      const result = allocate(input({ planAvailable: 300, lots: [lot('a', 5000)] }));

      expect(result).toEqual({ ok: true, split: { plan: 300, credit: 540, lots: [{ lotId: 'a', tokens: 540 }] } });
    });

    it('should hold a covered estimate wholly on the plan', () => {
      expect(allocate(input({ planAvailable: 20_000, lots: [lot('a', 1000)] }))).toEqual({
        ok: true, split: { plan: 840, credit: 0, lots: [] },
      });
    });

    it('should drain the soonest-expiring lot first and never-expiring lots last', () => {
      const never = lot('never', 1000);
      const late = lot('late', 300, { expiresAt: new Date(NOW.getTime() + 100 * HOUR) });
      const soon = lot('soon', 200, { expiresAt: new Date(NOW.getTime() + 2 * HOUR) });

      const result = allocate(input({ lots: [never, late, soon] }));

      expect(result.ok && result.split.lots).toEqual([
        { lotId: 'soon', tokens: 200 },
        { lotId: 'late', tokens: 300 },
        { lotId: 'never', tokens: 340 },
      ]);
    });

    it('should order lots with the same expiry by creation, then id', () => {
      const created = new Date(NOW.getTime() - HOUR);
      const older = lot('b', 10, { createdAt: new Date(NOW.getTime() - 2 * HOUR) });

      expect([lot('c', 10, { createdAt: created }), older, lot('a', 10, { createdAt: created })].sort(compareLotsFifo).map((l) => l.lotId))
        .toEqual(['b', 'a', 'c']);
    });
  });

  describe('refusals', () => {
    it('should refuse a blocked account before anything else', () => {
      const everythingWrong = input({ caps: { blocked: true, maxConcurrent: 1 }, openHolds: 5, videoMinutes: 999, debt: 50 });

      expect(allocate(everythingWrong)).toEqual({ ok: false, reason: 'blocked' });
    });

    it('should refuse a new run once maxConcurrent runs are in flight, before the length check', () => {
      expect(allocate(input({ openHolds: 2, caps: { maxConcurrent: 2 }, videoMinutes: 999 }))).toEqual({
        ok: false, reason: 'too_many_runs', maxConcurrent: 2,
      });
    });

    it('should refuse a video over 180 minutes whatever the balance', () => {
      expect(allocate(input({ videoMinutes: 181, tokens: 3906, planAvailable: 20_000 }))).toEqual({
        ok: false, reason: 'too_long', maxMinutes: 180,
      });
    });

    it('should admit a 180-minute video that the balance covers', () => {
      expect(allocate(input({ videoMinutes: 180, tokens: 3900, planAvailable: 20_000 })).ok).toBe(true);
    });

    it('should lower the ceiling with caps.maxVideoMinutes but never raise it', () => {
      expect(allocate(input({ videoMinutes: 25, planAvailable: 20_000, caps: { maxVideoMinutes: 20 } }))).toEqual({
        ok: false, reason: 'too_long', maxMinutes: 20,
      });
      expect(allocate(input({ videoMinutes: 181, planAvailable: 20_000, caps: { maxVideoMinutes: 500 } }))).toEqual({
        ok: false, reason: 'too_long', maxMinutes: 180,
      });
    });

    it('should refuse when the balance is one token short of the estimate', () => {
      expect(allocate(input({ planAvailable: 839 }))).toEqual({ ok: false, reason: 'insufficient', needed: 840, available: 839 });
    });

    it('should refuse while the account owes anything, even with tokens to cover the estimate', () => {
      expect(allocate(input({ lots: [lot('a', 5000)], debt: 1 }))).toEqual({
        ok: false, reason: 'insufficient', needed: 840, available: 4999,
      });
    });

    it('should leave an expired lot out of what is available', () => {
      const expired = lot('old', 5000, { expiresAt: new Date(NOW.getTime() - HOUR) });

      expect(allocate(input({ lots: [expired, lot('ok', 500)] }))).toEqual({
        ok: false, reason: 'insufficient', needed: 840, available: 500,
      });
    });

    it('should treat a lot expiring exactly now as expired', () => {
      expect(isLotSpendable(lot('x', 5, { expiresAt: NOW }), NOW)).toBe(false);
    });
  });
});

describe('chargeSplit', () => {
  const state = (overrides: Partial<SpendableState> = {}): SpendableState => ({ now: NOW, planAvailable: 0, lots: [], debt: 0, ...overrides });

  it('should take the plan, then lots FIFO, and owe the rest', () => {
    const soon = lot('soon', 50, { expiresAt: new Date(NOW.getTime() + HOUR) });

    expect(chargeSplit(200, state({ planAvailable: 30, lots: [lot('never', 100), soon] }))).toEqual({
      plan: 30, credit: 150, lots: [{ lotId: 'soon', tokens: 50 }, { lotId: 'never', tokens: 100 }], debt: 20,
    });
  });

  it('should never refuse: an empty account owes the whole charge', () => {
    expect(chargeSplit(75, state())).toEqual({ plan: 0, credit: 0, lots: [], debt: 75 });
  });

  it('should always sum to the charge and owe only once the plan and every spendable lot are empty', () => {
    const stateArb = fc.record({
      now: fc.constant(NOW),
      planAvailable: fc.integer({ min: 0, max: 3000 }),
      lots: fc.uniqueArray(lotArb, { maxLength: 5, selector: (l) => l.lotId }),
      debt: fc.constant(0),
    });
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 10_000 }), stateArb, (tokens, s) => {
        const split = chargeSplit(tokens, s);
        expect(split.plan + split.credit + split.debt).toBe(tokens);
        expect(split.lots.reduce((sum, slice) => sum + slice.tokens, 0)).toBe(split.credit);
        if (split.debt > 0) expect(split.plan + split.credit).toBe(s.planAvailable + spendableCredit(s.lots, NOW));
      }),
      { numRuns: 300 },
    );
  });
});

describe('balanceOf', () => {
  it('should be plan + spendable credits − debt, negative while in debt', () => {
    const expired = lot('old', 400, { expiresAt: new Date(NOW.getTime() - HOUR) });

    expect(balanceOf({ now: NOW, planAvailable: 100, lots: [lot('a', 50), expired], debt: 0 })).toBe(150);
    expect(balanceOf({ now: NOW, planAvailable: 0, lots: [expired], debt: 120 })).toBe(-120);
  });
});

describe('maxVideoMinutes', () => {
  it.each([
    [{}, 180],
    [{ maxVideoMinutes: null }, 180],
    [{ maxVideoMinutes: 60 }, 60],
    [{ maxVideoMinutes: 500 }, 180],
  ])('should give %j a ceiling of %i minutes', (caps, minutes) => {
    expect(maxVideoMinutes(caps)).toBe(minutes);
  });
});
