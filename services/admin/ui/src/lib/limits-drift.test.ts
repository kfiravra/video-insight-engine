import { describe, it, expect } from 'vitest';
import billingTypes from '../../../../../packages/types/src/billing.ts?raw';
import billingAdmin from '../../../../../api/src/services/billing/billing-admin.ts?raw';
import billingMinutes from '../../../../../api/src/services/billing/minutes.ts?raw';
import { CAP_LIMITS, MAX_ADMIN_MINUTES, MAX_REASON_LENGTH, PRO_PERIOD_MINUTES } from './billing-forms';

/**
 * The form checks in `billing-forms.ts` spare the operator a round trip by
 * repeating the ranges vie-api enforces. Nothing links the copies, so this reads
 * the server's source and fails when one side moves: the same guard
 * `test_billing_ledger_route.py` puts on the free-video constants.
 */

function numberAfter(source: string, pattern: RegExp, what: string): number {
  const match = pattern.exec(source);
  if (!match) throw new Error(`${what}: the server source no longer matches ${pattern}; update this test with it`);
  return Number(match[1].replaceAll('_', ''));
}

/** The `.min(a).max(b)` range of a field in billing-admin's caps schema. */
function capRange(field: string): { min: number; max: number } {
  const line = new RegExp(`${field}:\\s*z\\.number\\(\\)\\.int\\(\\)\\.min\\(([\\w_]+)\\)\\.max\\(([\\w_]+)\\)`).exec(billingAdmin);
  if (!line) throw new Error(`${field}: the caps schema no longer matches; update this test with it`);
  const value = (token: string): number =>
    /^\d+$/.test(token) ? Number(token) : numberAfter(billingTypes, new RegExp(`export const ${token} = (\\d+)`), token);
  return { min: value(line[1]), max: value(line[2]) };
}

describe('limits copied from the server', () => {
  it('should allow the same range of minutes per video as the caps schema', () => {
    expect(CAP_LIMITS.maxVideoMinutes).toEqual(capRange('maxVideoMinutes'));
  });

  it('should allow the same range of free videos per day as the caps schema', () => {
    expect(CAP_LIMITS.freeVideosPerDay).toEqual(capRange('freeVideosPerDay'));
  });

  it('should allow the same range of concurrent runs as the caps schema', () => {
    expect(CAP_LIMITS.maxConcurrent).toEqual(capRange('maxConcurrent'));
  });

  it('should cap an admin grant or adjustment at the same minutes as BillingService', () => {
    expect(MAX_ADMIN_MINUTES).toBe(numberAfter(billingMinutes, /export const MAX_ADMIN_MINUTES = ([\d_]+)/, 'MAX_ADMIN_MINUTES'));
  });

  it('should allow a reason as long as the server does', () => {
    expect(MAX_REASON_LENGTH).toBe(numberAfter(billingAdmin, /reason: z\.string\(\)\.trim\(\)\.min\(1[^)]*\)\.max\((\d+)\)/, 'reason'));
  });

  it('should quote the minutes a Pro plan grants each month as the plan table does', () => {
    expect(PRO_PERIOD_MINUTES).toBe(numberAfter(billingTypes, /pro:\s*\{\s*periodMinutes:\s*(\d+)/, 'PLANS.pro.periodMinutes'));
  });
});
