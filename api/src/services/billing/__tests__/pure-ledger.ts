import { ObjectId } from 'mongodb';
import { AccountDraft } from '../account-draft.js';
import { applyGrantLot } from '../admin-ops.js';
import { allocate, balanceOf } from '../allocation.js';
import { repayDebt } from '../debt.js';
import { applyHold, applyRelease, applySettleActual, findHold, spendableState } from '../hold-ops.js';
import { applySignupGrant } from '../maintenance-ops.js';
import { rollContext, rollPlanCycle } from '../plan-cycle.js';
import { checkProjection } from '../projection.js';
import { estimateTokens, tokensForUsd, videoMinutes } from '../tokens.js';
import { adminAudit, HOUR_MS, makeAccountDoc } from './billing-fixtures.js';
import type { AllocationResult } from '../allocation.js';
import type { ReleaseReason, SettleResult } from '../hold-ops.js';
import type { ProjectionCheck } from '../projection.js';
import type {
  BillingAccountDocument,
  BillingHoldDocument,
  CreditLotDocument,
  LedgerEntryDocument,
} from '../../../repositories/billing.repository.js';

/** Where every pure scenario starts: the signup grant is written at T0. */
export const T0 = new Date('2026-11-01T10:00:00Z');

export function hoursAfterT0(hours: number): Date {
  return new Date(T0.getTime() + hours * HOUR_MS);
}

/**
 * One account held in memory. Each `step` builds an AccountDraft at `now`,
 * rolls the plan cycle (a new cycle repays debt first, as BillingUnits does),
 * runs the pure transition and commits the draft: the account, every lot
 * and the entries. No Mongo, so property tests can run hundreds of cases.
 */
export class PureLedger {
  account: BillingAccountDocument;
  lots: CreditLotDocument[] = [];
  readonly entries: LedgerEntryDocument[] = [];

  constructor(account: BillingAccountDocument = makeAccountDoc()) {
    this.account = account;
  }

  step<T>(now: Date, op: (draft: AccountDraft) => T): T {
    const draft = new AccountDraft(this.account, this.lots, now);
    const grantKey = rollPlanCycle(draft);
    if (grantKey) repayDebt(draft, grantKey, rollContext(draft));
    const value = op(draft);
    this.account = { ...draft.account, version: this.account.version + 1, updatedAt: now };
    this.lots = [...draft.lots];
    this.entries.push(...draft.entries.map((entry) => ({ _id: new ObjectId(), ...entry })));
    return value;
  }

  check(): ProjectionCheck {
    return checkProjection(this.account, this.lots, this.entries);
  }

  /** plan remaining + spendable credits − debt, as of `now` (no roll). */
  balanceAt(now: Date): number {
    return balanceOf(spendableState(new AccountDraft(this.account, this.lots, now)));
  }

  /** Plan remaining + spendable credits, debt left out. */
  spendableAt(now: Date): number {
    return this.balanceAt(now) + this.account.buckets.debt.tokens;
  }

  /** [type, bucket, tokens, key, mirrorsHold] of the entries whose source.ref is `ref`, in write order. */
  rows(ref: string): Array<[string, string, number | undefined, string, boolean]> {
    return this.entries
      .filter((entry) => entry.source.ref === ref)
      .map((entry) => [entry.type, entry.bucket, entry.tokens, entry.idempotencyKey, entry.mirrorsHold === true]);
  }

  lot(lotId: ObjectId | string): CreditLotDocument | undefined {
    const hex = typeof lotId === 'string' ? lotId : lotId.toHexString();
    return this.lots.find((lot) => lot._id.toHexString() === hex);
  }
}

/** A fresh account with its 1,000-token signup lot (written at T0). */
export function signedUpLedger(): PureLedger {
  const ledger = new PureLedger();
  ledger.step(T0, (draft) => applySignupGrant(draft));
  return ledger;
}

/** An admin credit lot; returns its id hex. */
export function grantTokens(ledger: PureLedger, now: Date, tokens: number, expiresAt: Date | null = null): string {
  return ledger.step(now, (draft) => applyGrantLot(draft, { tokens, expiresAt }, adminAudit())).toHexString();
}

/** Decide a video of `durationSec` on the account as it is at `now`, without holding. */
export function decide(draft: AccountDraft, durationSec: number): AllocationResult {
  return allocate({
    ...spendableState(draft),
    tokens: estimateTokens(durationSec),
    videoMinutes: videoMinutes(durationSec),
    caps: draft.account.caps,
    openHolds: draft.account.holds.length,
  });
}

/** Hold the estimate of a video for `runId` when the gate admits it; null when it refuses. */
export function holdVideo(ledger: PureLedger, now: Date, runId: string, durationSec: number): BillingHoldDocument | null {
  return ledger.step(now, (draft) => {
    const decision = decide(draft, durationSec);
    if (!decision.ok) return null;
    const request = { requestId: runId, userId: draft.original.ownerId, youtubeId: 'dQw4w9WgXcQ', durationSec, tokens: estimateTokens(durationSec) };
    return applyHold(draft, request, decision.split);
  });
}

function requireHold(draft: AccountDraft, runId: string): BillingHoldDocument {
  const hold = findHold(draft, runId);
  if (!hold) throw new Error(`no open hold for ${runId}`);
  return hold;
}

export function settleRun(ledger: PureLedger, now: Date, runId: string, costUsd: number): SettleResult {
  return ledger.step(now, (draft) => applySettleActual(draft, requireHold(draft, runId), tokensForUsd(costUsd), costUsd));
}

export function releaseRun(ledger: PureLedger, now: Date, runId: string, reason: ReleaseReason = 'failed'): number {
  return ledger.step(now, (draft) => applyRelease(draft, requireHold(draft, runId), reason));
}
