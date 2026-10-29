import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  ACTOR_HEADER_MAX_AGE_SECONDS,
  EMPTY_BODY_SHA256,
  sha256Hex,
  signActorHeader,
  verifyActorSignature,
} from './admin-actor.js';
import type { ActorRequest } from './admin-actor.js';

interface ActorVector {
  name: string;
  method: string;
  target: string;
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
const TARGET = '/api/admin/billing/accounts/aaaaaaaaaaaaaaaaaaaaaaaa/credits';

const requestOf = (vector: ActorVector): ActorRequest => ({
  method: vector.method,
  target: vector.target,
  bodySha256: vector.bodySha256,
});

function post(body: string, target = TARGET): ActorRequest {
  return { method: 'POST', target, bodySha256: sha256Hex(body) };
}

function headerFor(request: ActorRequest, ts = NOW, userId = USER_ID): string {
  return signActorHeader(secret, userId, request, ts);
}

describe('actor header golden vectors', () => {
  it('should use the same freshness window as the shared vectors', () => {
    expect(ACTOR_HEADER_MAX_AGE_SECONDS).toBe(maxAgeSeconds);
  });

  it('should cover a GET, a query string, a body, a path parameter and non-ASCII text', () => {
    expect(actorVectors.map((vector) => vector.method)).toEqual(['GET', 'GET', 'POST', 'DELETE', 'POST']);
    expect(actorVectors.some((vector) => vector.target.includes('?'))).toBe(true);
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
      expect(signActorHeader(secret, vector.userId, requestOf(vector), vector.ts)).toBe(vector.header);
    },
  );

  it.each(actorVectors.map((vector) => [vector.name, vector] as const))(
    'should accept the golden header on its own request at its own timestamp: %s',
    (_name, vector) => {
      const result = verifyActorSignature(secret, vector.header, requestOf(vector), vector.ts);

      expect(result).toMatchObject({ ok: true, userId: vector.userId, unixTs: vector.ts });
    },
  );

  it.each(actorVectors.map((vector) => [vector.name, vector] as const))(
    'should refuse the golden header on another method: %s',
    (_name, vector) => {
      const otherMethod = vector.method === 'GET' ? 'DELETE' : 'GET';

      const result = verifyActorSignature(secret, vector.header, { ...requestOf(vector), method: otherMethod }, vector.ts);

      expect(result).toEqual({ ok: false, reason: 'bad_signature' });
    },
  );

  it.each(actorVectors.map((vector) => [vector.name, vector] as const))(
    'should refuse the golden header on another target: %s',
    (_name, vector) => {
      const result = verifyActorSignature(secret, vector.header, { ...requestOf(vector), target: `${vector.target}x` }, vector.ts);

      expect(result).toEqual({ ok: false, reason: 'bad_signature' });
    },
  );

  it('should hash an empty body to the constant used for body-less requests', () => {
    expect(sha256Hex('')).toBe(EMPTY_BODY_SHA256);
  });
});

describe('verifyActorSignature', () => {
  const body = '{"reason":"duplicate upload"}';

  it('should accept a fresh header signed for the same request', () => {
    const request = post(body);

    expect(verifyActorSignature(secret, headerFor(request), request, NOW)).toMatchObject({ ok: true, userId: USER_ID });
  });

  it('should hand back the timestamp and signature for the single-use record', () => {
    const request = post(body);
    const header = headerFor(request);

    const result = verifyActorSignature(secret, header, request, NOW);

    expect(result).toEqual({ ok: true, userId: USER_ID, unixTs: NOW, signature: header.split('.')[2] });
  });

  it('should treat the method as case-insensitive', () => {
    const request = post(body);

    expect(verifyActorSignature(secret, headerFor(request), { ...request, method: 'post' }, NOW).ok).toBe(true);
  });

  it('should accept a header exactly five minutes old', () => {
    const request = post(body);

    expect(verifyActorSignature(secret, headerFor(request), request, NOW + 300).ok).toBe(true);
  });

  it('should reject a header more than five minutes old as stale', () => {
    const request = post(body);

    expect(verifyActorSignature(secret, headerFor(request), request, NOW + 301)).toEqual({ ok: false, reason: 'stale' });
  });

  it('should reject a header timestamped more than five minutes in the future as stale', () => {
    const request = post(body);

    expect(verifyActorSignature(secret, headerFor(request, NOW + 301), request, NOW)).toEqual({ ok: false, reason: 'stale' });
  });

  it('should reject a header replayed against a different body', () => {
    const signed = post(body);

    expect(verifyActorSignature(secret, headerFor(signed), post('{"reason":"other"}'), NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a header replayed against a different method', () => {
    const signed = post(body);

    expect(verifyActorSignature(secret, headerFor(signed), { ...signed, method: 'DELETE' }, NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a header replayed against a different path', () => {
    const signed = post(body);
    const elsewhere = post(body, '/api/admin/billing/accounts/bbbbbbbbbbbbbbbbbbbbbbbb/credits');

    expect(verifyActorSignature(secret, headerFor(signed), elsewhere, NOW)).toEqual({ ok: false, reason: 'bad_signature' });
  });

  it('should reject a header replayed with a query string added', () => {
    const signed = post(body, '/api/admin/users/aaaaaaaaaaaaaaaaaaaaaaaa');

    const withQuery = post(body, '/api/admin/users/aaaaaaaaaaaaaaaaaaaaaaaa?immediate=true');

    expect(verifyActorSignature(secret, headerFor(signed), withQuery, NOW)).toEqual({ ok: false, reason: 'bad_signature' });
  });

  it('should bind the raw target: a percent-encoded form of the same route is a different request', () => {
    const signed = post(body, '/api/admin/queue/replay');

    const encoded = post(body, '/api/%61dmin/queue/replay');

    expect(verifyActorSignature(secret, headerFor(signed), encoded, NOW)).toEqual({ ok: false, reason: 'bad_signature' });
  });

  it('should reject a header whose user id was swapped', () => {
    const request = post(body);
    const [, ts, signature] = headerFor(request).split('.');

    expect(verifyActorSignature(secret, `${'0'.repeat(24)}.${ts}.${signature}`, request, NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a header whose timestamp was moved forward to look fresh', () => {
    const request = post(body);
    const [userId, , signature] = headerFor(request, NOW - 3600).split('.');

    expect(verifyActorSignature(secret, `${userId}.${NOW}.${signature}`, request, NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a header signed with a different secret', () => {
    const request = post(body);

    expect(verifyActorSignature(secret, signActorHeader('some-other-secret', USER_ID, request, NOW), request, NOW)).toEqual({
      ok: false,
      reason: 'bad_signature',
    });
  });

  it('should reject a flipped signature digit', () => {
    const request = post(body);
    const header = headerFor(request);
    const flipped = `${header.slice(0, -1)}${header.endsWith('0') ? '1' : '0'}`;

    expect(verifyActorSignature(secret, flipped, request, NOW)).toEqual({ ok: false, reason: 'bad_signature' });
  });

  it('should report a missing header', () => {
    expect(verifyActorSignature(secret, undefined, post(body), NOW)).toEqual({ ok: false, reason: 'missing' });
  });

  it.each([
    ['not three parts', 'abc.def'],
    ['four parts', `${headerFor(post(body))}.extra`],
    ['a user id that is not an ObjectId', `nope.${NOW}.${'a'.repeat(64)}`],
    ['a non-numeric timestamp', `${USER_ID}.soon.${'a'.repeat(64)}`],
    ['a signature that is not 64 hex digits', `${USER_ID}.${NOW}.abc`],
    ['an uppercase signature', `${USER_ID}.${NOW}.${'A'.repeat(64)}`],
    ['empty', ''],
  ])('should reject a malformed header: %s', (_name, header) => {
    expect(verifyActorSignature(secret, header, post(body), NOW)).toEqual({ ok: false, reason: 'malformed' });
  });

  it('should reject a repeated header rather than pick one', () => {
    const request = post(body);
    const header = headerFor(request);

    expect(verifyActorSignature(secret, [header, header], request, NOW)).toEqual({ ok: false, reason: 'malformed' });
  });
});
