import { ObjectId } from 'mongodb';
import { chargeSplit } from './allocation.js';
import { repayDebt } from './debt.js';
import { isCurrentCycle } from './plan-cycle.js';
import { spendableState } from './spendable.js';
import { splitProportionally } from './tokens.js';
import type { BillingBucket, BillingSource, BucketSplit, ExecutionKind } from '@vie/types';
import type { AccountDraft, EntryFields } from './account-draft.js';
import type { ChargeSplit } from './allocation.js';
import type { BillingHoldDocument, CreditLotDocument } from '../../repositories/billing.repository.js';

// Pure hold → settle | release transitions and direct charges on an
// AccountDraft. Ledger keys:
//   hold:<runId>:plan|lot:<lotId>               the estimate, taken at submit
//   debit:<runId>:plan|lot:<lotId>              the run's real cost out of its hold
//                                               (`mirrorsHold`: balance-neutral)
//   debit:<runId>:extra:plan|extra:lot:<id>|extra:debt
//                                               what the run cost beyond its hold
//   release:<runId>:…  release-expire:<runId>:…  the unused part going back (tokens
//                                               returned to a plan cycle or lot that
//                                               has since ended expire again at once)
//   debit:exec:<executionId>:plan|lot:<id>|debt a chat turn, library chat or action

/** Why a hold is given back in full. `cached`/`attached` = served without a run (Q6). */
export type ReleaseReason = 'cached' | 'attached' | 'failed' | 'submit_error' | 'stale' | 'admin';

export interface HoldRequest {
  requestId: string;
  userId: ObjectId;
  youtubeId: string | null;
  durationSec: number;
  /** The estimate. */
  tokens: number;
}

/** What a settle did with a hold: charged its real cost, gave the rest back, or ran into debt. */
export interface SettleResult {
  /** Tokens charged in all (the run's real cost). */
  charged: number;
  /** Unused estimate given back. */
  released: number;
  /** Tokens the run cost beyond everything the account had. */
  debt: number;
}

export interface ExecutionCharge {
  executionId: string;
  kind: ExecutionKind;
  /** The request id the assistant's llm_usage rows carry (`source.ref`). */
  requestId: string;
  userId: ObjectId;
  tokens: number;
  costUsd: number;
  /** Why the debit was written outside the usage callback (`backfill`: the repair job). */
  reason?: string;
}

interface HoldSlice {
  bucket: BillingBucket;
  tokens: number;
  lotId?: ObjectId;
  keySuffix: string;
}

type RunFields = Pick<EntryFields, 'source' | 'userId' | 'youtubeId' | 'durationSec' | 'executionKind'>;

function holdSlices(hold: BillingHoldDocument): HoldSlice[] {
  const slices: HoldSlice[] = [];
  if (hold.split.plan > 0) slices.push({ bucket: 'plan', tokens: hold.split.plan, keySuffix: 'plan' });
  for (const lot of hold.split.lots) {
    if (lot.tokens <= 0) continue;
    slices.push({ bucket: 'credit', tokens: lot.tokens, lotId: lot.lotId, keySuffix: `lot:${lot.lotId.toHexString()}` });
  }
  return slices;
}

function runFields(hold: BillingHoldDocument): RunFields {
  const source: BillingSource = { kind: 'run', ref: hold.requestId };
  const youtubeId = hold.youtubeId ?? undefined;
  return { source, userId: hold.userId, youtubeId, durationSec: hold.durationSec, executionKind: 'video' };
}

function costShares(weights: number[], costUsd: number | undefined): Array<number | undefined> {
  if (costUsd === undefined) return weights.map(() => undefined);
  return splitProportionally(costUsd, weights);
}

export { spendableState } from './spendable.js';

export function findHold(draft: AccountDraft, requestId: string): BillingHoldDocument | undefined {
  return draft.account.holds.find((hold) => hold.requestId === requestId);
}

function removeHold(draft: AccountDraft, requestId: string): void {
  draft.account.holds = draft.account.holds.filter((hold) => hold.requestId !== requestId);
  draft.markChanged();
}

