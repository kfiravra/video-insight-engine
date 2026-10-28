import {
  AccountBlockedError,
  AppError,
  InsufficientMinutesError,
  TooManyRunsError,
  VideoTooLongForPlanError,
} from '../../utils/errors.js';
import type { AllocationRejection } from './allocation.js';

// The minutes gate's refusal → its domain error (the classes live in
// utils/errors.ts; error-envelope.ts puts their fields on the wire).

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
