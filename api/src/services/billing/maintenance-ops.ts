import { ObjectId } from 'mongodb';
import { SIGNUP_GRANT_TOKENS } from '@vie/types';
import { repayDebt } from './debt.js';
import { addMonthsUtc, monthsElapsed } from './tokens.js';
import type { AccountDraft } from './account-draft.js';
import type { RecurringGrantDocument } from '../../repositories/billing.repository.js';
import type { RunState } from '../../repositories/billing-maintenance.repository.js';

// Pure system transitions on an AccountDraft: the repair job's (P1.6) and the
// signup grant. Keys:
//   expire:lot:<lotId>                  one per lot — a lot expires once; tokens
//                                       released into it later expire at once
//                                       (release-expire), so it never refills
//   recurring:<grantId>:<runAt ISO>     one lot per grant per month
//   grant:signup:<userId>               the one-time signup grant (GDPR erasure
//                                       re-keys it with the tombstone id)

export const lotExpiryKey = (lotId: ObjectId): string => `expire:lot:${lotId.toHexString()}`;

export const signupGrantKey = (ownerId: ObjectId): string => `grant:signup:${ownerId.toHexString()}`;

/** Expire what is left of every open lot past its expiry. Returns the tokens expired. */
export function applyLotExpiry(draft: AccountDraft, alreadyExpired: ReadonlySet<string>): number {
  let expired = 0;
  for (const lot of draft.lots) {
    const due = lot.expiresAt !== null && lot.expiresAt.getTime() <= draft.now.getTime();
    const key = lotExpiryKey(lot._id);
    if (!due || lot.tokensRemaining <= 0 || alreadyExpired.has(key)) continue;
    const tokens = lot.tokensRemaining;
    draft.changeLot(lot, -tokens);
    draft.addEntry({
      type: 'expire', bucket: 'credit', tokens: -tokens, lotId: lot._id, idempotencyKey: key,
      source: { kind: 'system', ref: `lot-expiry:${lot._id.toHexString()}` },
    });
    expired += tokens;
  }
  return expired;
}

/**
 * The one-time signup grant: a never-expiring lot of SIGNUP_GRANT_TOKENS
 * (repaying debt first, which only a migrated account could have). The
 * caller checks `grant:signup:<userId>` is not written yet. Returns the lot id.
 */
export function applySignupGrant(draft: AccountDraft): ObjectId {
  const lotId = new ObjectId();
  const source = { kind: 'system' as const, ref: 'signup' };
  const key = signupGrantKey(draft.original.ownerId);
  draft.addLot({
    _id: lotId, accountId: draft.accountId, tokensGranted: SIGNUP_GRANT_TOKENS, tokensRemaining: SIGNUP_GRANT_TOKENS,
    source, expiresAt: null, createdAt: draft.now,
  });
  draft.addEntry({ type: 'grant', bucket: 'credit', tokens: SIGNUP_GRANT_TOKENS, lotId, idempotencyKey: key, source });
  draft.account.signupGrantAt = draft.now;
  draft.markChanged();
  repayDebt(draft, key, { source });
  return lotId;
}

export interface RecurringRun {
  /** The monthly slot this run grants for (anchored on the grant's creation, so it never drifts). */
  runAt: Date;
  /** When the next month's lot is due. */
  nextRunAt: Date;
  /** The lot expires at the next run plus `graceMs`: one month, no rollover beyond the grace. */
  expiresAt: Date;
  idempotencyKey: string;
}

/**
 * The slot due at `now`; idle months in between grant nothing (like a Pro
 * cycle). `graceMs` keeps the outgoing lot spendable until the repair job's
 * next tick has granted the new one, so there is no gap with no tokens at
 * the turn of the month (I3); the old lot is spent first (it expires first).
 */
export function recurringRunAt(grant: RecurringGrantDocument, now: Date, graceMs = 0): RecurringRun {
  const index = monthsElapsed(grant.createdAt, now);
  const runAt = addMonthsUtc(grant.createdAt, index);
  const nextRunAt = addMonthsUtc(grant.createdAt, index + 1);
  return {
    runAt,
    nextRunAt,
    expiresAt: new Date(nextRunAt.getTime() + graceMs),
    idempotencyKey: `recurring:${grant._id.toHexString()}:${runAt.toISOString()}`,
  };
}

/** Grant this month's lot of a recurring grant (repaying debt first). Returns the new lot's id. */
export function applyRecurringRun(draft: AccountDraft, grant: RecurringGrantDocument, run: RecurringRun): ObjectId {
  const lotId = new ObjectId();
  const source = { kind: 'recurring' as const, ref: grant._id.toHexString() };
  const context = { source, actorId: grant.actorId, auditId: grant.auditId, reason: grant.reason };
  draft.addLot({
    _id: lotId, accountId: draft.accountId, tokensGranted: grant.tokens, tokensRemaining: grant.tokens,
    source, expiresAt: run.expiresAt, createdAt: draft.now,
  });
  draft.addEntry({ type: 'grant', bucket: 'credit', tokens: grant.tokens, lotId, idempotencyKey: run.idempotencyKey, ...context });
  repayDebt(draft, run.idempotencyKey, context);
  return lotId;
}

export type StaleHoldAction = 'settle' | 'release' | 'keep';

/**
 * What to do with a hold older than the max age, given the run it paid for:
 * completed by this run → settle on the run's cost (the user got the video;
 * the run-cost callback was lost); failed / missing / unstamped (no row carries its run id) /
 * stalled (pending or processing with no write for the max age — B13's
 * queue-TTL stuck row) → release; still moving → keep. A row that says
 * `completed` but was last written before the hold was taken is an older
 * run's output — e.g. a PIPELINE_VERSION regen re-stamped it and its job was
 * lost — so it is released, never charged (W2).
 */
export function staleHoldAction(run: RunState | null, now: Date, maxAgeMs: number, holdCreatedAt: Date): StaleHoldAction {
  if (!run) return 'release';
  if (run.status === 'completed') return run.updatedAt.getTime() >= holdCreatedAt.getTime() ? 'settle' : 'release';
  if (run.status === 'failed') return 'release';
  return now.getTime() - run.updatedAt.getTime() >= maxAgeMs ? 'release' : 'keep';
}
