import { describe, it, expect, vi, afterEach } from 'vitest';
import { newIdempotencyKey } from './idempotency';

const ACCEPTED = /^[A-Za-z0-9_-]{8,128}$/;

describe('newIdempotencyKey', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should produce a key vie-api accepts', () => {
    expect(newIdempotencyKey()).toMatch(ACCEPTED);
  });

  it('should produce a different key each time', () => {
    expect(newIdempotencyKey()).not.toBe(newIdempotencyKey());
  });

  it('should still produce an accepted key without crypto.randomUUID', () => {
    vi.stubGlobal('crypto', {});

    expect(newIdempotencyKey()).toMatch(ACCEPTED);
  });
});
