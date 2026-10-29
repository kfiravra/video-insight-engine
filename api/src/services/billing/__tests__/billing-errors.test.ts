import { describe, it, expect } from 'vitest';
import {
  AccountBlockedError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientMinutesError,
  MetadataUnavailableError,
  NotFoundError,
  TooManyRunsError,
  VideoTooLongForPlanError,
} from '../../../utils/errors.js';
import { refusalReason, refusalServesWithoutRun } from '../billing-errors.js';

describe('refusalReason', () => {
  it.each([
    [new InsufficientMinutesError(10, 0, '2026-11-02T10:00:00.000Z'), 'free_window_used'],
    [new InsufficientMinutesError(45, 0, null), 'needs_paid_minutes'],
    [new InsufficientMinutesError(10, 0, null), 'free_disabled'],
    [new VideoTooLongForPlanError(180), 'too_long'],
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
  it('should serve a cached video to an account out of minutes but never to a blocked one', () => {
    expect([refusalServesWithoutRun(new InsufficientMinutesError(45, 0, null)), refusalServesWithoutRun(new AccountBlockedError())])
      .toEqual([true, false]);
  });
});
