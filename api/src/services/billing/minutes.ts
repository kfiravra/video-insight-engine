import { FREE_VIDEO, MIN_MINUTES_PER_VIDEO } from '@vie/types';
import { ValidationError } from '../../utils/errors.js';

/** A manual Pro plan (admin "set plan") runs this long unless the admin gives an end date. */
export const MANUAL_PRO_TERM_MONTHS = 12;

/** Rolling free-video window (Q3). */
export const FREE_WINDOW_MS = FREE_VIDEO.windowHours * 60 * 60 * 1000;

/** Upper bound for any single admin minutes amount — catches a mistyped extra zero. */
export const MAX_ADMIN_MINUTES = 100_000;

/**
 * Minutes a video costs: its length rounded up to the minute, never less
 * than `MIN_MINUTES_PER_VIDEO` (Q8). Unknown length (0, e.g. a live stream
 * whose metadata has no duration) costs the minimum.
 */
export function chargeMinutes(durationSec: number): number {
  if (!Number.isFinite(durationSec) || durationSec < 0) {
    throw new ValidationError('durationSec must be a finite, non-negative number');
  }
  return Math.max(Math.ceil(durationSec / 60), MIN_MINUTES_PER_VIDEO);
}

/**
 * `date` plus `months` calendar months in UTC, clamped to the target month's
 * last day (Jan 31 + 1 → Feb 28/29). Always computed from the same anchor,
 * so monthly cycles never drift (Jan 31 → Feb 28 → Mar 31).
 */
export function addMonthsUtc(date: Date, months: number): Date {
  const firstOfTarget = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1));
  const year = firstOfTarget.getUTCFullYear();
  const month = firstOfTarget.getUTCMonth();
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const result = new Date(date.getTime());
  result.setUTCFullYear(year, month, Math.min(date.getUTCDate(), lastDay));
  return result;
}

/** Whole months elapsed from `anchor` to `now` (0 while inside the first month). */
export function monthsElapsed(anchor: Date, now: Date): number {
  let months = (now.getUTCFullYear() - anchor.getUTCFullYear()) * 12 + (now.getUTCMonth() - anchor.getUTCMonth());
  if (addMonthsUtc(anchor, months) > now) months -= 1;
  return Math.max(months, 0);
}

/**
 * Split `total` across `weights` proportionally, rounded to 1e-6, with the
 * rounding remainder on the last share so the shares sum to `total`.
 */
export function splitProportionally(total: number, weights: number[]): number[] {
  const sum = weights.reduce((a, b) => a + b, 0);
  if (sum === 0) return weights.map(() => 0);
  const round = (n: number): number => Math.round(n * 1e6) / 1e6;
  const shares = weights.map((w) => round((total * w) / sum));
  const drift = round(total - shares.reduce((a, b) => a + b, 0));
  shares[shares.length - 1] = round(shares[shares.length - 1] + drift);
  return shares;
}
