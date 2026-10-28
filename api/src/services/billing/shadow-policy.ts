import type { BillingShadowDecision, BillingShadowPath } from '@vie/types';
import type { RefusalReason } from './billing-errors.js';

// Contract 4: the old dollar gate and the minutes ledger disagree on purpose
// in some places. Every intended difference is one row of POLICY_DELTAS with
// the decision behind it; a difference no row explains is `disagree`, which
// the P1.11 replay harness fails on. Since P3.2 the old gate is gone: the
// harness compares the ledger against recorded old decisions (its scenarios
// and the dev `billingShadow` rows, kept readable). Two differences are never
// "explained": a refusal on a cached / attached path that was not served free
// (a Q6 break, C1) is `disagree`, and a ledger that could not get the video's
// length is its own class `metadata_unavailable` (W1) — counted and reported
// by the gate, because during a YouTube block it would refuse every submit.

/**
 * Why the ledger refused (or could not decide) — `newDecision.reason`: the
 * submit paths' vocabulary (billing-errors.ts `refusalReason`), plus two that
 * occur only in recorded shadow rows.
 */
export type ShadowReason = RefusalReason | 'submit_error' | 'shadow_error';

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
