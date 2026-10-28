/**
 * A fresh `Idempotency-Key` for one user action (8–128 characters of
 * `A-Za-z0-9_-`, what vie-api accepts). Uniqueness is all that matters, not
 * secrecy, so the fallback for browsers without `crypto.randomUUID` is fine.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const random = Math.random().toString(36).slice(2, 12).padEnd(10, '0');
  return `k-${Date.now().toString(36)}-${random}`;
}
