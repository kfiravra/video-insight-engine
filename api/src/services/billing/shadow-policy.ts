import { FREE_VIDEO } from '@vie/types';
import type { BillingShadowDecision, BillingShadowPath } from '@vie/types';
import type { AllocationRejection } from './allocation.js';

// Contract 4: the old dollar gate and the minutes ledger disagree on purpose
// in some places. Every intended difference is one row of POLICY_DELTAS with
// the decision behind it; a difference no row explains is `disagree`, which
// the P1.11 replay harness fails on.

/** Why the ledger refused (or could not decide) — stored as `newDecision.reason`. */
export type ShadowReason =
  | 'free_window_used'
  | 'needs_paid_minutes'
  | 'free_disabled'
  | 'too_long'
  | 'blocked'
  | 'too_many_runs'
  | 'metadata_unavailable'
  | 'hold_finalized'
  | 'submit_error'
  | 'shadow_error';

export interface ShadowFacts {
  path: BillingShadowPath;
  oldDecision: BillingShadowDecision;
  newDecision: BillingShadowDecision & { reason?: string };
}

export interface ShadowClassification {
  classification: 'agree' | 'policy_delta' | 'disagree';
  policyRef?: string;
}

interface PolicyDelta {
  id: string;
  policyRef: string;
  applies: (facts: ShadowFacts) => boolean;
}

const refusedFor =
  (reason: ShadowReason) =>
  ({ oldDecision, newDecision }: ShadowFacts): boolean =>
    oldDecision.admitted && !newDecision.admitted && newDecision.reason === reason;

/** The intended differences, first match wins. Keep the refs pointing at a decision. */
export const POLICY_DELTAS: readonly PolicyDelta[] = [
  {
    id: 'free-window',
    policyRef: 'Q3: one free video (≤ 30 min) per rolling 24 h vs the old $/UTC-day cap',
    applies: refusedFor('free_window_used'),
  },
  {
    id: 'paid-minutes',
    policyRef: 'Q1/Q4/Q10(a): a video the free video cannot cover needs plan or credit minutes vs the old $/day allowance',
    applies: refusedFor('needs_paid_minutes'),
  },
  {
    id: 'free-disabled',
    policyRef: 'caps.freeVideosPerDay = 0: an admin turned the free video off (no old equivalent)',
    applies: refusedFor('free_disabled'),
  },
  {
    id: 'too-long',
    policyRef: 'Q2: video length checked at submit — free 30 min, paid 180 min (old gate: summarizer backstop only)',
    applies: refusedFor('too_long'),
  },
  {
    id: 'blocked',
    policyRef: 'caps.blocked: the per-account switch (P0 gate decision 2026-10-28; no old equivalent)',
    applies: refusedFor('blocked'),
  },
  {
    id: 'too-many-runs',
    policyRef: 'caps.maxConcurrent: admin cap on runs in flight (no old equivalent)',
    applies: refusedFor('too_many_runs'),
  },
  {
    id: 'metadata',
    policyRef: 'P1.5: the quote needs the video duration and fails closed without it when enforcing',
    applies: refusedFor('metadata_unavailable'),
  },
  {
    id: 'no-daily-cap',
    policyRef: 'brief §4.1 + Q3: no per-user $ cap — the free video rolls per 24 h and plan/credit minutes are never daily-capped',
    applies: ({ oldDecision, newDecision }) => !oldDecision.admitted && newDecision.admitted,
  },
  {
    id: 'failed-released',
    policyRef: 'proposal §4 settle + Contract 4: a failed run is released in full; the old gate kept its partial $',
    applies: ({ path, oldDecision, newDecision }) =>
      path === 'failed' && newDecision.admitted && oldDecision.charged && !newDecision.charged,
  },
];

export function classifyShadow(facts: ShadowFacts): ShadowClassification {
  const { oldDecision, newDecision } = facts;
  if (oldDecision.admitted === newDecision.admitted && oldDecision.charged === newDecision.charged) {
    return { classification: 'agree' };
  }
  const delta = POLICY_DELTAS.find((candidate) => candidate.applies(facts));
  return delta ? { classification: 'policy_delta', policyRef: delta.policyRef } : { classification: 'disagree' };
}

/** Name the ledger's refusal in the vocabulary the policy table matches on. */
export function rejectionReason(rejection: AllocationRejection, minutes: number): ShadowReason {
  switch (rejection.reason) {
    case 'blocked':
      return 'blocked';
    case 'too_many_runs':
      return 'too_many_runs';
    case 'too_long':
      return 'too_long';
    case 'insufficient':
      if (rejection.nextFreeAt) return 'free_window_used';
      // A video that fits the free limit and still found no slot: the free video is switched off.
      return minutes > FREE_VIDEO.maxVideoMinutes ? 'needs_paid_minutes' : 'free_disabled';
  }
}
