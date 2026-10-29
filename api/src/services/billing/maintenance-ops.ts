import { ObjectId } from 'mongodb';
import { addMonthsUtc, monthsElapsed } from './minutes.js';
import type { AccountDraft } from './account-draft.js';
import type { RecurringGrantDocument } from '../../repositories/billing.repository.js';
import type { RunState } from '../../repositories/billing-maintenance.repository.js';

// Pure repair-job transitions on an AccountDraft (P1.6). Keys:
//   expire:lot:<lotId>                  one per lot — a lot expires once; minutes
//                                       released into it later expire at once
//                                       (release-expire), so it never refills
//   recurring:<grantId>:<runAt ISO>     one lot per grant per month

export const lotExpiryKey = (lotId: ObjectId): string => `expire:lot:${lotId.toHexString()}`;

/** Expire what is left of every open lot past its expiry. Returns the minutes expired. */
export function applyLotExpiry(draft: AccountDraft, alreadyExpired: ReadonlySet<string>): number {
  let expired = 0;
  for (const lot of draft.lots) {
    const due = lot.expiresAt !== null && lot.expiresAt.getTime() <= draft.now.getTime();
    const key = lotExpiryKey(lot._id);
    if (!due || lot.minutesRemaining <= 0 || alreadyExpired.has(key)) continue;
    const minutes = lot.minutesRemaining;
    draft.changeLot(lot, -minutes);
    draft.addEntry({
      type: 'expire', bucket: 'credit', minutes: -minutes, lotId: lot._id, idempotencyKey: key,
      source: { kind: 'system', ref: `lot-expiry:${lot._id.toHexString()}` },
    });
    expired += minutes;
  }
  return expired;
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
 * next tick has granted the new one, so there is no gap with no minutes at
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

/** Grant this month's lot of a recurring grant. Returns the new lot's id. */
export function applyRecurringRun(draft: AccountDraft, grant: RecurringGrantDocument, run: RecurringRun): ObjectId {
  const lotId = new ObjectId();
  const source = { kind: 'recurring' as const, ref: grant._id.toHexString() };
  draft.addLot({
    _id: lotId, accountId: draft.accountId, minutesGranted: grant.minutes, minutesRemaining: grant.minutes,
    source, expiresAt: run.expiresAt, createdAt: draft.now,
  });
  draft.addEntry({
    type: 'grant', bucket: 'credit', minutes: grant.minutes, lotId, idempotencyKey: run.idempotencyKey, source,
    actorId: grant.actorId, auditId: grant.auditId, reason: grant.reason,
  });
  return lotId;
}

export type StaleHoldAction = 'settle' | 'release' | 'keep';

/**
 * What to do with a hold older than the max age, given the run it paid for:
 * completed by this run → settle (the user got the video; the completion
 * relay was lost); failed / missing / unstamped (no row carries its run id) /
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
