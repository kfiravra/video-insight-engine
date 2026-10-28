import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  ACTOR_HEADER_MAX_AGE_SECONDS,
  EMPTY_BODY_SHA256,
  sha256Hex,
  signActorHeader,
  verifyActorSignature,
} from './admin-actor.js';

interface ActorVector {
  name: string;
  userId: string;
  ts: number;
  body: string;
  bodySha256: string;
  header: string;
}

// Shared with services/admin/tests/test_vie_api_client.py: the Python signer
// and this verifier are held to the same bytes.
const vectors = JSON.parse(
  readFileSync(new URL('../../../packages/types/test-vectors/admin-auth.json', import.meta.url), 'utf8'),
) as { actorHeader: { secret: string; maxAgeSeconds: number; vectors: ActorVector[] } };
const { secret, maxAgeSeconds, vectors: actorVectors } = vectors.actorHeader;

const USER_ID = 'a1b2c3d4e5f60718293a4b5c';
const NOW = 1_790_000_000;

function headerFor(body: string, ts = NOW, userId = USER_ID): string {
  return signActorHeader(secret, userId, sha256Hex(body), ts);
}

describe('actor header golden vectors', () => {
  it('should use the same freshness window as the shared vectors', () => {
    expect(ACTOR_HEADER_MAX_AGE_SECONDS).toBe(maxAgeSeconds);
  });

  it.each(actorVectors.map((vector) => [vector.name, vector] as const))(
    'should hash the body to the golden digest: %s',
    (_name, vector) => {
      expect(sha256Hex(Buffer.from(vector.body, 'utf8'))).toBe(vector.bodySha256);
    },
  );

  it.each(actorVectors.map((vector) => [vector.name, vector] as const))(
    'should sign to the golden header: %s',
    (_name, vector) => {
      expect(signActorHeader(secret, vector.userId, vector.bodySha256, vector.ts)).toBe(vector.header);
    },
  );

  it.each(actorVectors.map((vector) => [vector.name, vector] as const))(
    'should accept the golden header at its own timestamp: %s',
    (_name, vector) => {
      const result = verifyActorSignature(secret, vector.header, vector.bodySha256, vector.ts);

      expect(result).toEqual({ ok: true, userId: vector.userId });
    },
  );

  it('should hash an empty body to the constant used for body-less requests', () => {
    expect(sha256Hex('')).toBe(EMPTY_BODY_SHA256);
  });
});

describe('verifyActorSignature', () => {
  const body = '{"reason":"duplicate upload"}';

  it('should accept a fresh header signed over the same body', () => {
    const result = verifyActorSignature(secret, headerFor(body), sha256Hex(body), NOW);

    expect(result).toEqual({ ok: true, userId: USER_ID });
  });

  it('should accept a header exactly five minutes old', () => {
    const result = verifyActorSignature(secret, headerFor(body), sha256Hex(body), NOW + 300);

    expect(result.ok).toBe(true);
  });

  it('should reject a header more than five minutes old as stale', () => {
    const result = verifyActorSignature(secret, headerFor(body), sha256Hex(body), NOW + 301);

    expect(result).toEqual({ ok: false, reason: 'stale' });
  });

  it('should reject a header timestamped more than five minutes in the future as stale', () => {
    const result = verifyActorSignature(secret, headerFor(body, NOW + 301), sha256Hex(body), NOW);

    expect(result).toEqual({ ok: false, reason: 'stale' });
  });

  it('should reject a header replayed against a different body', () => {
    const result = verifyActorSignature(secret, headerFor(body), sha256Hex('{"reason":"other"}'), NOW);

    expect(result).toEqual({ ok: false, reason: 'bad_signature' });
  });

  it('should reject a header whose user id was swapped', () => {
    const [, ts, signature] = headerFor(body).split('.');
    const swapped = `${'0'.repeat(24)}.${ts}.${signature}`;

    expect(verifyActorSignature(secret, swapped, sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a header whose timestamp was moved forward to look fresh', () => {
    const [userId, , signature] = headerFor(body, NOW - 3600).split('.');
    const refreshed = `${userId}.${NOW}.${signature}`;

    expect(verifyActorSignature(secret, refreshed, sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a header signed with a different secret', () => {
    const forged = signActorHeader('some-other-secret', USER_ID, sha256Hex(body), NOW);

    expect(verifyActorSignature(secret, forged, sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a flipped signature digit', () => {
    const header = headerFor(body);
    const flipped = `${header.slice(0, -1)}${header.endsWith('0') ? '1' : '0'}`;

    expect(verifyActorSignature(secret, flipped, sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should report a missing header', () => {
    expect(verifyActorSignature(secret, undefined, sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'missing',
    });
  });

  it.each([
    ['not three parts', 'abc.def'],
    ['four parts', `${headerFor(body)}.extra`],
    ['a user id that is not an ObjectId', `nope.${NOW}.${'a'.repeat(64)}`],
    ['a non-numeric timestamp', `${USER_ID}.soon.${'a'.repeat(64)}`],
    ['a signature that is not 64 hex digits', `${USER_ID}.${NOW}.abc`],
    ['an uppercase signature', `${USER_ID}.${NOW}.${'A'.repeat(64)}`],
    ['empty', ''],
  ])('should reject a malformed header: %s', (_name, header) => {
    expect(verifyActorSignature(secret, header, sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'malformed',
    });
  });

  it('should reject a repeated header rather than pick one', () => {
    const header = headerFor(body);

    expect(verifyActorSignature(secret, [header, header], sha256Hex(body), NOW)).toEqual({
      ok: false,
      reason: 'malformed',
    });
  });
});
