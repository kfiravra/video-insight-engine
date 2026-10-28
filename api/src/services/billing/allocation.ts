import { FREE_VIDEO, PAID_MAX_VIDEO_MINUTES } from '@vie/types';
import { FREE_WINDOW_MS } from './minutes.js';
import type { AccountCaps, BucketSplit, LotSlice } from '@vie/types';

// Pure spend-order rules (proposal §4 as corrected by the brief): the whole
// video from the free bucket when it fits, otherwise plan minutes then credit
// lots FIFO. No I/O, no clock — `now` is an input — so the property tests can
// drive it directly.

export interface AllocationLot {
  lotId: string;
  minutesRemaining: number;
  expiresAt: Date | null;
  createdAt: Date;
}

export interface AllocationInput {
  minutes: number;
  now: Date;
  recentFreeVideoAt: Date[];
  /** Plan minutes left in the current cycle (0 without an active cycle). */
  planAvailable: number;
  lots: AllocationLot[];
  caps: AccountCaps;
  openHolds: number;
}

export interface FreeWindowState {
  /** Free videos allowed per rolling window. */
  cap: number;
  /** Free videos still inside the window. */
  inWindow: Date[];
  available: boolean;
  nextFreeAt: Date | null;
}

export interface VideoLimits {
  freeMaxMinutes: number;
  paidMaxMinutes: number;
}

export type AllocationRejection =
  | { ok: false; reason: 'blocked' }
  | { ok: false; reason: 'too_many_runs'; maxConcurrent: number }
  | { ok: false; reason: 'too_long'; maxMinutes: number }
  | { ok: false; reason: 'insufficient'; needed: number; available: number; nextFreeAt: Date | null };

export interface AllocationSuccess {
  ok: true;
  split: BucketSplit;
}

export type AllocationResult = AllocationSuccess | AllocationRejection;

/**
 * Rolling window (Q3): a free video counts while it is less than 24 h old,
 * so at exactly 24 h 00 the slot reopens. With the window full, the next
 * slot opens when the oldest counted video ages out.
 */
export function freeWindowState(recentFreeVideoAt: Date[], caps: AccountCaps, now: Date): FreeWindowState {
  const cap = caps.freeVideosPerDay ?? FREE_VIDEO.perWindow;
  const windowStart = now.getTime() - FREE_WINDOW_MS;
  const inWindow = recentFreeVideoAt
    .filter((at) => at.getTime() > windowStart)
    .sort((a, b) => a.getTime() - b.getTime());
  const available = inWindow.length < cap;
  const opener = cap > 0 && !available ? inWindow[inWindow.length - cap] : undefined;
  const nextFreeAt = opener ? new Date(opener.getTime() + FREE_WINDOW_MS) : null;
  return { cap, inWindow, available, nextFreeAt };
}

/** Caps only ever tighten: free 30 (Q2) and paid 180 (Q2) are ceilings an admin can lower. */
export function videoLimits(caps: AccountCaps): VideoLimits {
  const cap = caps.maxVideoMinutes ?? Number.POSITIVE_INFINITY;
  return {
    freeMaxMinutes: Math.min(FREE_VIDEO.maxVideoMinutes, cap),
    paidMaxMinutes: Math.min(PAID_MAX_VIDEO_MINUTES, cap),
  };
}

/** A lot can pay only while it has minutes and has not expired (expiry is exclusive). */
export function isLotSpendable(lot: AllocationLot, now: Date): boolean {
  return lot.minutesRemaining > 0 && (lot.expiresAt === null || lot.expiresAt.getTime() > now.getTime());
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
  return spendableLots(lots, now).reduce((sum, lot) => sum + lot.minutesRemaining, 0);
}

function takeFromLots(minutes: number, lots: AllocationLot[]): LotSlice[] {
  const slices: LotSlice[] = [];
  let left = minutes;
  for (const lot of lots) {
    if (left === 0) break;
    const take = Math.min(left, lot.minutesRemaining);
    slices.push({ lotId: lot.lotId, minutes: take });
    left -= take;
  }
  return slices;
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
 * Decide where a video's minutes come from.
 * - The free bucket takes the WHOLE video when it fits (≤ 30 min, or the
 *   capped limit) and a slot is open; never part of one.
 * - Otherwise (too long for free, or the window is full) the whole video
 *   goes to plan then credits (Q10 a); neither is ever capped per day.
 * - Paid minutes pay for at most 180 min (Q2, or the capped limit).
 */
export function allocate(input: AllocationInput): AllocationResult {
  const gate = gateRejection(input);
  if (gate) return gate;
  const { minutes, now, caps } = input;
  const limits = videoLimits(caps);
  const free = freeWindowState(input.recentFreeVideoAt, caps, now);
  const fitsFree = minutes <= limits.freeMaxMinutes;
  if (fitsFree && free.available) {
    return { ok: true, split: { free: minutes, plan: 0, credit: 0, lots: [] } };
  }
  if (minutes > limits.paidMaxMinutes) return { ok: false, reason: 'too_long', maxMinutes: limits.paidMaxMinutes };

  const lots = spendableLots(input.lots, now);
  const planAvailable = Math.max(input.planAvailable, 0);
  const creditAvailable = lots.reduce((sum, lot) => sum + lot.minutesRemaining, 0);
  if (planAvailable + creditAvailable < minutes) {
    const nextFreeAt = fitsFree ? free.nextFreeAt : null;
    return { ok: false, reason: 'insufficient', needed: minutes, available: planAvailable + creditAvailable, nextFreeAt };
  }
  const plan = Math.min(minutes, planAvailable);
  const slices = takeFromLots(minutes - plan, lots);
  return { ok: true, split: { free: 0, plan, credit: minutes - plan, lots: slices } };
}
