import { describe, it, expect } from 'vitest';
import { ApiError } from '@/api/client';
import { classifyBillingCode, classifyBillingError, isTokensNotice } from '../billing-errors';

describe('classifyBillingCode', () => {
  it('should read the fields of INSUFFICIENT_TOKENS', () => {
    expect(classifyBillingCode('INSUFFICIENT_TOKENS', { needed: 840, available: 300 })).toEqual({
      kind: 'insufficient',
      needed: 840,
      available: 300,
    });
  });

  it('should keep a negative balance (an account in debt)', () => {
    expect(classifyBillingCode('INSUFFICIENT_TOKENS', { needed: 1, available: -120 })).toEqual({
      kind: 'insufficient',
      needed: 1,
      available: -120,
    });
  });

  it('should leave missing INSUFFICIENT_TOKENS fields null', () => {
    expect(classifyBillingCode('INSUFFICIENT_TOKENS')).toEqual({ kind: 'insufficient', needed: null, available: null });
  });

  it('should ignore fields of the wrong type', () => {
    const notice = classifyBillingCode('INSUFFICIENT_TOKENS', { needed: '840', available: Number.NaN });

    expect(notice).toEqual({ kind: 'insufficient', needed: null, available: null });
  });

  it('should read maxMinutes from VIDEO_TOO_LONG', () => {
    expect(classifyBillingCode('VIDEO_TOO_LONG', { maxMinutes: 180 })).toEqual({ kind: 'too_long', maxMinutes: 180 });
  });

  it('should map ACCOUNT_BLOCKED', () => {
    expect(classifyBillingCode('ACCOUNT_BLOCKED')).toEqual({ kind: 'blocked' });
  });

  it('should read maxConcurrent from TOO_MANY_RUNS', () => {
    expect(classifyBillingCode('TOO_MANY_RUNS', { maxConcurrent: 2 })).toEqual({
      kind: 'too_many_runs',
      maxConcurrent: 2,
    });
  });

  it('should read retryAfterSec from RATE_LIMITED', () => {
    expect(classifyBillingCode('RATE_LIMITED', { retryAfterSec: 840 })).toEqual({
      kind: 'rate_limited',
      retryAfterSec: 840,
    });
  });

  it.each([{}, { retryAfterSec: '840' }, { retryAfterSec: Number.NaN }])(
    'should leave retryAfterSec null when the body has %j',
    (details) => {
      expect(classifyBillingCode('RATE_LIMITED', details)).toEqual({ kind: 'rate_limited', retryAfterSec: null });
    },
  );

  it('should not carry the server message of RATE_LIMITED, which is English-only', () => {
    const notice = classifyBillingCode('RATE_LIMITED', { message: 'Max 20 videos per hour', retryAfterSec: 60 });

    expect(notice).not.toHaveProperty('message');
  });

  it.each([undefined, null, 'NOT_FOUND', 'VALIDATION_ERROR'])('should return null for %s', (code) => {
    expect(classifyBillingCode(code)).toBeNull();
  });

  it.each(['INSUFFICIENT_MINUTES', 'VIDEO_TOO_LONG_FOR_PLAN', 'CHAT_LIMIT_REACHED'])(
    'should not know the retired minutes-era code %s',
    (code) => {
      expect(classifyBillingCode(code, { needed: 31, available: 12, nextAt: '2026-10-29T10:00:00.000Z' })).toBeNull();
    },
  );
});

describe('classifyBillingError', () => {
  it('should classify an ApiError by its code and body', () => {
    const err = new ApiError(402, 'This needs 840 tokens; 300 available', 'INSUFFICIENT_TOKENS', {
      error: 'INSUFFICIENT_TOKENS',
      needed: 840,
      available: 300,
    });

    expect(classifyBillingError(err)).toEqual({ kind: 'insufficient', needed: 840, available: 300 });
  });

  it('should return null for an ApiError that is not a billing refusal', () => {
    expect(classifyBillingError(new ApiError(500, 'boom', 'INTERNAL_ERROR'))).toBeNull();
  });

  it('should return null for a plain Error', () => {
    expect(classifyBillingError(new Error('INSUFFICIENT_TOKENS'))).toBeNull();
  });
});

describe('isTokensNotice', () => {
  it.each([
    [{ kind: 'insufficient', needed: 1, available: 0 } as const, true],
    [{ kind: 'too_long', maxMinutes: 180 } as const, true],
    [{ kind: 'blocked' } as const, false],
    [{ kind: 'too_many_runs', maxConcurrent: 2 } as const, false],
    [{ kind: 'rate_limited', retryAfterSec: 60 } as const, false],
  ])('should say whether %j opens the tokens callout', (notice, expected) => {
    expect(isTokensNotice(notice)).toBe(expected);
  });
});
