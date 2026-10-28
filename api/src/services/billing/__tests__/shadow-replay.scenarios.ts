import type { BillingShadowDecision, BillingShadowPath } from '@vie/types';

// P1.11 replay scenarios — pure data, so the P3 gate re-runs them unchanged.
// Each scenario drives the real submit / playlist / internal-status paths;
// `expect` lists the runs whose shadow row is judged. `oldDecision` is the
// old dollar gate's decision recorded as data: today the harness asserts it
// against the live gate, after P3.2 (old gate deleted) it is the only source.

export type AccountKind =
  | 'free'
  | 'credit' // free plan + a 300-minute admin lot
  | 'pro' // manual Pro, 600 minutes this cycle
  | 'over_limit' // free plan, already at the old $2/day cap
  | 'over_limit_credit' // at the old cap, with a 300-minute lot
  | 'near_limit_credit'; // $1.80 of the $2 cap spent, with a 300-minute lot

export type SubmitOutcome = 'created' | 'cached' | 'attached' | 'throws';

export type ReplayStep =
  /** `lengthUnknown`: the metadata pre-flight fails for this submit (a YouTube block). */
  | { kind: 'submit'; run: string; minutes: number; outcome: SubmitOutcome; bypassCache?: boolean; lengthUnknown?: boolean }
  | { kind: 'finish'; run: string; status: 'completed' | 'failed'; costUsd: number }
  | { kind: 'playlist'; items: Array<{ run: string; minutes: number; outcome: Exclude<SubmitOutcome, 'throws'> }> }
  /** Spend the free video in the ledger directly (the old gate refuses this account's submits). */
  | { kind: 'ledger_free_video' };

/** POLICY_DELTAS ids (shadow-policy.ts). */
export type PolicyId =
  | 'free-window'
  | 'paid-minutes'
  | 'free-disabled'
  | 'too-long'
  | 'blocked'
  | 'too-many-runs'
  | 'no-daily-cap'
  | 'failed-released';

export interface ReplayExpectation {
  run: string;
  path: BillingShadowPath;
  oldDecision: BillingShadowDecision;
  /** `metadata_unavailable` is its own class (W1), never a policy delta. */
  classification: 'agree' | 'policy_delta' | 'metadata_unavailable';
  policy?: PolicyId;
  /** How many runs of this video may hold minutes (default: not checked). */
  heldRuns?: number;
}

export interface ReplayScenario {
  name: string;
  account: AccountKind;
  steps: ReplayStep[];
  expect: ReplayExpectation[];
}

const RAN: BillingShadowDecision = { admitted: true, charged: true };
const SERVED: BillingShadowDecision = { admitted: true, charged: false };
const REFUSED: BillingShadowDecision = { admitted: false, charged: false };

/** Use the free video and finish the run, so a later submit has to pay. */
const FREE_VIDEO_USED: ReplayStep[] = [
  { kind: 'submit', run: 'first', minutes: 10, outcome: 'created' },
  { kind: 'finish', run: 'first', status: 'completed', costUsd: 0.1 },
];

