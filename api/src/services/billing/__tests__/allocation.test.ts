import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { FREE_VIDEO, PAID_MAX_VIDEO_MINUTES } from '@vie/types';
import { allocate, compareLotsFifo, freeWindowState, isLotSpendable } from '../allocation.js';
import type { AllocationInput, AllocationLot } from '../allocation.js';

const HOUR = 60 * 60 * 1000;
const NOW = new Date('2026-11-01T12:00:00Z');

function lot(id: string, minutesRemaining: number, overrides: Partial<AllocationLot> = {}): AllocationLot {
  return { lotId: id, minutesRemaining, expiresAt: null, createdAt: new Date(NOW.getTime() - 10 * HOUR), ...overrides };
}

function input(overrides: Partial<AllocationInput> = {}): AllocationInput {
  return {
    minutes: 10,
    now: NOW,
    recentFreeVideoAt: [],
    planAvailable: 0,
    lots: [],
    caps: {},
    openHolds: 0,
    ...overrides,
  };
}

/** Free video already used an hour ago — the window is full. */
const FREE_USED = [new Date(NOW.getTime() - HOUR)];

const lotArb = fc.record({
  lotId: fc.hexaString({ minLength: 4, maxLength: 4 }),
  minutesRemaining: fc.integer({ min: 0, max: 300 }),
  expiresAt: fc.option(fc.integer({ min: -48, max: 48 * 30 }).map((h) => new Date(NOW.getTime() + h * HOUR)), { nil: null }),
  createdAt: fc.integer({ min: 1, max: 24 * 365 }).map((h) => new Date(NOW.getTime() - h * HOUR)),
});

const inputArb = fc.record({
  minutes: fc.integer({ min: 3, max: 240 }),
  now: fc.constant(NOW),
  recentFreeVideoAt: fc.array(fc.integer({ min: 0, max: 48 }).map((h) => new Date(NOW.getTime() - h * HOUR)), { maxLength: 3 }),
  planAvailable: fc.integer({ min: 0, max: 600 }),
  lots: fc.uniqueArray(lotArb, { maxLength: 5, selector: (l) => l.lotId }),
  caps: fc.record(
    { maxVideoMinutes: fc.integer({ min: 3, max: 180 }), freeVideosPerDay: fc.integer({ min: 0, max: 3 }) },
    { requiredKeys: [] },
  ),
  openHolds: fc.constant(0),
});

