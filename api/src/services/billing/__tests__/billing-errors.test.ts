import { describe, it, expect } from 'vitest';
import {
  AccountBlockedError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientTokensError,
  MetadataUnavailableError,
  NotFoundError,
  TooManyRunsError,
  VideoTooLongError,
} from '../../../utils/errors.js';
import { refusalReason, refusalServesWithoutRun, rejectionToError, SERVES_WITHOUT_RUN } from '../billing-errors.js';
import type { AllocationRejection } from '../allocation.js';

describe('rejectionToError', () => {
  it.each([
    [{ ok: false, reason: 'blocked' }, AccountBlockedError, { code: 'ACCOUNT_BLOCKED', status: 403 }],
    [{ ok: false, reason: 'too_many_runs', maxConcurrent: 2 }, TooManyRunsError, { code: 'TOO_MANY_RUNS', status: 429, maxConcurrent: 2 }],
    [{ ok: false, reason: 'too_long', maxMinutes: 180 }, VideoTooLongError, { code: 'VIDEO_TOO_LONG', status: 422, maxMinutes: 180 }],
    [
      { ok: false, reason: 'insufficient', needed: 840, available: -120 },
      InsufficientTokensError,
      { code: 'INSUFFICIENT_TOKENS', status: 402, needed: 840, available: -120 },
    ],
  ] as Array<[AllocationRejection, new (...args: never[]) => Error, Record<string, unknown>]>)(
    'should turn %j into its domain error',
    (rejection, type, fields) => {
      const err = rejectionToError(rejection);

      expect(err).toBeInstanceOf(type);
      expect(err).toMatchObject(fields);
    },
  );
});

describe('refusalReason', () => {
  it.each([
    [new InsufficientTokensError(840, 100), 'insufficient'],
    [new VideoTooLongError(180), 'too_long'],
    [new AccountBlockedError(), 'blocked'],
    [new TooManyRunsError(1), 'too_many_runs'],
    [new MetadataUnavailableError('timeout'), 'metadata_unavailable'],
    [new HoldFinalizedError(), 'hold_finalized'],
    [new HoldConflictError(), 'hold_conflict'],
  ])('should name %s as %s', (err, reason) => {
    expect(refusalReason(err)).toBe(reason);
  });

  it('should name no reason for an error that is not a ledger decision', () => {
    expect(refusalReason(new NotFoundError('Video'))).toBeNull();
  });
});

describe('refusalServesWithoutRun', () => {
  it('should serve a cached video to an account short of tokens, over the length cap or at its run limit', () => {
    expect([
      refusalServesWithoutRun(new InsufficientTokensError(840, 0)),
      refusalServesWithoutRun(new VideoTooLongError(60)),
      refusalServesWithoutRun(new TooManyRunsError(1)),
      refusalServesWithoutRun(new MetadataUnavailableError('timeout')),
    ]).toEqual([true, true, true, true]);
  });

  it('should never serve a blocked account, a finished or a conflicting run', () => {
    expect([
      refusalServesWithoutRun(new AccountBlockedError()),
      refusalServesWithoutRun(new HoldFinalizedError()),
      refusalServesWithoutRun(new HoldConflictError()),
      refusalServesWithoutRun(new Error('boom')),
    ]).toEqual([false, false, false, false]);
  });

  it('should list exactly the refusals that still serve', () => {
    expect([...SERVES_WITHOUT_RUN].sort()).toEqual(['insufficient', 'metadata_unavailable', 'too_long', 'too_many_runs']);
  });
});
