import { describe, it, expect } from 'vitest';
import { POLICY_DELTAS, classifyShadow, rejectionReason } from '../shadow-policy.js';
import type { ShadowFacts } from '../shadow-policy.js';

const ADMIT = { admitted: true, charged: true };
const SERVED_FREE = { admitted: true, charged: false };
const REFUSE = { admitted: false, charged: false };

function facts(overrides: Partial<ShadowFacts>): ShadowFacts {
  return { path: 'created', oldDecision: ADMIT, newDecision: ADMIT, ...overrides };
}

describe('classifyShadow', () => {
  it('should agree when both gates admit and charge alike', () => {
    expect(classifyShadow(facts({}))).toEqual({ classification: 'agree' });
  });

  it('should agree on a cached video served free by both', () => {
    expect(classifyShadow(facts({ path: 'cached', oldDecision: SERVED_FREE, newDecision: SERVED_FREE }))).toEqual({
      classification: 'agree',
    });
  });

  it.each([
    ['free_window_used', 'Q3'],
    ['needs_paid_minutes', 'Q10(a)'],
    ['free_disabled', 'caps.freeVideosPerDay'],
    ['too_long', 'Q2'],
    ['blocked', 'caps.blocked'],
    ['too_many_runs', 'caps.maxConcurrent'],
    ['metadata_unavailable', 'P1.5'],
  ])('should explain a ledger refusal for %s by %s', (reason, ref) => {
    const result = classifyShadow(facts({ newDecision: { ...REFUSE, reason } }));

    expect(result.classification).toBe('policy_delta');
    expect(result.policyRef).toContain(ref);
  });

  it('should explain the ledger admitting what the old $ cap refused (brief §4.1)', () => {
    const result = classifyShadow(facts({ path: 'refused', oldDecision: REFUSE, newDecision: SERVED_FREE }));

    expect(result).toMatchObject({ classification: 'policy_delta', policyRef: expect.stringContaining('§4.1') });
  });

  it('should explain a failed run released in full while the old gate kept its $', () => {
    const result = classifyShadow(facts({ path: 'failed', oldDecision: ADMIT, newDecision: SERVED_FREE }));

    expect(result).toMatchObject({ classification: 'policy_delta', policyRef: expect.stringContaining('released in full') });
  });

  it.each([
    ['a reused requestId', { ...REFUSE, reason: 'hold_finalized' }],
    ['a shadow error', { ...REFUSE, reason: 'shadow_error' }],
    ['a completed run the ledger did not charge', SERVED_FREE],
  ])('should disagree on %s', (_label, newDecision) => {
    const path = newDecision === SERVED_FREE ? 'completed' : 'created';

    expect(classifyShadow(facts({ path, newDecision })).classification).toBe('disagree');
  });

  it('should give every policy delta a decision reference', () => {
    expect(POLICY_DELTAS.every((delta) => delta.policyRef.length > 10)).toBe(true);
    expect(new Set(POLICY_DELTAS.map((delta) => delta.id)).size).toBe(POLICY_DELTAS.length);
  });
});

describe('rejectionReason', () => {
  const nextFreeAt = new Date();

  it.each([
    [{ ok: false as const, reason: 'insufficient' as const, needed: 10, available: 0, nextFreeAt }, 10, 'free_window_used'],
    [{ ok: false as const, reason: 'insufficient' as const, needed: 45, available: 0, nextFreeAt: null }, 45, 'needs_paid_minutes'],
    [{ ok: false as const, reason: 'insufficient' as const, needed: 10, available: 0, nextFreeAt: null }, 10, 'free_disabled'],
    [{ ok: false as const, reason: 'too_long' as const, maxMinutes: 180 }, 200, 'too_long'],
    [{ ok: false as const, reason: 'blocked' as const }, 10, 'blocked'],
    [{ ok: false as const, reason: 'too_many_runs' as const, maxConcurrent: 1 }, 10, 'too_many_runs'],
  ])('should name %j at %i minutes as %s', (rejection, minutes, reason) => {
    expect(rejectionReason(rejection, minutes)).toBe(reason);
  });
});