describe('allocate', () => {
  describe('properties', () => {
    it('should take exactly the video minutes when it admits a video', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (!result.ok) return;
          const { free, plan, credit, lots } = result.split;
          expect(free + plan + credit).toBe(i.minutes);
          expect(lots.reduce((sum, s) => sum + s.minutes, 0)).toBe(credit);
        }),
      );
    });

    it('should use the free bucket for the whole video or not at all', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (result.ok) expect([0, i.minutes]).toContain(result.split.free);
        }),
      );
    });

    it('should never put a video over 30 minutes on the free bucket (Q10 a)', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (result.ok && i.minutes > FREE_VIDEO.maxVideoMinutes) expect(result.split.free).toBe(0);
        }),
      );
    });

    it('should spend plan minutes before credits and never overdraw a lot or use an unspendable one', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (!result.ok) return;
          if (result.split.credit > 0) expect(result.split.plan).toBe(i.planAvailable);
          for (const slice of result.split.lots) {
            const source = i.lots.find((l) => l.lotId === slice.lotId);
            expect(source && isLotSpendable(source, NOW)).toBe(true);
            expect(slice.minutes).toBeLessThanOrEqual(source?.minutesRemaining ?? 0);
          }
        }),
      );
    });

    it('should drain lots in FIFO order (a later lot only once every earlier one is empty)', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (!result.ok || result.split.lots.length === 0) return;
          const spendable = i.lots.filter((l) => isLotSpendable(l, NOW)).sort(compareLotsFifo);
          const used = result.split.lots.map((s) => s.lotId);
          expect(used).toEqual(spendable.slice(0, used.length).map((l) => l.lotId));
          result.split.lots.slice(0, -1).forEach((slice, n) => expect(slice.minutes).toBe(spendable[n].minutesRemaining));
        }),
      );
    });

    it('should never admit a video longer than the paid maximum or the account cap', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          const limit = Math.min(PAID_MAX_VIDEO_MINUTES, i.caps.maxVideoMinutes ?? Infinity);
          if (i.minutes > limit) expect(result.ok).toBe(false);
        }),
      );
    });

    it('should report needed = video minutes and available = spendable paid minutes when it cannot pay', () => {
      fc.assert(
        fc.property(inputArb, (i) => {
          const result = allocate(i);
          if (result.ok || result.reason !== 'insufficient') return;
          const credits = i.lots.filter((l) => isLotSpendable(l, NOW)).reduce((sum, l) => sum + l.minutesRemaining, 0);
          expect(result.needed).toBe(i.minutes);
          expect(result.available).toBe(i.planAvailable + credits);
          expect(result.available).toBeLessThan(i.minutes);
        }),
      );
    });

    it('should never let free videos exceed the cap inside any rolling 24 h window', () => {
      const scenario = fc.record({
        cap: fc.integer({ min: 0, max: 3 }),
        submits: fc.array(
          fc.record({ gapMinutes: fc.integer({ min: 0, max: 26 * 60 }), minutes: fc.integer({ min: 3, max: 40 }) }),
          { maxLength: 25 },
        ),
      });
      fc.assert(
        fc.property(scenario, ({ cap, submits }) => {
          let clock = NOW.getTime();
          const freeAt: Date[] = [];
          for (const { gapMinutes, minutes } of submits) {
            clock += gapMinutes * 60 * 1000;
            const now = new Date(clock);
            const result = allocate(input({ minutes, now, recentFreeVideoAt: freeAt, caps: { freeVideosPerDay: cap } }));
            if (result.ok && result.split.free > 0) freeAt.push(now);
            const inWindow = freeAt.filter((t) => t.getTime() > clock - 24 * HOUR);
            expect(inWindow.length).toBeLessThanOrEqual(cap);
          }
        }),
      );
    });

    it('should never cap plan or credit minutes per day — any number of videos run while minutes last', () => {
      const scenario = fc.array(fc.integer({ min: 31, max: 180 }), { minLength: 1, maxLength: 30 });
      fc.assert(
        fc.property(scenario, fc.integer({ min: 0, max: 1 }), (videos, planShare) => {
          const total = videos.reduce((a, b) => a + b, 0);
          let plan = planShare === 1 ? Math.floor(total / 2) : 0;
          const lots = [lot('a', total - plan)];
          for (const minutes of videos) {
            const result = allocate(input({ minutes, recentFreeVideoAt: FREE_USED, planAvailable: plan, lots }));
            expect(result.ok).toBe(true);
            if (!result.ok) return;
            plan -= result.split.plan;
            for (const s of result.split.lots) lots[0].minutesRemaining -= s.minutes;
          }
        }),
      );
    });
  });

  describe('spend order', () => {
    it('should give a fitting video to the free bucket whole even with plan minutes left', () => {
      const result = allocate(input({ minutes: 25, planAvailable: 600 }));

      expect(result).toEqual({ ok: true, split: { free: 25, plan: 0, credit: 0, lots: [] } });
    });

    it('should split at the exact plan boundary: plan 10 left, video 12 → 10 plan + 2 credit', () => {
      const result = allocate(input({ minutes: 12, recentFreeVideoAt: FREE_USED, planAvailable: 10, lots: [lot('a', 50)] }));

      expect(result).toEqual({ ok: true, split: { free: 0, plan: 10, credit: 2, lots: [{ lotId: 'a', minutes: 2 }] } });
    });

    it('should send a free user\'s 45-minute video wholly to credits and leave the free video unused (Q10 a)', () => {
      const result = allocate(input({ minutes: 45, lots: [lot('a', 100)] }));

      expect(result).toEqual({ ok: true, split: { free: 0, plan: 0, credit: 45, lots: [{ lotId: 'a', minutes: 45 }] } });
    });

    it('should drain the soonest-expiring lot first and never-expiring lots last', () => {
      const never = lot('never', 100);
      const late = lot('late', 5, { expiresAt: new Date(NOW.getTime() + 100 * HOUR) });
      const soon = lot('soon', 5, { expiresAt: new Date(NOW.getTime() + 2 * HOUR) });

      const result = allocate(input({ minutes: 40, recentFreeVideoAt: FREE_USED, lots: [never, late, soon] }));

      expect(result.ok && result.split.lots).toEqual([
        { lotId: 'soon', minutes: 5 },
        { lotId: 'late', minutes: 5 },
        { lotId: 'never', minutes: 30 },
      ]);
    });
  });

  describe('rolling free window', () => {
    const lastFree = new Date(NOW.getTime() - 24 * HOUR + 60 * 1000);

    it('should keep the free video closed at 23 h 59 and say when it opens', () => {
      const result = allocate(input({ minutes: 10, recentFreeVideoAt: [lastFree] }));

      expect(result).toEqual({
        ok: false, reason: 'insufficient', needed: 10, available: 0,
        nextFreeAt: new Date(lastFree.getTime() + 24 * HOUR),
      });
    });

    it('should reopen the free video at exactly 24 h 00', () => {
      const result = allocate(input({ minutes: 10, recentFreeVideoAt: [new Date(NOW.getTime() - 24 * HOUR)] }));

      expect(result.ok && result.split.free).toBe(10);
    });

    it('should not offer a next free time for a video too long to be free', () => {
      const result = allocate(input({ minutes: 45, recentFreeVideoAt: [lastFree] }));

      expect(!result.ok && result.reason === 'insufficient' && result.nextFreeAt).toBeNull();
    });
  });

  describe('lot expiry', () => {
    it('should exclude an expired lot from what is available', () => {
      const expired = lot('old', 100, { expiresAt: new Date(NOW.getTime() - HOUR) });

      const result = allocate(input({ minutes: 40, lots: [expired, lot('ok', 20)] }));

      expect(result).toEqual({ ok: false, reason: 'insufficient', needed: 40, available: 20, nextFreeAt: null });
    });

    it('should treat a lot expiring exactly now as expired', () => {
      expect(isLotSpendable(lot('x', 5, { expiresAt: NOW }), NOW)).toBe(false);
    });
  });

  describe('caps', () => {
    it('should refuse a blocked account even with minutes', () => {
      expect(allocate(input({ caps: { blocked: true }, lots: [lot('a', 100)] }))).toEqual({ ok: false, reason: 'blocked' });
    });

    it('should disable the free video when freeVideosPerDay is 0', () => {
      const result = allocate(input({ minutes: 10, caps: { freeVideosPerDay: 0 } }));

      expect(result).toEqual({ ok: false, reason: 'insufficient', needed: 10, available: 0, nextFreeAt: null });
    });

    it('should allow a second free video when freeVideosPerDay is 2', () => {
      const result = allocate(input({ minutes: 10, recentFreeVideoAt: FREE_USED, caps: { freeVideosPerDay: 2 } }));

      expect(result.ok && result.split.free).toBe(10);
    });

    it('should lower the video ceiling with maxVideoMinutes', () => {
      const result = allocate(input({ minutes: 25, recentFreeVideoAt: FREE_USED, lots: [lot('a', 100)], caps: { maxVideoMinutes: 20 } }));

      expect(result).toEqual({ ok: false, reason: 'too_long', maxMinutes: 20 });
    });

    it('should refuse a new run once maxConcurrent runs are in flight', () => {
      const result = allocate(input({ openHolds: 2, caps: { maxConcurrent: 2 } }));

      expect(result).toEqual({ ok: false, reason: 'too_many_runs', maxConcurrent: 2 });
    });
  });

  describe('length limits', () => {
    it('should refuse a video over 180 minutes whatever the balance (Q2)', () => {
      const result = allocate(input({ minutes: 181, planAvailable: 600, lots: [lot('a', 1000)] }));

      expect(result).toEqual({ ok: false, reason: 'too_long', maxMinutes: 180 });
    });

    it('should admit a 180-minute video on paid minutes', () => {
      const result = allocate(input({ minutes: 180, planAvailable: 600 }));

      expect(result.ok && result.split.plan).toBe(180);
    });
  });
});

describe('freeWindowState', () => {
  it('should name the oldest counted video + 24 h as the next free time when full', () => {
    const older = new Date(NOW.getTime() - 5 * HOUR);
    const newer = new Date(NOW.getTime() - 1 * HOUR);

    const state = freeWindowState([newer, older], { freeVideosPerDay: 2 }, NOW);

    expect(state.nextFreeAt).toEqual(new Date(older.getTime() + 24 * HOUR));
  });

  it('should report no next free time when the free video is disabled', () => {
    expect(freeWindowState([], { freeVideosPerDay: 0 }, NOW).nextFreeAt).toBeNull();
  });
});
