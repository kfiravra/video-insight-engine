import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

/**
 * The signed `X-Admin-Actor` header vie-admin sends with every call it makes
 * for a named admin:
 *
 *   `<userId>.<unixTs>.<hex hmac-sha256(secret, `${userId}|${unixTs}|${sha256hex(body)}`)>`
 *
 * `body` is the exact request body (empty for a GET), so the header cannot be
 * replayed against another payload, and it is accepted for five minutes only.
 * vie-admin's `src/services/vie_api_client.py` signs it. Both sides are held to
 * the vectors in `packages/types/test-vectors/admin-auth.json`.
 *
 * Pure and config-free so operator scripts can sign without loading the
 * server config; `admin-auth.ts` binds it to `ADMIN_SESSION_SECRET`.
 */

export const ACTOR_HEADER_MAX_AGE_SECONDS = 300;

export const EMPTY_BODY_SHA256 = createHash('sha256').update('').digest('hex');

const USER_ID_PATTERN = /^[a-f0-9]{24}$/;
const TIMESTAMP_PATTERN = /^\d{1,12}$/;
const SIGNATURE_PATTERN = /^[a-f0-9]{64}$/;

export type ActorHeaderFailure = 'missing' | 'malformed' | 'bad_signature' | 'stale';

export type ActorHeaderResult = { ok: true; userId: string } | { ok: false; reason: ActorHeaderFailure };

export function sha256Hex(body: Buffer | string): string {
  return createHash('sha256').update(body).digest('hex');
}

function signature(secret: string, userId: string, unixTs: number, bodySha256: string): string {
  return createHmac('sha256', secret).update(`${userId}|${unixTs}|${bodySha256}`).digest('hex');
}

/** The header value for `userId` acting at `nowSeconds` with a body hashing to `bodySha256`. */
export function signActorHeader(
  secret: string,
  userId: string,
  bodySha256: string,
  nowSeconds: number,
): string {
  return `${userId}.${nowSeconds}.${signature(secret, userId, nowSeconds, bodySha256)}`;
}

/**
 * Check a header against the request body's hash. The signature is compared in
 * constant time and checked before freshness, so a caller without the secret
 * learns nothing about the clock; `stale` therefore means "correctly signed,
 * but more than five minutes away from now, in either direction".
 */
export function verifyActorSignature(
  secret: string,
  headerValue: string | string[] | undefined,
  bodySha256: string,
  nowSeconds: number,
): ActorHeaderResult {
  if (headerValue === undefined) return { ok: false, reason: 'missing' };
  // A repeated header is ambiguous; never pick one.
  if (Array.isArray(headerValue)) return { ok: false, reason: 'malformed' };

  const [userId, tsRaw, provided, ...extra] = headerValue.split('.');
  const wellFormed =
    extra.length === 0 &&
    USER_ID_PATTERN.test(userId ?? '') &&
    TIMESTAMP_PATTERN.test(tsRaw ?? '') &&
    SIGNATURE_PATTERN.test(provided ?? '');
  if (!wellFormed) return { ok: false, reason: 'malformed' };

  const unixTs = Number(tsRaw);
  const expected = Buffer.from(signature(secret, userId, unixTs, bodySha256));
  if (!timingSafeEqual(Buffer.from(provided), expected)) return { ok: false, reason: 'bad_signature' };
  if (Math.abs(nowSeconds - unixTs) > ACTOR_HEADER_MAX_AGE_SECONDS) return { ok: false, reason: 'stale' };
  return { ok: true, userId };
}
