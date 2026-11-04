import { describe, it, expect } from 'vitest';
import { ObjectId } from 'mongodb';
import { applySetCaps, applySetPlan } from '../admin-ops.js';
import { toLedgerView, toSnapshot, toUsageSummary } from '../views.js';
import { adminAudit, makeAccountDoc } from './billing-fixtures.js';
import { grantTokens, holdVideo, hoursAfterT0, settleRun, signedUpLedger, T0 } from './pure-ledger.js';
import type { LedgerEntryDocument } from '../../../repositories/billing.repository.js';

/** Every key of a plain-JSON value, at any depth. */
function keysOf(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(keysOf);
  if (value === null || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, item]) => [key, ...keysOf(item)]);
}

describe('toUsageSummary', () => {
  it('should show a new account its 1,000 signup tokens and nothing else', () => {
    const ledger = signedUpLedger();

    expect(toUsageSummary(ledger.account, ledger.lots, T0)).toEqual({
      plan: { id: 'free', periodEnd: null },
      balance: 1000,
      planTokens: null,
      credits: { available: 1000, lots: [{ tokens: 1000, expiresAt: null }] },
      debt: 0,
      holds: [],
      maxVideoMinutes: 180,
      blocked: false,
      signupGrantTokens: 1000,
    });
  });

  it('should show the Pro cycle, the holds in flight and the admin caps', () => {
    const ledger = signedUpLedger();
    ledger.step(T0, (draft) => applySetPlan(draft, { planId: 'pro' }, adminAudit()));
    holdVideo(ledger, T0, 'run-1', 600);
    ledger.step(T0, (draft) => applySetCaps(draft, { maxVideoMinutes: 60, blocked: true }, adminAudit()));

    const summary = toUsageSummary(ledger.account, ledger.lots, T0);

    expect(summary).toMatchObject({
      plan: { id: 'pro', periodEnd: '2027-11-01T10:00:00.000Z' },
      balance: 20_160,
      planTokens: { granted: 20_000, used: 840, remaining: 19_160, cycleEnd: '2026-12-01T10:00:00.000Z' },
      holds: [{ youtubeId: 'dQw4w9WgXcQ', tokens: 840 }],
      maxVideoMinutes: 60,
      blocked: true,
    });
  });

  it('should show debt as a negative balance', () => {
    const ledger = signedUpLedger();
    holdVideo(ledger, T0, 'run-1', 600);
    settleRun(ledger, hoursAfterT0(1), 'run-1', 0.4);

    expect(toUsageSummary(ledger.account, ledger.lots, hoursAfterT0(1))).toMatchObject({
      balance: -200, debt: 200, credits: { available: 0, lots: [] },
    });
  });

  it('should leave expired lots out of the credits', () => {
    const ledger = signedUpLedger();
    grantTokens(ledger, T0, 500, hoursAfterT0(1));

    const summary = toUsageSummary(ledger.account, ledger.lots, hoursAfterT0(2));

    expect(summary.credits).toEqual({ available: 1000, lots: [{ tokens: 1000, expiresAt: null }] });
  });

  it('should say 0 signup tokens on an account that never got the grant (migrated accounts get it from the script)', () => {
    expect(toUsageSummary(makeAccountDoc(), [], T0).signupGrantTokens).toBe(0);
  });

  it('should carry no dollar field anywhere', () => {
    const ledger = signedUpLedger();
    ledger.step(T0, (draft) => applySetPlan(draft, { planId: 'pro' }, adminAudit()));
    holdVideo(ledger, T0, 'run-1', 600);

    const keys = keysOf(toUsageSummary(ledger.account, ledger.lots, T0));

    expect(keys.filter((key) => /usd|cost|price/i.test(key))).toEqual([]);
  });
});

describe('toSnapshot', () => {
  it('should snapshot the account in tokens: plan, credits, debt, balance and the signup date', () => {
    const ledger = signedUpLedger();
    ledger.step(T0, (draft) => applySetPlan(draft, { planId: 'pro' }, adminAudit()));
    holdVideo(ledger, T0, 'run-1', 600);

    expect(toSnapshot(ledger.account, ledger.lots, T0)).toMatchObject({
      planTokens: { cycleStart: T0.toISOString(), cycleEnd: '2026-12-01T10:00:00.000Z', granted: 20_000, used: 840, remaining: 19_160 },
      credits: { available: 1000 },
      debt: 0,
      balance: 20_160,
      signupGrantAt: T0.toISOString(),
      openHolds: 1,
    });
  });

  it('should give a null signup date to an account without the grant', () => {
    expect(toSnapshot(makeAccountDoc(), [], T0)).toMatchObject({ signupGrantAt: null, balance: 0, debt: 0 });
  });
});

describe('toLedgerView', () => {
  const base = (): LedgerEntryDocument => ({
    _id: new ObjectId(), accountId: new ObjectId(), userId: new ObjectId(), type: 'grant', bucket: 'credit',
    source: { kind: 'admin', ref: 'a1' }, idempotencyKey: 'admin:a1', createdAt: T0,
  });

  it('should show a token entry as unit tokens with its amount', () => {
    const view = toLedgerView({ ...base(), unit: 'tokens', tokens: -840, type: 'debit', mirrorsHold: true });

    expect(view).toMatchObject({ unit: 'tokens', amount: -840, mirrorsHold: true });
    expect(view).not.toHaveProperty('tokens');
  });

  it('should show a minute entry from before the migration as unit minutes', () => {
    const view = toLedgerView({ ...base(), minutes: 300 });

    expect(view).toMatchObject({ unit: 'minutes', amount: 300 });
    expect(view).not.toHaveProperty('minutes');
    expect(view).not.toHaveProperty('mirrorsHold');
  });

  it('should carry the execution fields of a chat debit', () => {
    const view = toLedgerView({ ...base(), unit: 'tokens', tokens: -12, type: 'debit', executionKind: 'chat', executionId: 'ex-1' });

    expect(view).toMatchObject({ executionKind: 'chat', executionId: 'ex-1' });
  });
});
