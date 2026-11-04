import { ValidationError } from '../../utils/errors.js';

// What tokens cost and what an execution charges. Internal to the api: the
// markup and the estimate never ship in @vie/types (the web only ever shows
// token amounts, never dollars).

/** 1 token = $0.001 of retail value. */
export const TOKENS_PER_USD = 1000;

/** Retail value per dollar of raw LLM cost (`llm_usage.cost_usd`, `pipeline.timing.costUsd`). */
export const COST_MARKUP = 3;

/**
 * A video's estimate before it runs, from its length: a fixed part (plan,
 * synthesis, judges) plus a per-minute part (transcript, extraction, vision).
 * Calibrated against observed runs (222 s → $0.2526); re-fit from prod data.
 */
export const ESTIMATE = { fixedUsd: 0.22, perMinuteUsd: 0.006 } as const;

/** A manual Pro plan (admin "set plan") runs this long unless the admin gives an end date. */
export const MANUAL_PRO_TERM_MONTHS = 12;

/** Upper bound for any single admin token amount — catches a mistyped extra zero. */
export const MAX_ADMIN_TOKENS = 1_000_000;

/** Rounds away float noise (0.1 × 3 × 1000 = 300.00000000000006) before rounding up. */
function roundMicro(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

/**
 * Tokens for `costUsd` of raw LLM cost: `ceil(cost × markup × 1000)`, at
 * least 1 when anything was spent, 0 for a free execution.
 */
export function tokensForUsd(costUsd: number): number {
  if (!Number.isFinite(costUsd) || costUsd < 0) {
    throw new ValidationError('costUsd must be a finite, non-negative number');
  }
  if (costUsd === 0) return 0;
  return Math.max(Math.ceil(roundMicro(costUsd * COST_MARKUP * TOKENS_PER_USD)), 1);
}

/** Whole minutes a video counts as (rounded up); unknown length (0) counts as 0. */
export function videoMinutes(durationSec: number): number {
  if (!Number.isFinite(durationSec) || durationSec < 0) {
    throw new ValidationError('durationSec must be a finite, non-negative number');
  }
  return Math.ceil(durationSec / 60);
}

/** The tokens a video holds before it runs (the estimate), from its length. */
export function estimateTokens(durationSec: number): number {
  const minutes = videoMinutes(durationSec);
  return tokensForUsd(roundMicro(ESTIMATE.fixedUsd + ESTIMATE.perMinuteUsd * minutes));
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
  const shares = weights.map((w) => roundMicro((total * w) / sum));
  const drift = roundMicro(total - shares.reduce((a, b) => a + b, 0));
  shares[shares.length - 1] = roundMicro(shares[shares.length - 1] + drift);
  return shares;
}
