import { describe, it, expect } from 'vitest';
import { ValidationError } from '../../../utils/errors.js';
import { addMonthsUtc, chargeMinutes, monthsElapsed, splitProportionally } from '../minutes.js';

describe('minutes', () => {
  describe('chargeMinutes', () => {
    it.each([
      [0, 3],
      [1, 3],
      [180, 3],
      [181, 4],
      [600, 10],
      [601, 11],
      [3600, 60],
      [10_800, 180],
    ])('should charge %i seconds as %i minutes', (seconds, minutes) => {
      expect(chargeMinutes(seconds)).toBe(minutes);
    });

    it.each([-1, Number.NaN, Number.POSITIVE_INFINITY])('should reject a duration of %s', (seconds) => {
      expect(() => chargeMinutes(seconds)).toThrow(ValidationError);
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
