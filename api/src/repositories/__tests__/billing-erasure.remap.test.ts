import { describe, it, expect } from 'vitest';
import { ObjectId } from 'mongodb';
import { ErasureIdMap, remapKey, replaceDeep } from '../billing-erasure.remap.js';

describe('ErasureIdMap', () => {
  it('should give one old id the same fresh id every time, so rows that shared it still do', () => {
    const ids = new ErasureIdMap();
    const old = new ObjectId();

    expect(ids.auditId(old).equals(ids.auditId(old.toHexString().toUpperCase()))).toBe(true);
  });

  it('should never hand back the old id or reuse a new one for a different old id', () => {
    const ids = new ErasureIdMap();
    const [a, b] = [new ObjectId(), new ObjectId()];

    expect([ids.auditId(a).equals(a), ids.auditId(a).equals(ids.auditId(b))]).toEqual([false, false]);
  });

  it.each([
    ['admin', true],
    ['migration', true],
    ['run', true],
    ['payment', true],
    ['system', false],
    ['recurring', false],
  ] as const)('should re-key a %s ref: %s', (kind, rekeyed) => {
    const ids = new ErasureIdMap();
    const ref = new ObjectId().toHexString();

    expect(ids.sourceRef(kind, ref) !== ref).toBe(rekeyed);
  });

  it('should map a run ref and the hold that names it to the same new run id', () => {
    const ids = new ErasureIdMap();

    expect(ids.sourceRef('run', 'run-7')).toBe(ids.runId('run-7'));
  });
});

describe('remapKey', () => {
  it.each([
    ['hold:run-1:free', 'run-1', 'new-run', 'hold:new-run:free'],
    ['admin:abc:lot:l1', 'abc', 'def', 'admin:def:lot:l1'],
    ['roll:acct:grant:2026-11-01', 'run-1', 'new-run', 'roll:acct:grant:2026-11-01'],
  ])('should rewrite %s with %s → %s', (key, oldRef, newRef, expected) => {
    expect(remapKey(key, oldRef, newRef)).toBe(expected);
  });
});

describe('replaceDeep', () => {
  const real = 'aaaaaaaaaaaaaaaaaaaaaaaa';

  it('should replace the id at any depth and leave everything else alone', () => {
    const value = { ownerId: real, nested: [{ who: real.toUpperCase() }, 'keep'], n: 3, at: null };

    expect(replaceDeep(value, real, 'tomb')).toEqual({ ownerId: 'tomb', nested: [{ who: 'tomb' }, 'keep'], n: 3, at: null });
  });

  it('should keep a Date or an ObjectId as it is', () => {
    const at = new Date('2026-11-01T00:00:00Z');
    const id = new ObjectId();

    expect(replaceDeep({ at, id }, real, 'tomb')).toEqual({ at, id });
  });
});
