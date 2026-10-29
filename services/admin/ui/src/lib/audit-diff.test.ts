import { describe, it, expect } from 'vitest';
import { diffAudit } from './audit-diff';

describe('diffAudit', () => {
  it('should list only the fields that changed', () => {
    const before = { plan: { id: 'free' }, credits: { available: 0 } };
    const after = { plan: { id: 'pro' }, credits: { available: 0 } };

    expect(diffAudit(before, after)).toEqual([{ path: 'plan.id', before: 'free', after: 'pro' }]);
  });

  it('should walk nested objects into dotted paths, sorted', () => {
    const before = { planMinutes: { granted: 0, used: 0 }, caps: {} };
    const after = { planMinutes: { granted: 600, used: 0 }, caps: { maxConcurrent: 2 } };

    expect(diffAudit(before, after).map((line) => line.path)).toEqual(['caps.maxConcurrent', 'planMinutes.granted']);
  });

  it('should ignore version and updatedAt, which change on every write', () => {
    const before = { version: 1, updatedAt: '2026-10-01', plan: { id: 'free' } };
    const after = { version: 2, updatedAt: '2026-10-02', plan: { id: 'free' } };

    expect(diffAudit(before, after)).toEqual([]);
  });

  it('should show a field that was added with no before value', () => {
    expect(diffAudit({}, { lotId: 'abc' })).toEqual([{ path: 'lotId', before: null, after: 'abc' }]);
  });

  it('should show a field that was removed with no after value', () => {
    expect(diffAudit({ blocked: true }, {})).toEqual([{ path: 'blocked', before: 'true', after: null }]);
  });

  it('should compare arrays whole', () => {
    const lines = diffAudit({ ids: [1, 2] }, { ids: [1, 3] });

    expect(lines).toEqual([{ path: 'ids', before: '[1,2]', after: '[1,3]' }]);
  });

  it('should treat a null value as a value, not as missing', () => {
    expect(diffAudit({ expiresAt: null }, { expiresAt: '2027-01-01' })).toEqual([
      { path: 'expiresAt', before: 'null', after: '2027-01-01' },
    ]);
  });

  it('should show everything as added when there is no before', () => {
    expect(diffAudit(undefined, { action: 'purge' })).toEqual([{ path: 'action', before: null, after: 'purge' }]);
  });

  it('should diff plain values under one name', () => {
    expect(diffAudit('a', 'b')).toEqual([{ path: '(value)', before: 'a', after: 'b' }]);
  });

  it('should return nothing when both sides are missing', () => {
    expect(diffAudit(undefined, undefined)).toEqual([]);
  });

  it('should shorten a very long value', () => {
    const [line] = diffAudit({ note: 'a' }, { note: 'b'.repeat(500) });

    expect(line.after?.length).toBe(121);
  });
});
