import {
  AccountBlockedError,
  AppError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientTokensError,
  MetadataUnavailableError,
  TooManyRunsError,
  VideoTooLongError,
} from '../../utils/errors.js';
import type { AllocationRejection } from './allocation.js';

// The token gate's refusal → its domain error (the classes live in
// utils/errors.ts; error-envelope.ts puts their fields on the wire), and back:
// a thrown refusal named in one vocabulary (the submit paths).

/** Why the ledger refused (or could not decide). */
export type RefusalReason =
  | 'insufficient'
  | 'too_long'
  | 'blocked'
  | 'too_many_runs'
  | 'metadata_unavailable'
  | 'hold_finalized'
  | 'hold_conflict';

/**
 * Refusals that still let a video be served when serving it costs nothing —
 * already in the library, a completed row, or a live run to attach to (Q6).
 * Only a run needs tokens; a blocked account is refused outright. Read by
 * the submit paths (serve-only createVideo).
 */
export const SERVES_WITHOUT_RUN: ReadonlySet<string> = new Set<RefusalReason>([
  'insufficient',
  'too_long',
  'too_many_runs',
  'metadata_unavailable',
]);

export function rejectionToError(rejection: AllocationRejection): AppError {
  switch (rejection.reason) {
    case 'blocked':
      return new AccountBlockedError();
    case 'too_many_runs':
      return new TooManyRunsError(rejection.maxConcurrent);
    case 'too_long':
      return new VideoTooLongError(rejection.maxMinutes);
    case 'insufficient':
      return new InsufficientTokensError(rejection.needed, rejection.available);
  }
}

/** The ledger's refusal named from a thrown error; null when the error is not a ledger decision. */
export function refusalReason(err: unknown): RefusalReason | null {
  if (err instanceof MetadataUnavailableError) return 'metadata_unavailable';
  if (err instanceof HoldFinalizedError) return 'hold_finalized';
  if (err instanceof HoldConflictError) return 'hold_conflict';
  if (err instanceof AccountBlockedError) return 'blocked';
  if (err instanceof TooManyRunsError) return 'too_many_runs';
  if (err instanceof VideoTooLongError) return 'too_long';
  if (err instanceof InsufficientTokensError) return 'insufficient';
  return null;
}

/** True when the ledger's refusal `err` still lets a cached / attachable video be served (Q6). */
export function refusalServesWithoutRun(err: unknown): boolean {
  const reason = refusalReason(err);
  return reason !== null && SERVES_WITHOUT_RUN.has(reason);
}