/** Take `split` (the estimate) out of the account's buckets and record the hold. */
export function applyHold(draft: AccountDraft, request: HoldRequest, split: BucketSplit): BillingHoldDocument {
  const { account, now } = draft;
  const hold: BillingHoldDocument = {
    ...request,
    split: { ...split, lots: split.lots.map((slice) => ({ lotId: new ObjectId(slice.lotId), tokens: slice.tokens })) },
    planCycleStart: split.plan > 0 ? account.buckets.plan.cycleStart : null,
    createdAt: now,
  };
  account.buckets.plan.tokensUsed += split.plan;
  const fields = runFields(hold);
  for (const slice of holdSlices(hold)) {
    if (slice.lotId) draft.changeLot(requireLot(draft, slice.lotId), -slice.tokens);
    const key = `hold:${request.requestId}:${slice.keySuffix}`;
    draft.addEntry({ ...fields, type: 'hold', bucket: slice.bucket, tokens: -slice.tokens, lotId: slice.lotId, idempotencyKey: key });
  }
  account.holds.push(hold);
  draft.markChanged();
  return hold;
}

/**
 * Take a charge split out of the buckets as debits keyed `<prefix>:plan`,
 * `<prefix>:lot:<id>`, `<prefix>:debt`, with `costUsd` shared by tokens.
 */
function applyChargeSplit(
  draft: AccountDraft,
  split: ChargeSplit,
  prefix: string,
  fields: Omit<EntryFields, 'type' | 'bucket' | 'tokens' | 'idempotencyKey'>,
  costUsd: number | undefined,
): void {
  const parts: HoldSlice[] = [];
  if (split.plan > 0) parts.push({ bucket: 'plan', tokens: split.plan, keySuffix: 'plan' });
  for (const slice of split.lots) {
    if (slice.tokens > 0) parts.push({ bucket: 'credit', tokens: slice.tokens, lotId: new ObjectId(slice.lotId), keySuffix: `lot:${slice.lotId}` });
  }
  if (split.debt > 0) parts.push({ bucket: 'debt', tokens: split.debt, keySuffix: 'debt' });
  const shares = costShares(parts.map((part) => part.tokens), costUsd);
  parts.forEach((part, i) => {
    if (part.bucket === 'plan') draft.account.buckets.plan.tokensUsed += part.tokens;
    if (part.lotId) draft.changeLot(requireLot(draft, part.lotId), -part.tokens);
    if (part.bucket === 'debt') draft.changeDebt(part.tokens);
    draft.addEntry({
      ...fields, type: 'debit', bucket: part.bucket, tokens: -part.tokens, lotId: part.lotId,
      idempotencyKey: `${prefix}:${part.keySuffix}`, costUsd: shares[i],
    });
  });
  draft.markChanged();
}

/**
 * Settle a run on its real cost (`tokens`, from `costUsd`). Within the hold:
 * debit along the hold's own slices (plan, then its lots — a lot that expired
 * during the run still pays), give the unused rest back. Beyond it: debit the
 * excess from the remaining plan, then lots FIFO, then debt (overdraft — the
 * run already happened).
 */
export function applySettleActual(
  draft: AccountDraft,
  hold: BillingHoldDocument,
  tokens: number,
  costUsd: number | undefined,
): SettleResult {
  const fields = runFields(hold);
  const debited: HoldSlice[] = [];
  const unused: HoldSlice[] = [];
  let left = tokens;
  for (const slice of holdSlices(hold)) {
    const take = Math.min(left, slice.tokens);
    if (take > 0) debited.push({ ...slice, tokens: take });
    if (slice.tokens > take) unused.push({ ...slice, tokens: slice.tokens - take });
    left -= take;
  }
  removeHold(draft, hold.requestId);
  const extra = left > 0 ? chargeSplit(left, spendableState(draft)) : null;
  const extraTokens = extra ? left : 0;

  // The overrun's share only when there is one, so the rounding remainder lands on a written entry.
  const weights = [...debited.map((slice) => slice.tokens), ...(extra ? [extraTokens] : [])];
  const shares = costShares(weights, costUsd);
  debited.forEach((slice, i) => {
    draft.addEntry({
      ...fields, type: 'debit', bucket: slice.bucket, tokens: -slice.tokens, lotId: slice.lotId, mirrorsHold: true,
      idempotencyKey: `debit:${hold.requestId}:${slice.keySuffix}`, costUsd: shares[i],
    });
  });
  if (extra) applyChargeSplit(draft, extra, `debit:${hold.requestId}:extra`, fields, shares[shares.length - 1]);

  const released = giveBack(draft, hold, unused, {});
  return { charged: tokens, released, debt: extra?.debt ?? 0 };
}

