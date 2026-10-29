import { ObjectId } from 'mongodb';
import { FREE_WINDOW_MS, splitProportionally } from './minutes.js';
import { isCurrentCycle } from './plan-cycle.js';
import type { BillingBucket, BillingSource, BucketSplit } from '@vie/types';
import type { AccountDraft, EntryFields } from './account-draft.js';
import type { BillingHoldDocument, CreditLotDocument } from '../../repositories/billing.repository.js';

// Pure hold → debit | release transitions on an AccountDraft. Ledger keys:
//   hold:<requestId>:free|plan|lot:<lotId>
//   debit:<requestId>:…     (same suffixes; settle is idempotent on them)
//   release:<requestId>:…   release-expire:<requestId>:…  (minutes returned
//   to a plan cycle or lot that has since ended expire again at once)

/** Why a hold is given back. `cached`/`attached` = served without a run (Q6). */
export type ReleaseReason = 'cached' | 'attached' | 'failed' | 'submit_error' | 'stale' | 'admin';

export interface HoldRequest {
  requestId: string;
  userId: ObjectId;
  youtubeId: string | null;
  durationSec: number;
  minutes: number;
}

interface HoldSlice {
  bucket: BillingBucket;
  minutes: number;
  lotId?: ObjectId;
  keySuffix: string;
}

function holdSlices(hold: BillingHoldDocument): HoldSlice[] {
  const slices: HoldSlice[] = [];
  if (hold.split.free > 0) slices.push({ bucket: 'free', minutes: hold.split.free, keySuffix: 'free' });
  if (hold.split.plan > 0) slices.push({ bucket: 'plan', minutes: hold.split.plan, keySuffix: 'plan' });
  for (const lot of hold.split.lots) {
    slices.push({ bucket: 'credit', minutes: lot.minutes, lotId: lot.lotId, keySuffix: `lot:${lot.lotId.toHexString()}` });
  }
  return slices;
}

function runFields(hold: BillingHoldDocument): Pick<EntryFields, 'source' | 'userId' | 'youtubeId' | 'durationSec'> {
  const source: BillingSource = { kind: 'run', ref: hold.requestId };
  const youtubeId = hold.youtubeId ?? undefined;
  return { source, userId: hold.userId, youtubeId, durationSec: hold.durationSec };
}

function costShares(slices: HoldSlice[], costUsd: number | undefined): Array<number | undefined> {
  if (costUsd === undefined) return slices.map(() => undefined);
  return splitProportionally(costUsd, slices.map((slice) => slice.minutes));
}

export function findHold(draft: AccountDraft, requestId: string): BillingHoldDocument | undefined {
  return draft.account.holds.find((hold) => hold.requestId === requestId);
}

function removeHold(draft: AccountDraft, requestId: string): void {
  draft.account.holds = draft.account.holds.filter((hold) => hold.requestId !== requestId);
  draft.markChanged();
}

/** Take `split` out of the account's buckets and record the hold. */
export function applyHold(draft: AccountDraft, request: HoldRequest, split: BucketSplit): BillingHoldDocument {
  const { account, now } = draft;
  const hold: BillingHoldDocument = {
    ...request,
    split: { ...split, lots: split.lots.map((slice) => ({ lotId: new ObjectId(slice.lotId), minutes: slice.minutes })) },
    freeVideoAt: split.free > 0 ? now : null,
    planCycleStart: split.plan > 0 ? account.buckets.plan.cycleStart : null,
    createdAt: now,
  };
  if (split.free > 0) {
    const windowStart = now.getTime() - FREE_WINDOW_MS;
    const inWindow = account.buckets.free.recentFreeVideoAt.filter((at) => at.getTime() > windowStart);
    account.buckets.free.recentFreeVideoAt = [...inWindow, now];
  }
  account.buckets.plan.minutesUsed += split.plan;
  const fields = runFields(hold);
  for (const slice of holdSlices(hold)) {
    if (slice.lotId) draft.changeLot(requireLot(draft, slice.lotId), -slice.minutes);
    const key = `hold:${request.requestId}:${slice.keySuffix}`;
    draft.addEntry({ ...fields, type: 'hold', bucket: slice.bucket, minutes: -slice.minutes, lotId: slice.lotId, idempotencyKey: key });
  }
  account.holds.push(hold);
  draft.markChanged();
  return hold;
}

/** Post the hold as debits (same minutes — no true-up) with the run's $ for margin. */
export function applySettle(draft: AccountDraft, hold: BillingHoldDocument, costUsd?: number): void {
  const slices = holdSlices(hold);
  const shares = costShares(slices, costUsd);
  const fields = runFields(hold);
  slices.forEach((slice, i) => {
    const key = `debit:${hold.requestId}:${slice.keySuffix}`;
    draft.addEntry({
      ...fields, type: 'debit', bucket: slice.bucket, minutes: -slice.minutes, lotId: slice.lotId,
      idempotencyKey: key, costUsd: shares[i],
    });
  });
  removeHold(draft, hold.requestId);
}

/** Give every slice back to exactly where it came from. */
export function applyRelease(
  draft: AccountDraft,
  hold: BillingHoldDocument,
  reason: ReleaseReason,
  costUsd?: number,
): void {
  const slices = holdSlices(hold);
  const shares = costShares(slices, costUsd);
  const fields = { ...runFields(hold), reason };
  slices.forEach((slice, i) => {
    const entry = { ...fields, bucket: slice.bucket, lotId: slice.lotId, costUsd: shares[i] };
    draft.addEntry({ ...entry, type: 'release', minutes: slice.minutes, idempotencyKey: `release:${hold.requestId}:${slice.keySuffix}` });
    if (!returnSlice(draft, hold, slice)) {
      const key = `release-expire:${hold.requestId}:${slice.keySuffix}`;
      draft.addEntry({ ...entry, type: 'expire', minutes: -slice.minutes, costUsd: undefined, idempotencyKey: key });
    }
  });
  removeHold(draft, hold.requestId);
}

/** Put one slice back; false when its plan cycle or lot has ended (the minutes expire instead). */
function returnSlice(draft: AccountDraft, hold: BillingHoldDocument, slice: HoldSlice): boolean {
  const { account, now } = draft;
  if (slice.bucket === 'free') {
    // Remove this hold's timestamp once (another free hold may share the instant).
    const times = account.buckets.free.recentFreeVideoAt;
    const index = times.findIndex((at) => at.getTime() === hold.freeVideoAt?.getTime());
    if (index >= 0) account.buckets.free.recentFreeVideoAt = [...times.slice(0, index), ...times.slice(index + 1)];
    return true;
  }
  if (slice.bucket === 'plan') {
    if (!isCurrentCycle(account, hold.planCycleStart)) return false;
    account.buckets.plan.minutesUsed -= slice.minutes;
    return true;
  }
  const lot = requireLot(draft, slice.lotId);
  if (lot.expiresAt !== null && lot.expiresAt.getTime() <= now.getTime()) return false;
  draft.changeLot(lot, slice.minutes);
  return true;
}

function requireLot(draft: AccountDraft, lotId: ObjectId | undefined): CreditLotDocument {
  const lot = lotId ? draft.findLot(lotId) : undefined;
  if (!lot) throw new Error(`credit lot ${lotId?.toHexString() ?? '?'} is not loaded in this billing unit`);
  return lot;
}
