import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

/**
 * The signed `X-Admin-Actor` header vie-admin sends with every call it makes
 * for a named admin:
 *
 *   `<userId>.<unixTs>.<hex hmac-sha256(secret,
 *      `actor|${METHOD}|${target}|${userId}|${unixTs}|${sha256hex(body)}`)>`
 *
 * It binds the HTTP method, the request target exactly as received (path plus
 * query, `req.raw.url`) and the body, so a captured header cannot be replayed
 * against another route, user or payload; the `actor|` prefix keeps it apart
 * from the `session|` messages vie-admin signs login tokens with under the same
 * secret. It is accepted for five minutes only, and the plugin lets a
 * non-GET signature through once (see `ReplayGuard`).
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

/** On success, who signed it and what to key a single-use record on. */
export interface ActorHeaderGrant {
  ok: true;
  userId: string;
  /** The header's own timestamp (unix seconds). */
  unixTs: number;
  /** Hex HMAC from the header; unique per (method, target, user, second, body). */
  signature: string;
}

export type ActorHeaderResult = ActorHeaderGrant | { ok: false; reason: ActorHeaderFailure };

export function sha256Hex(body: Buffer | string): string {
  return createHash('sha256').update(body).digest('hex');
}

/** What a signature binds besides who and when: the request itself. */
export interface ActorRequest {
  /** HTTP method; case-insensitive. */
  method: string;
  /** The request target as received, path plus query (`req.raw.url`), not a decoded or normalised form. */
  target: string;
  /** sha256 hex of the exact body bytes ('' for no body). */
  bodySha256: string;
}

function signature(secret: string, userId: string, unixTs: number, request: ActorRequest): string {
  const { method, target, bodySha256 } = request;
  const message = `actor|${method.toUpperCase()}|${target}|${userId}|${unixTs}|${bodySha256}`;
  return createHmac('sha256', secret).update(message).digest('hex');
}

/** The header value for `userId` making `request` at `nowSeconds`. */
export function signActorHeader(
  secret: string,
  userId: string,
  request: ActorRequest,
  nowSeconds: number,
): string {
  return `${userId}.${nowSeconds}.${signature(secret, userId, nowSeconds, request)}`;
}

/**
 * Check a header against the request it arrived on. The signature is compared
 * in constant time and checked before freshness, so a caller without the secret
 * learns nothing about the clock; `stale` therefore means "correctly signed,
 * but more than five minutes away from now, in either direction". A header
 * signed for another method, target or body is `bad_signature`.
 */
export function verifyActorSignature(
  secret: string,
  headerValue: string | string[] | undefined,
  request: ActorRequest,
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
  const expected = Buffer.from(signature(secret, userId, unixTs, request));
  if (!timingSafeEqual(Buffer.from(provided), expected)) return { ok: false, reason: 'bad_signature' };
  if (Math.abs(nowSeconds - unixTs) > ACTOR_HEADER_MAX_AGE_SECONDS) return { ok: false, reason: 'stale' };
  return { ok: true, userId, unixTs, signature: provided };
}
