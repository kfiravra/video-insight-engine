import { AppError, ConflictError } from '../../utils/errors.js';
import type { AllocationRejection } from './allocation.js';

// Domain errors of the minutes gate. In shadow mode (P1) nothing surfaces
// them to a user; P3.1 maps them onto the HTTP envelope (`needed`,
// `available`, `nextFreeAt`, `maxMinutes` as structured fields).

/** The account cannot pay for the video: `needed` minutes, `available` paid minutes. */
export class InsufficientMinutesError extends AppError {
  constructor(
    public readonly needed: number,
    public readonly available: number,
    public readonly nextFreeAt: string | null,
  ) {
    super('INSUFFICIENT_MINUTES', 402, `This video needs ${needed} minutes; ${available} available`);
    this.name = 'InsufficientMinutesError';
  }
}

/** Longer than any minutes on this account can pay for (paid 180, free 30, or an admin cap). */
export class VideoTooLongForPlanError extends AppError {
  constructor(public readonly maxMinutes: number) {
    super('VIDEO_TOO_LONG_FOR_PLAN', 422, `Videos up to ${maxMinutes} minutes are supported on this account`);
    this.name = 'VideoTooLongForPlanError';
  }
}

export class AccountBlockedError extends AppError {
  constructor() {
    super('ACCOUNT_BLOCKED', 403, 'This account cannot start new videos');
    this.name = 'AccountBlockedError';
  }
}

/** `caps.maxConcurrent` runs are already in flight. */
export class TooManyRunsError extends AppError {
  constructor(public readonly maxConcurrent: number) {
    super('TOO_MANY_RUNS', 429, `At most ${maxConcurrent} videos can run at once on this account`);
    this.name = 'TooManyRunsError';
  }
}

/** A hold was requested for a requestId whose run was already settled or released. */
export class HoldFinalizedError extends ConflictError {
  constructor() {
    super('HOLD_FINALIZED', 'This run has already been settled or released');
    this.name = 'HoldFinalizedError';
  }
}

export function rejectionToError(rejection: AllocationRejection): AppError {
  switch (rejection.reason) {
    case 'blocked':
      return new AccountBlockedError();
    case 'too_many_runs':
      return new TooManyRunsError(rejection.maxConcurrent);
    case 'too_long':
      return new VideoTooLongForPlanError(rejection.maxMinutes);
    case 'insufficient':
      return new InsufficientMinutesError(
        rejection.needed,
        rejection.available,
        rejection.nextFreeAt?.toISOString() ?? null,
      );
  }
}
