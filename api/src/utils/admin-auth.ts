import { timingSafeEqual } from 'node:crypto';
import { z } from 'zod';
import { config } from '../config.js';

// `x-admin-id` is operator metadata for audit rows, not auth. Constrain it so
// a multi-valued or oversized header can't pollute the audit log.
const adminIdSchema = z.string().min(1).max(64).regex(/^[A-Za-z0-9._-]+$/);

export type AdminIdHeader = { ok: true; adminId: string | null } | { ok: false };

/**
 * Collapse a possibly repeated `x-admin-id` header to its first value and
 * validate it. An absent header is fine (`adminId: null`); a malformed one
 * is not (`ok: false`, callers answer 400).
 */
export function parseAdminIdHeader(headerValue: string | string[] | undefined): AdminIdHeader {
  const raw = Array.isArray(headerValue) ? headerValue[0] : headerValue;
  if (raw === undefined) {
    return { ok: true, adminId: null };
  }
  const parsed = adminIdSchema.safeParse(raw);
  return parsed.success ? { ok: true, adminId: parsed.data } : { ok: false };
}

/**
 * Constant-time comparison of a request's `x-admin-key` header against the
 * configured admin API key. A plain `!==` short-circuits on the first differing
 * byte, leaking the key one character at a time via response timing; this
 * compares the full buffers in constant time. The length guard runs first
 * because `timingSafeEqual` throws on unequal-length buffers — leaking the
 * key's length is far less sensitive than leaking its contents.
 */
export function isValidAdminKey(headerValue: string | string[] | undefined): boolean {
  const provided = Buffer.from(Array.isArray(headerValue) ? '' : headerValue ?? '');
  const expected = Buffer.from(config.ADMIN_API_KEY);
  if (provided.length !== expected.length) {
    return false;
  }
  return timingSafeEqual(provided, expected);
}
