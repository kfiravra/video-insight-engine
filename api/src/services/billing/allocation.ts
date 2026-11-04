import { MAX_VIDEO_MINUTES } from '@vie/types';
import type { AccountCaps, BucketSplit, LotSlice } from '@vie/types';

// Pure spend-order rules: an estimate is held from the Pro cycle first, then
// credit lots FIFO by expiry. No I/O, no clock — `now` is an input — so the
// property tests can drive it directly.

export interface AllocationLot {
  lotId: string;
  tokensRemaining: number;
  expiresAt: Date | null;
  createdAt: Date;
}

/** What the account can spend: Pro cycle tokens left, its lots, what it owes. */
export interface SpendableState {
  now: Date;
  /** Plan tokens left in the current cycle (0 without an active cycle). */
  planAvailable: number;
  lots: AllocationLot[];
  /** Tokens owed (≥ 0). */
  debt: number;
}

export interface AllocationInput extends SpendableState {
  /** The estimate to hold. */
  tokens: number;
  /** The video's length in whole minutes. */
  videoMinutes: number;
  caps: AccountCaps;
  openHolds: number;
}

export type AllocationRejection =
  | { ok: false; reason: 'blocked' }
  | { ok: false; reason: 'too_many_runs'; maxConcurrent: number }
  | { ok: false; reason: 'too_long'; maxMinutes: number }
  | { ok: false; reason: 'insufficient'; needed: number; available: number };

export interface AllocationSuccess {
  ok: true;
  split: BucketSplit;
}

export type AllocationResult = AllocationSuccess | AllocationRejection;

/** Where a direct charge (a run's cost beyond its hold, a chat turn) came from: plan, lots, then debt. */
export interface ChargeSplit extends BucketSplit {
  debt: number;
}

/** Caps only ever tighten: MAX_VIDEO_MINUTES for everyone, an admin can lower it. */
export function maxVideoMinutes(caps: AccountCaps): number {
  return Math.min(MAX_VIDEO_MINUTES, caps.maxVideoMinutes ?? Number.POSITIVE_INFINITY);
}

/** A lot can pay only while it has tokens and has not expired (expiry is exclusive). */
export function isLotSpendable(lot: AllocationLot, now: Date): boolean {
  return lot.tokensRemaining > 0 && (lot.expiresAt === null || lot.expiresAt.getTime() > now.getTime());
}

/** FIFO spend order: soonest expiry first, never-expiring last, then oldest, then id. */
export function compareLotsFifo(a: AllocationLot, b: AllocationLot): number {
  const aExp = a.expiresAt?.getTime() ?? Number.POSITIVE_INFINITY;
  const bExp = b.expiresAt?.getTime() ?? Number.POSITIVE_INFINITY;
  if (aExp !== bExp) return aExp < bExp ? -1 : 1;
  if (a.createdAt.getTime() !== b.createdAt.getTime()) return a.createdAt.getTime() - b.createdAt.getTime();
  return a.lotId < b.lotId ? -1 : a.lotId > b.lotId ? 1 : 0;
}

export function spendableLots(lots: AllocationLot[], now: Date): AllocationLot[] {
  return lots.filter((lot) => isLotSpendable(lot, now)).sort(compareLotsFifo);
}

export function spendableCredit(lots: AllocationLot[], now: Date): number {
  return spendableLots(lots, now).reduce((sum, lot) => sum + lot.tokensRemaining, 0);
}

/** plan remaining + spendable credits − debt; negative while in debt. */
export function balanceOf(state: SpendableState): number {
  return Math.max(state.planAvailable, 0) + spendableCredit(state.lots, state.now) - state.debt;
}

/** Take `tokens` from `lots` in the order given (callers pass spendable lots, FIFO). */
export function takeFromLots(tokens: number, lots: AllocationLot[]): LotSlice[] {
  const slices: LotSlice[] = [];
  let left = tokens;
  for (const lot of lots) {
    if (left === 0) break;
    const take = Math.min(left, lot.tokensRemaining);
    slices.push({ lotId: lot.lotId, tokens: take });
    left -= take;
  }
  return slices;
}

/**
 * Take `tokens` from the plan, then spendable lots FIFO; whatever they cannot
 * cover becomes debt. Never refuses: a run that cost more than its hold, and
 * a chat turn, are charged after the fact (overdraft).
 */
export function chargeSplit(tokens: number, state: SpendableState): ChargeSplit {
  const plan = Math.min(tokens, Math.max(state.planAvailable, 0));
  const lots = takeFromLots(tokens - plan, spendableLots(state.lots, state.now));
  const credit = lots.reduce((sum, slice) => sum + slice.tokens, 0);
  return { plan, credit, lots, debt: tokens - plan - credit };
}

function gateRejection(input: AllocationInput): AllocationRejection | null {
  const { caps, openHolds } = input;
  if (caps.blocked) return { ok: false, reason: 'blocked' };
  if (caps.maxConcurrent != null && openHolds >= caps.maxConcurrent) {
    return { ok: false, reason: 'too_many_runs', maxConcurrent: caps.maxConcurrent };
  }
  return null;
}

/**
 * Decide whether a video may run and where its estimate is held from:
 * blocked → too many runs → too long (MAX_VIDEO_MINUTES or the admin cap) →
 * the balance must cover the whole estimate with no debt → plan first, then
 * credit lots FIFO.
 */
export function allocate(input: AllocationInput): AllocationResult {
  const gate = gateRejection(input);
  if (gate) return gate;
  const maxMinutes = maxVideoMinutes(input.caps);
  if (input.videoMinutes > maxMinutes) return { ok: false, reason: 'too_long', maxMinutes };
  const available = balanceOf(input);
  if (input.debt > 0 || available < input.tokens) {
    return { ok: false, reason: 'insufficient', needed: input.tokens, available };
  }
  const { plan, credit, lots } = chargeSplit(input.tokens, input);
  return { ok: true, split: { plan, credit, lots } };
}
