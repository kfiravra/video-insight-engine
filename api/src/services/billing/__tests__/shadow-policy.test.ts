import { describe, it, expect } from 'vitest';
import {
  AccountBlockedError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientMinutesError,
  MetadataUnavailableError,
  NotFoundError,
  TooManyRunsError,
  VideoTooLongForPlanError,
} from '../../../utils/errors.js';
import { POLICY_DELTAS, classifyShadow, errorReason, refusalServesWithoutRun } from '../shadow-policy.js';
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
  ])('should explain a ledger refusal for %s by %s', (reason, ref) => {
    const result = classifyShadow(facts({ newDecision: { ...REFUSE, reason } }));

    expect(result.classification).toBe('policy_delta');
    expect(result.policyRef).toContain(ref);
  });

  it('should class a missing duration on its own, never explained (W1)', () => {
    expect(classifyShadow(facts({ newDecision: { ...REFUSE, reason: 'metadata_unavailable' } }))).toEqual({
      classification: 'metadata_unavailable',
    });
  });

  it.each(['cached', 'attached'] as const)('should disagree on a %s video the ledger refused instead of serving free (C1)', (path) => {
    const verdict = classifyShadow(facts({ path, oldDecision: SERVED_FREE, newDecision: { ...REFUSE, reason: 'free_window_used' } }));

    expect(verdict.classification).toBe('disagree');
  });

  it('should still explain a blocked account getting nothing, even from cache', () => {
    const verdict = classifyShadow(facts({ path: 'cached', oldDecision: SERVED_FREE, newDecision: { ...REFUSE, reason: 'blocked' } }));

    expect(verdict).toMatchObject({ classification: 'policy_delta', policyRef: expect.stringContaining('caps.blocked') });
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

describe('errorReason', () => {
  it.each([
    [new InsufficientMinutesError(10, 0, '2026-11-02T10:00:00.000Z'), 'free_window_used'],
    [new InsufficientMinutesError(45, 0, null), 'needs_paid_minutes'],
    [new InsufficientMinutesError(10, 0, null), 'free_disabled'],
    [new VideoTooLongForPlanError(180), 'too_long'],
    [new AccountBlockedError(), 'blocked'],
    [new TooManyRunsError(1), 'too_many_runs'],
    [new MetadataUnavailableError('timeout'), 'metadata_unavailable'],
    [new HoldFinalizedError(), 'hold_finalized'],
    [new HoldConflictError(), 'hold_conflict'],
  ])('should name %s as %s', (err, reason) => {
    expect(errorReason(err)).toBe(reason);
  });

  it('should name no reason for an error that is not a ledger decision', () => {
    expect(errorReason(new NotFoundError('Video'))).toBeNull();
  });
});

describe('refusalServesWithoutRun', () => {
  it('should serve a cached video to an account out of minutes but never to a blocked one', () => {
    expect([refusalServesWithoutRun(new InsufficientMinutesError(45, 0, null)), refusalServesWithoutRun(new AccountBlockedError())])
      .toEqual([true, false]);
  });
});
