import type { GrantCreditRequest } from './api';
import { MAX_ADMIN_TOKENS, MAX_REASON_LENGTH } from './billing-forms';
import { formatNumber } from './format';

export interface GrantFormValues {
  tokens: string;
  reason: string;
  /** `YYYY-MM-DD` from the date input, or ''. */
  expiry: string;
}

/** The request for the form's values, or an Error whose message says what to fix. */
export function buildGrantRequest(values: GrantFormValues, now: Date = new Date()): GrantCreditRequest {
  const tokens = Number(values.tokens);
  if (!Number.isInteger(tokens) || values.tokens.trim() === '') throw new Error('Enter a whole number of tokens');
  if (tokens < 1 || tokens > MAX_ADMIN_TOKENS) {
    throw new Error(`Tokens must be between 1 and ${formatNumber(MAX_ADMIN_TOKENS)}`);
  }
  const reason = values.reason.trim();
  if (!reason) throw new Error('Reason is required');
  if (reason.length > MAX_REASON_LENGTH) throw new Error(`Reason must be at most ${MAX_REASON_LENGTH} characters`);
  if (!values.expiry) return { tokens, reason };

  // A date means "through the end of that day" in UTC.
  const expiresAt = new Date(`${values.expiry}T23:59:59.000Z`);
  if (Number.isNaN(expiresAt.getTime())) throw new Error('Expiry is not a valid date');
  if (expiresAt <= now) throw new Error('Expiry must be in the future');
  return { tokens, reason, expiresAt: expiresAt.toISOString() };
}
