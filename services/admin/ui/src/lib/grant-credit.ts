import type { GrantCreditRequest } from './api';

export const MAX_GRANT_MINUTES = 100_000;
export const MAX_REASON_LENGTH = 500;

export interface GrantFormValues {
  minutes: string;
  reason: string;
  /** `YYYY-MM-DD` from the date input, or ''. */
  expiry: string;
}

/** The request for the form's values, or an Error whose message says what to fix. */
export function buildGrantRequest(values: GrantFormValues, now: Date = new Date()): GrantCreditRequest {
  const minutes = Number(values.minutes);
  if (!Number.isInteger(minutes) || values.minutes.trim() === '') throw new Error('Enter a whole number of minutes');
  if (minutes < 1 || minutes > MAX_GRANT_MINUTES) {
    throw new Error(`Minutes must be between 1 and ${MAX_GRANT_MINUTES}`);
  }
  const reason = values.reason.trim();
  if (!reason) throw new Error('Reason is required');
  if (reason.length > MAX_REASON_LENGTH) throw new Error(`Reason must be at most ${MAX_REASON_LENGTH} characters`);
  if (!values.expiry) return { minutes, reason };

  // A date means "through the end of that day" in UTC.
  const expiresAt = new Date(`${values.expiry}T23:59:59.000Z`);
  if (Number.isNaN(expiresAt.getTime())) throw new Error('Expiry is not a valid date');
  if (expiresAt <= now) throw new Error('Expiry must be in the future');
  return { minutes, reason, expiresAt: expiresAt.toISOString() };
}
