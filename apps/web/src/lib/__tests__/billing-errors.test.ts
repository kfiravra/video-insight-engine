import { describe, it, expect } from 'vitest';
import { ApiError } from '@/api/client';
import { classifyBillingCode, classifyBillingError } from '../billing-errors';

describe('classifyBillingCode', () => {
  it('should read the fields of INSUFFICIENT_MINUTES', () => {
    const notice = classifyBillingCode('INSUFFICIENT_MINUTES', {
      needed: 31,
      available: 12,
      nextFreeAt: '2026-10-29T10:00:00.000Z',
    });

    expect(notice).toEqual({
      kind: 'insufficient',
      needed: 31,
      available: 12,
      nextFreeAt: '2026-10-29T10:00:00.000Z',
    });
  });

  it('should leave missing INSUFFICIENT_MINUTES fields null', () => {
    expect(classifyBillingCode('INSUFFICIENT_MINUTES')).toEqual({
      kind: 'insufficient',
      needed: null,
      available: null,
      nextFreeAt: null,
    });
  });

  it('should ignore fields of the wrong type', () => {
    const notice = classifyBillingCode('INSUFFICIENT_MINUTES', {
      needed: '31',
      available: Number.NaN,
      nextFreeAt: 5,
    });

    expect(notice).toEqual({ kind: 'insufficient', needed: null, available: null, nextFreeAt: null });
  });

  it('should read maxMinutes from VIDEO_TOO_LONG_FOR_PLAN', () => {
    expect(classifyBillingCode('VIDEO_TOO_LONG_FOR_PLAN', { maxMinutes: 180 })).toEqual({
      kind: 'too_long',
      maxMinutes: 180,
    });
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

  it('should prefer the body message of RATE_LIMITED, then the envelope message', () => {
    expect(classifyBillingCode('RATE_LIMITED', { message: 'Max 20 videos per hour' }, 'x')).toEqual({
      kind: 'rate_limited',
      message: 'Max 20 videos per hour',
    });
    expect(classifyBillingCode('RATE_LIMITED', {}, 'Slow down')).toEqual({
      kind: 'rate_limited',
      message: 'Slow down',
    });
  });

  it('should read nextAt from CHAT_LIMIT_REACHED', () => {
    expect(classifyBillingCode('CHAT_LIMIT_REACHED', { nextAt: '2026-10-29T10:00:00.000Z' })).toEqual({
      kind: 'chat_limit',
      nextAt: '2026-10-29T10:00:00.000Z',
    });
  });

  it.each([undefined, 'NOT_FOUND', 'VALIDATION_ERROR'])('should return null for %s', (code) => {
    expect(classifyBillingCode(code)).toBeNull();
  });
});

describe('classifyBillingError', () => {
  it('should classify an ApiError by its code and body', () => {
    const err = new ApiError(402, 'This video needs 31 minutes', 'INSUFFICIENT_MINUTES', {
      needed: 31,
      available: 12,
    });

    expect(classifyBillingError(err)).toEqual({
      kind: 'insufficient',
      needed: 31,
      available: 12,
      nextFreeAt: null,
    });
  });

  it('should return null for an ApiError that is not a billing refusal', () => {
    expect(classifyBillingError(new ApiError(500, 'boom', 'INTERNAL_ERROR'))).toBeNull();
  });

  it('should return null for a plain Error', () => {
    expect(classifyBillingError(new Error('INSUFFICIENT_MINUTES'))).toBeNull();
  });
});
