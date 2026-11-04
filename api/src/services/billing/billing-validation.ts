import { ValidationError } from '../../utils/errors.js';

// Input checks shared by the ledger's run and execution paths.

const MAX_REQUEST_ID_LENGTH = 200;

/** A run id, request id or execution id: 1–200 characters. */
export function validateRequestId(requestId: string, label = 'requestId'): void {
  if (requestId.length === 0 || requestId.length > MAX_REQUEST_ID_LENGTH) {
    throw new ValidationError(`${label} must be 1–${MAX_REQUEST_ID_LENGTH} characters`);
  }
}

/** An LLM cost in dollars, when given: finite and not negative. */
export function validateCost(costUsd: number | undefined): void {
  if (costUsd !== undefined && (!Number.isFinite(costUsd) || costUsd < 0)) {
    throw new ValidationError('costUsd must be a finite, non-negative number');
  }
}
