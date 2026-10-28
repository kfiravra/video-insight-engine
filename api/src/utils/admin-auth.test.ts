import { describe, it, expect } from 'vitest';
import { isValidAdminKey, verifyActorHeader } from './admin-auth.js';
import { sha256Hex, signActorHeader } from './admin-actor.js';
import { config } from '../config.js';

describe('isValidAdminKey', () => {
  it('should return true when the header matches the configured admin key', () => {
    expect(isValidAdminKey(config.ADMIN_API_KEY)).toBe(true);
  });

  it('should return false when the header does not match', () => {
    expect(isValidAdminKey('wrong-key')).toBe(false);
  });

  it('should return false when the header is undefined', () => {
    expect(isValidAdminKey(undefined)).toBe(false);
  });

  it('should return false when the header is an empty string', () => {
    expect(isValidAdminKey('')).toBe(false);
  });

  it('should return false when the header is an array (never trust duplicated headers)', () => {
    expect(isValidAdminKey([config.ADMIN_API_KEY])).toBe(false);
  });

  it('should return false for a correct prefix that is shorter than the key', () => {
    expect(isValidAdminKey(config.ADMIN_API_KEY.slice(0, -1))).toBe(false);
  });

  it('should return false for a same-length key that differs in one byte', () => {
    const flipped = `${config.ADMIN_API_KEY.slice(0, -1)}#`;
    expect(isValidAdminKey(flipped)).toBe(false);
  });
});

describe('verifyActorHeader', () => {
  const userId = 'a1b2c3d4e5f60718293a4b5c';
  const now = 1_790_000_000;
  const bodyHash = sha256Hex('{"max":5}');

  it('should accept a header signed with ADMIN_SESSION_SECRET', () => {
    const header = signActorHeader(config.ADMIN_SESSION_SECRET, userId, bodyHash, now);

    expect(verifyActorHeader(header, bodyHash, now)).toEqual({ ok: true, userId });
  });

  it('should reject a header signed with the machine key instead', () => {
    const header = signActorHeader(config.ADMIN_API_KEY, userId, bodyHash, now);

    expect(verifyActorHeader(header, bodyHash, now)).toEqual({ ok: false, reason: 'bad_signature' });
  });

  it('should judge freshness against the real clock when none is given', () => {
    const header = signActorHeader(config.ADMIN_SESSION_SECRET, userId, bodyHash, now);

    expect(verifyActorHeader(header, bodyHash)).toEqual({ ok: false, reason: 'stale' });
  });
});
