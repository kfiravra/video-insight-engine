import { FREE_VIDEO } from '@vie/types';
import {
  AccountBlockedError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientMinutesError,
  MetadataUnavailableError,
  TooManyRunsError,
  VideoTooLongForPlanError,
} from '../../utils/errors.js';
import type { BillingShadowDecision, BillingShadowPath } from '@vie/types';
import type { AllocationRejection } from './allocation.js';

// Contract 4: the old dollar gate and the minutes ledger disagree on purpose
// in some places. Every intended difference is one row of POLICY_DELTAS with
// the decision behind it; a difference no row explains is `disagree`, which
// the P1.11 replay harness fails on. Two differences are never "explained":
// a refusal on a cached / attached path that was not served free (a Q6
// break, C1) is `disagree`, and a ledger that could not get the video's
// length is its own class `metadata_unavailable` (W1) — counted and reported
// by the gate, because during a YouTube block it would refuse every submit.

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
  | 'hold_conflict'
  | 'submit_error'
  | 'shadow_error';

/**
 * Refusals that still let a video be served when serving it costs nothing —
 * already in the library, a completed row, or a live run to attach to (Q6).
 * Only a run needs minutes; a blocked account is refused outright. Read by
 * the enforcement path (serve-only createVideo) and the shadow alike.
 */
export const SERVES_WITHOUT_RUN: ReadonlySet<string> = new Set<ShadowReason>([
  'free_window_used',
  'needs_paid_minutes',
  'free_disabled',
  'too_long',
  'too_many_runs',
  'metadata_unavailable',
]);

export interface ShadowFacts {
  path: BillingShadowPath;
  oldDecision: BillingShadowDecision;
  newDecision: BillingShadowDecision & { reason?: string };
}

export type ShadowClassificationKind = 'agree' | 'policy_delta' | 'disagree' | 'metadata_unavailable';

export interface ShadowClassification {
  classification: ShadowClassificationKind;
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

/**
 * A cached or attached video costs nothing, so the ledger must serve it (Q6):
 * one it refused anyway is a defect, not policy. A blocked account is the one
 * deliberate exception — `caps.blocked` serves nothing (P3.1).
 */
function refusedFreeServe({ path, newDecision }: ShadowFacts): boolean {
  return (path === 'cached' || path === 'attached') && !newDecision.admitted && newDecision.reason !== 'blocked';
}

export function classifyShadow(facts: ShadowFacts): ShadowClassification {
  const { oldDecision, newDecision } = facts;
  if (oldDecision.admitted === newDecision.admitted && oldDecision.charged === newDecision.charged) {
    return { classification: 'agree' };
  }
  if (refusedFreeServe(facts)) return { classification: 'disagree' };
  if (newDecision.reason === 'metadata_unavailable') return { classification: 'metadata_unavailable' };
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

/** The ledger's refusal named from a thrown error; null when the error is not a decision. */
export function errorReason(err: unknown): ShadowReason | null {
  if (err instanceof MetadataUnavailableError) return 'metadata_unavailable';
  if (err instanceof HoldFinalizedError) return 'hold_finalized';
  if (err instanceof HoldConflictError) return 'hold_conflict';
  if (err instanceof AccountBlockedError) return 'blocked';
  if (err instanceof TooManyRunsError) return 'too_many_runs';
  if (err instanceof VideoTooLongForPlanError) return 'too_long';
  if (err instanceof InsufficientMinutesError) {
    if (err.nextFreeAt) return 'free_window_used';
    return err.needed > FREE_VIDEO.maxVideoMinutes ? 'needs_paid_minutes' : 'free_disabled';
  }
  return null;
}

/** True when the ledger's refusal `err` still lets a cached / attachable video be served (Q6). */
export function refusalServesWithoutRun(err: unknown): boolean {
  const reason = errorReason(err);
  return reason !== null && SERVES_WITHOUT_RUN.has(reason);
}
