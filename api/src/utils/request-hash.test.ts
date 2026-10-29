import { describe, it, expect } from 'vitest';
import { hashRequest } from './request-hash.js';

describe('hashRequest', () => {
  it('should not depend on the order of keys, at any depth', () => {
    const a = hashRequest({ x: 1, nested: { b: 2, a: 1 } });
    const b = hashRequest({ nested: { a: 1, b: 2 }, x: 1 });

    expect(a).toBe(b);
  });

  it('should tell different values apart', () => {
    expect(hashRequest({ minutes: 150 })).not.toBe(hashRequest({ minutes: 500 }));
  });

  it('should tell null from absent', () => {
    expect(hashRequest({ a: null })).not.toBe(hashRequest({}));
  });

  it('should keep array order', () => {
    expect(hashRequest([1, 2])).not.toBe(hashRequest([2, 1]));
  });

  it('should hash a Date by its instant', () => {
    expect(hashRequest({ at: new Date('2027-01-01T00:00:00Z') })).toBe(hashRequest({ at: new Date('2027-01-01T00:00:00.000Z') }));
  });

  it('should be a sha256 hex digest', () => {
    expect(hashRequest('x')).toMatch(/^[0-9a-f]{64}$/);
  });
});