export const REPLAY_SCENARIOS: readonly ReplayScenario[] = [
  {
    name: 'free: created, still running',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 10, outcome: 'created' }],
    expect: [{ run: 'a', path: 'created', oldDecision: RAN, classification: 'agree' }],
  },
  {
    name: 'free: created → completed',
    account: 'free',
    steps: [
      { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' },
      { kind: 'finish', run: 'a', status: 'completed', costUsd: 0.2 },
    ],
    expect: [{ run: 'a', path: 'completed', oldDecision: RAN, classification: 'agree' }],
  },
  {
    name: 'free: created → failed',
    account: 'free',
    steps: [
      { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' },
      { kind: 'finish', run: 'a', status: 'failed', costUsd: 0.05 },
    ],
    expect: [{ run: 'a', path: 'failed', oldDecision: RAN, classification: 'policy_delta', policy: 'failed-released' }],
  },
  {
    name: 'free: cached',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 10, outcome: 'cached' }],
    expect: [{ run: 'a', path: 'cached', oldDecision: SERVED, classification: 'agree' }],
  },
  {
    name: 'free: attached to another user\'s run',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 10, outcome: 'attached' }],
    expect: [{ run: 'a', path: 'attached', oldDecision: SERVED, classification: 'agree' }],
  },
  {
    name: 'free: createVideo throws',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 10, outcome: 'throws' }],
    expect: [{ run: 'a', path: 'failed', oldDecision: SERVED, classification: 'agree' }],
  },
  {
    name: 'free: a second free video inside 24 h',
    account: 'free',
    steps: [...FREE_VIDEO_USED, { kind: 'submit', run: 'b', minutes: 10, outcome: 'created' }],
    expect: [{ run: 'b', path: 'created', oldDecision: RAN, classification: 'policy_delta', policy: 'free-window' }],
  },
  {
    // C1 / Q6: the ledger cannot pay for a run, yet a cached video costs nothing.
    name: 'free: free video used, then an already-cached video',
    account: 'free',
    steps: [...FREE_VIDEO_USED, { kind: 'submit', run: 'b', minutes: 10, outcome: 'cached' }],
    expect: [{ run: 'b', path: 'cached', oldDecision: SERVED, classification: 'agree' }],
  },
  {
    name: 'free: free video used, then a video another user is running',
    account: 'free',
    steps: [...FREE_VIDEO_USED, { kind: 'submit', run: 'b', minutes: 10, outcome: 'attached' }],
    expect: [{ run: 'b', path: 'attached', oldDecision: SERVED, classification: 'agree' }],
  },
  {
    // W1: no length, no quote — never an explained difference.
    name: 'free: the pre-flight cannot get the video\'s length',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 10, outcome: 'created', lengthUnknown: true }],
    expect: [{ run: 'a', path: 'created', oldDecision: RAN, classification: 'metadata_unavailable' }],
  },
  {
    name: 'free: a 45-minute video with no paid minutes',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 45, outcome: 'created' }],
    expect: [{ run: 'a', path: 'created', oldDecision: RAN, classification: 'policy_delta', policy: 'paid-minutes' }],
  },
  {
    name: 'free: a video over 180 minutes',
    account: 'free',
    steps: [{ kind: 'submit', run: 'a', minutes: 200, outcome: 'created' }],
    expect: [{ run: 'a', path: 'created', oldDecision: RAN, classification: 'policy_delta', policy: 'too-long' }],
  },
  {
    name: 'credit: a 45-minute video → completed',
    account: 'credit',
    steps: [
      { kind: 'submit', run: 'a', minutes: 45, outcome: 'created' },
      { kind: 'finish', run: 'a', status: 'completed', costUsd: 0.4 },
    ],
    expect: [{ run: 'a', path: 'completed', oldDecision: RAN, classification: 'agree' }],
  },
  {
    name: 'credit: free video used, the next one paid from credits',
    account: 'credit',
    steps: [
      ...FREE_VIDEO_USED,
      { kind: 'submit', run: 'b', minutes: 20, outcome: 'created' },
      { kind: 'finish', run: 'b', status: 'completed', costUsd: 0.3 },
    ],
    expect: [{ run: 'b', path: 'completed', oldDecision: RAN, classification: 'agree' }],
  },
  {
    name: 'pro: free video first, then plan minutes',
    account: 'pro',
    steps: [
      ...FREE_VIDEO_USED,
      { kind: 'submit', run: 'b', minutes: 60, outcome: 'created' },
      { kind: 'finish', run: 'b', status: 'completed', costUsd: 0.6 },
    ],
    expect: [
      { run: 'first', path: 'completed', oldDecision: RAN, classification: 'agree' },
      { run: 'b', path: 'completed', oldDecision: RAN, classification: 'agree' },
    ],
  },
  {
    name: 'pro: cached',
    account: 'pro',
    steps: [{ kind: 'submit', run: 'a', minutes: 20, outcome: 'cached' }],
    expect: [{ run: 'a', path: 'cached', oldDecision: SERVED, classification: 'agree' }],
  },
  {
    name: 'over the old $ cap: the free video is still open',
    account: 'over_limit',
    steps: [{ kind: 'submit', run: 'a', minutes: 10, outcome: 'created' }],
    expect: [{ run: 'a', path: 'refused', oldDecision: REFUSED, classification: 'policy_delta', policy: 'no-daily-cap' }],
  },
  {
    name: 'over the old $ cap: credits pay',
    account: 'over_limit_credit',
    steps: [{ kind: 'submit', run: 'a', minutes: 45, outcome: 'created' }],
    expect: [{ run: 'a', path: 'refused', oldDecision: REFUSED, classification: 'policy_delta', policy: 'no-daily-cap' }],
  },
  {
    name: 'over the old $ cap, free video used, no credits: both refuse',
    account: 'over_limit',
    steps: [{ kind: 'ledger_free_video' }, { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' }],
    expect: [{ run: 'a', path: 'refused', oldDecision: REFUSED, classification: 'agree' }],
  },
  {
    // Holds are keyed on a server id per submission (P3.1), so a resubmit can
    // no longer reuse a run: an HTTP retry of the same submit meets the
    // idempotency gate (duplicate) and must not hold a second time.
    name: 'retry: the same submit sent twice (an HTTP retry) keeps one hold',
    account: 'free',
    steps: [
      { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' },
      { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' },
    ],
    expect: [{ run: 'a', path: 'created', oldDecision: RAN, classification: 'agree', heldRuns: 1 }],
  },
  {
    name: 'retry: translation sends "completed" a second time',
    account: 'free',
    steps: [
      { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' },
      { kind: 'finish', run: 'a', status: 'completed', costUsd: 0.2 },
      { kind: 'finish', run: 'a', status: 'completed', costUsd: 0.03 },
    ],
    expect: [{ run: 'a', path: 'retry', oldDecision: RAN, classification: 'agree' }],
  },
  {
    name: 'retry: "failed" arrives after "completed"',
    account: 'free',
    steps: [
      { kind: 'submit', run: 'a', minutes: 10, outcome: 'created' },
      { kind: 'finish', run: 'a', status: 'completed', costUsd: 0.2 },
      { kind: 'finish', run: 'a', status: 'failed', costUsd: 0 },
    ],
    expect: [{ run: 'a', path: 'retry', oldDecision: RAN, classification: 'agree' }],
  },
  {
    name: 'playlist on a credit account: cached, completed and a 45-minute item',
    account: 'credit',
    steps: [
      {
        kind: 'playlist',
        items: [
          { run: 'p1', minutes: 5, outcome: 'cached' },
          { run: 'p2', minutes: 10, outcome: 'created' },
          { run: 'p3', minutes: 45, outcome: 'created' },
        ],
      },
      { kind: 'finish', run: 'p2', status: 'completed', costUsd: 0.2 },
    ],
    expect: [
      { run: 'p1', path: 'cached', oldDecision: SERVED, classification: 'agree' },
      { run: 'p2', path: 'completed', oldDecision: RAN, classification: 'agree' },
      { run: 'p3', path: 'created', oldDecision: RAN, classification: 'agree' },
    ],
  },
  {
    name: 'playlist that runs into the old $ cap half-way',
    account: 'near_limit_credit',
    steps: [
      {
        kind: 'playlist',
        items: [
          { run: 'q1', minutes: 10, outcome: 'created' },
          { run: 'q2', minutes: 10, outcome: 'created' },
          { run: 'q3', minutes: 10, outcome: 'created' },
          { run: 'q4', minutes: 10, outcome: 'created' },
        ],
      },
    ],
    expect: [
      { run: 'q1', path: 'created', oldDecision: RAN, classification: 'agree' },
      { run: 'q2', path: 'created', oldDecision: RAN, classification: 'agree' },
      { run: 'q3', path: 'refused', oldDecision: REFUSED, classification: 'policy_delta', policy: 'no-daily-cap' },
      { run: 'q4', path: 'refused', oldDecision: REFUSED, classification: 'policy_delta', policy: 'no-daily-cap' },
    ],
  },
];