/** Give every slice of the hold back to exactly where it came from. Returns the tokens released. */
export function applyRelease(
  draft: AccountDraft,
  hold: BillingHoldDocument,
  reason: ReleaseReason,
  costUsd?: number,
): number {
  removeHold(draft, hold.requestId);
  return giveBack(draft, hold, holdSlices(hold), { reason, costUsd });
}

/**
 * Release `slices` of a (removed) hold: each goes back to its plan cycle or
 * lot, or expires at once when that has ended; what lands repays debt first.
 */
function giveBack(
  draft: AccountDraft,
  hold: BillingHoldDocument,
  slices: HoldSlice[],
  options: { reason?: ReleaseReason; costUsd?: number },
): number {
  if (slices.length === 0) return 0;
  const { reason, costUsd } = options;
  const shares = costShares(slices.map((slice) => slice.tokens), costUsd);
  const fields = { ...runFields(hold), ...(reason && { reason }) };
  let returned = 0;
  slices.forEach((slice, i) => {
    const entry = { ...fields, bucket: slice.bucket, lotId: slice.lotId, costUsd: shares[i] };
    draft.addEntry({ ...entry, type: 'release', tokens: slice.tokens, idempotencyKey: `release:${hold.requestId}:${slice.keySuffix}` });
    if (returnSlice(draft, hold, slice)) {
      returned += slice.tokens;
      return;
    }
    const key = `release-expire:${hold.requestId}:${slice.keySuffix}`;
    draft.addEntry({ ...entry, type: 'expire', tokens: -slice.tokens, costUsd: undefined, idempotencyKey: key });
  });
  if (returned > 0) repayDebt(draft, `release:${hold.requestId}`, { source: fields.source, userId: fields.userId });
  return slices.reduce((sum, slice) => sum + slice.tokens, 0);
}

/** Put one slice back; false when its plan cycle or lot has ended (the tokens expire instead). */
function returnSlice(draft: AccountDraft, hold: BillingHoldDocument, slice: HoldSlice): boolean {
  const { account, now } = draft;
  if (slice.bucket === 'plan') {
    if (!isCurrentCycle(account, hold.planCycleStart)) return false;
    account.buckets.plan.tokensUsed -= slice.tokens;
    return true;
  }
  const lot = requireLot(draft, slice.lotId);
  if (lot.expiresAt !== null && lot.expiresAt.getTime() <= now.getTime()) return false;
  draft.changeLot(lot, slice.tokens);
  return true;
}

/** Charge a chat turn / library chat / action its real cost: plan, then lots FIFO, then debt. */
export function applyExecutionCharge(draft: AccountDraft, charge: ExecutionCharge): ChargeSplit {
  const { executionId, kind, requestId, userId, tokens, costUsd, reason } = charge;
  const split = chargeSplit(tokens, spendableState(draft));
  const fields = { source: { kind: 'run' as const, ref: requestId }, userId, executionKind: kind, executionId, ...(reason && { reason }) };
  applyChargeSplit(draft, split, `debit:exec:${executionId}`, fields, costUsd);
  return split;
}

function requireLot(draft: AccountDraft, lotId: ObjectId | undefined): CreditLotDocument {
  const lot = lotId ? draft.findLot(lotId) : undefined;
  if (!lot) throw new Error(`credit lot ${lotId?.toHexString() ?? '?'} is not loaded in this billing unit`);
  return lot;
}
