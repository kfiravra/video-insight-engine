import { timingSafeEqual } from 'node:crypto';
import { config } from '../config.js';
import { verifyActorSignature, type ActorHeaderResult, type ActorRequest } from './admin-actor.js';

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

/**
 * Verify the `X-Admin-Actor` header vie-admin signs for a named admin, against
 * the request it arrived on (method, raw target, body hash) and
 * `ADMIN_SESSION_SECRET`. Freshness is five minutes either way. See
 * `admin-actor.ts` for the format.
 */
export function verifyActorHeader(
  headerValue: string | string[] | undefined,
  request: ActorRequest,
  nowSeconds: number = Math.floor(Date.now() / 1000),
): ActorHeaderResult {
  return verifyActorSignature(config.ADMIN_SESSION_SECRET, headerValue, request, nowSeconds);
}
