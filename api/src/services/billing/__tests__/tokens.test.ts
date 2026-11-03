import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { ValidationError } from '../../../utils/errors.js';
import {
  addMonthsUtc,
  estimateTokens,
  monthsElapsed,
  splitProportionally,
  tokensForUsd,
  videoMinutes,
} from '../tokens.js';

describe('tokens', () => {
  describe('tokensForUsd', () => {
    it.each([
      [0.25, 750],
      [0.2526, 758],
      [0.481, 1443],
      [1.3, 3900],
      [0.001, 3],
      [0.0001, 1],
    ])('should charge $%d of LLM cost as %i tokens (3× markup, $0.001 a token)', (usd, tokens) => {
      expect(tokensForUsd(usd)).toBe(tokens);
    });

    it('should round float noise away instead of charging a token for it (0.1 × 3 × 1000 = 300.00000000000006)', () => {
      expect(0.1 * 3 * 1000).not.toBe(300);
      expect([tokensForUsd(0.1), tokensForUsd(0.25), tokensForUsd(0.28)]).toEqual([300, 750, 840]);
    });

    it('should round a real fraction up', () => {
      expect(tokensForUsd(0.2502)).toBe(751);
    });

    it('should charge at least 1 token when anything was spent', () => {
      expect([tokensForUsd(1e-9), tokensForUsd(1e-12)]).toEqual([1, 1]);
    });

    it('should charge 0 tokens for a free execution', () => {
      expect(tokensForUsd(0)).toBe(0);
    });

    it.each([-0.01, Number.NaN, Number.POSITIVE_INFINITY])('should reject a cost of %s', (usd) => {
      expect(() => tokensForUsd(usd)).toThrow(ValidationError);
    });

    it('should be monotonic, whole and never below cost × 3000', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 5_000_000 }), fc.integer({ min: 0, max: 1000 }), (micros, extra) => {
          const low = micros / 1e6;
          const high = (micros + extra) / 1e6;
          const tokens = tokensForUsd(low);
          expect(Number.isInteger(tokens)).toBe(true);
          expect(tokens).toBeGreaterThanOrEqual(1);
          expect(tokens).toBeGreaterThanOrEqual(Math.round(low * 3000 * 1e6) / 1e6);
          expect(tokensForUsd(high)).toBeGreaterThanOrEqual(tokens);
        }),
      );
    });
  });

  describe('videoMinutes', () => {
    it.each([
      [0, 0],
      [1, 1],
      [60, 1],
      [61, 2],
      [222, 4],
      [10_800, 180],
    ])('should count %i seconds as %i minutes', (seconds, minutes) => {
      expect(videoMinutes(seconds)).toBe(minutes);
    });

    it.each([-1, Number.NaN, Number.POSITIVE_INFINITY])('should reject a duration of %s', (seconds) => {
      expect(() => videoMinutes(seconds)).toThrow(ValidationError);
    });
  });

  describe('estimateTokens', () => {
    it.each([
      [5, 750],
      [10, 840],
      [15, 930],
      [18, 984],
      [20, 1020],
      [30, 1200],
      [60, 1740],
      [100, 2460],
      [180, 3900],
    ])('should estimate a %i-minute video at %i tokens', (minutes, tokens) => {
      expect(estimateTokens(minutes * 60)).toBe(tokens);
    });

    it('should estimate an unknown length (0 s) at the fixed part alone', () => {
      expect(estimateTokens(0)).toBe(660);
    });

    it('should count a partial minute as a whole one (222 s → 4 min)', () => {
      expect(estimateTokens(222)).toBe(732);
      expect(estimateTokens(222)).toBe(estimateTokens(240));
    });

    it('should let the 1,000-token signup grant cover an 18-minute video but not a 20-minute one', () => {
      expect([estimateTokens(18 * 60) <= 1000, estimateTokens(20 * 60) <= 1000]).toEqual([true, false]);
    });

    it('should never fall when the video gets longer', () => {
      fc.assert(
        fc.property(fc.integer({ min: 0, max: 10_800 }), fc.integer({ min: 0, max: 600 }), (seconds, more) => {
          expect(estimateTokens(seconds + more)).toBeGreaterThanOrEqual(estimateTokens(seconds));
        }),
      );
    });

    it('should reject a negative duration', () => {
      expect(() => estimateTokens(-1)).toThrow(ValidationError);
    });
  });

  describe('addMonthsUtc', () => {
    it('should clamp to the last day of a shorter month', () => {
      expect(addMonthsUtc(new Date('2026-01-31T10:00:00Z'), 1).toISOString()).toBe('2026-02-28T10:00:00.000Z');
    });

    it('should land on Feb 29 in a leap year', () => {
      expect(addMonthsUtc(new Date('2028-01-31T00:00:00Z'), 1).toISOString()).toBe('2028-02-29T00:00:00.000Z');
    });

    it('should not drift when computed from the same anchor', () => {
      expect(addMonthsUtc(new Date('2026-01-31T00:00:00Z'), 2).toISOString()).toBe('2026-03-31T00:00:00.000Z');
    });

    it('should cross a year boundary', () => {
      expect(addMonthsUtc(new Date('2026-11-15T00:00:00Z'), 3).toISOString()).toBe('2027-02-15T00:00:00.000Z');
    });
  });

  describe('monthsElapsed', () => {
    const anchor = new Date('2026-01-31T12:00:00Z');

    it('should be 0 inside the first month', () => {
      expect(monthsElapsed(anchor, new Date('2026-02-28T11:59:59Z'))).toBe(0);
    });

    it('should count a month at the clamped boundary', () => {
      expect(monthsElapsed(anchor, new Date('2026-02-28T12:00:00Z'))).toBe(1);
    });

    it('should count idle months', () => {
      expect(monthsElapsed(anchor, new Date('2026-06-01T00:00:00Z'))).toBe(4);
    });

    it('should never be negative', () => {
      expect(monthsElapsed(anchor, new Date('2025-12-01T00:00:00Z'))).toBe(0);
    });
  });

  describe('splitProportionally', () => {
    it('should split by weight and sum exactly to the total', () => {
      const shares = splitProportionally(0.1, [1, 1, 1]);

      expect(shares.reduce((a, b) => a + b, 0)).toBeCloseTo(0.1, 9);
      expect(shares[0]).toBeCloseTo(0.033333, 6);
    });

    it('should give zero shares when every weight is zero', () => {
      expect(splitProportionally(1, [0, 0])).toEqual([0, 0]);
    });
  });
});
