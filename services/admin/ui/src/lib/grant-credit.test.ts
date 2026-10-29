import { describe, it, expect } from 'vitest';
import { buildGrantRequest } from './grant-credit';

const NOW = new Date('2026-11-01T10:00:00Z');
const base = { minutes: '150', reason: 'goodwill', expiry: '' };

describe('buildGrantRequest', () => {
  it('should build a request with no expiry by default', () => {
    expect(buildGrantRequest(base, NOW)).toEqual({ minutes: 150, reason: 'goodwill' });
  });

  it('should trim the reason', () => {
    expect(buildGrantRequest({ ...base, reason: '  support ticket 42  ' }, NOW).reason).toBe('support ticket 42');
  });

  it('should expire a chosen date at the end of that day in UTC', () => {
    const request = buildGrantRequest({ ...base, expiry: '2027-01-31' }, NOW);

    expect(request.expiresAt).toBe('2027-01-31T23:59:59.000Z');
  });

  it('should have no adminId or dollar field: the server knows who is granting', () => {
    expect(Object.keys(buildGrantRequest({ ...base, expiry: '2027-01-31' }, NOW)).sort()).toEqual([
      'expiresAt',
      'minutes',
      'reason',
    ]);
  });

  it.each(['', '  ', 'abc', '1.5', '0', '-5', '100001'])('should reject %j minutes', (minutes) => {
    expect(() => buildGrantRequest({ ...base, minutes }, NOW)).toThrow(/minutes/i);
  });

  it('should accept the limits', () => {
    expect(buildGrantRequest({ ...base, minutes: '1' }, NOW).minutes).toBe(1);
    expect(buildGrantRequest({ ...base, minutes: '100000' }, NOW).minutes).toBe(100_000);
  });

  it('should require a reason', () => {
    expect(() => buildGrantRequest({ ...base, reason: '   ' }, NOW)).toThrow('Reason is required');
  });

  it('should reject a reason over 500 characters', () => {
    expect(() => buildGrantRequest({ ...base, reason: 'x'.repeat(501) }, NOW)).toThrow(/at most 500/);
  });

  it('should reject an expiry that is not in the future', () => {
    expect(() => buildGrantRequest({ ...base, expiry: '2026-10-31' }, NOW)).toThrow('Expiry must be in the future');
  });

  it('should accept an expiry today, which runs to the end of the day', () => {
    expect(buildGrantRequest({ ...base, expiry: '2026-11-01' }, NOW).expiresAt).toBe('2026-11-01T23:59:59.000Z');
  });

  it('should reject a date it cannot read', () => {
    expect(() => buildGrantRequest({ ...base, expiry: '2026-13-45' }, NOW)).toThrow('Expiry is not a valid date');
  });
});
