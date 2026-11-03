import { ObjectId } from 'mongodb';
import { ValidationError } from '../../utils/errors.js';
import { spendableLots } from './allocation.js';
import { repayDebt } from './debt.js';
import { endPlan, expirePlanRemainder, openCycle, planAvailable } from './plan-cycle.js';
import { addMonthsUtc, MANUAL_PRO_TERM_MONTHS } from './tokens.js';
import { toAllocationLot } from './views.js';
import type { AccountCaps, PlanId } from '@vie/types';
import type { AccountDraft } from './account-draft.js';
import type { EntryContext } from './plan-cycle.js';
import type { CreditLotDocument } from '../../repositories/billing.repository.js';

// Pure admin transitions on an AccountDraft (Contract 1). Every entry they
// write carries the actor, the reason and the audit id, and is keyed
// `admin:<auditId>[:<suffix>]` with `source.ref = auditId`. Tokens an admin
// adds repay the account's debt first (`repay:admin:<auditId>…`).

export interface AdminAudit {
  actorId: string;
  reason: string;
  auditId: ObjectId;
}

export interface SetPlanInput {
  planId: PlanId;
  periodEnd?: Date;
}

export interface GrantLotInput {
  tokens: number;
  expiresAt?: Date | null;
}

export interface AdjustInput {
  bucket: 'plan' | 'credit';
  tokens: number;
}

function auditContext(audit: AdminAudit): EntryContext {
  const { actorId, reason, auditId } = audit;
  return { source: { kind: 'admin', ref: auditId.toHexString() }, actorId, reason, auditId };
}

function adminKey(audit: AdminAudit, suffix?: string): string {
  const base = `admin:${audit.auditId.toHexString()}`;
  return suffix ? `${base}:${suffix}` : base;
}

/**
 * Pro: a manual plan from now to `periodEnd` (default 12 months) with the
 * first monthly cycle granted now (repaying debt first); whatever was left of
 * a previous cycle expires. Free: plan tokens stop now.
 */
export function applySetPlan(draft: AccountDraft, input: SetPlanInput, audit: AdminAudit): void {
  const context = auditContext(audit);
  if (input.planId === 'free') {
    endPlan(draft, adminKey(audit, 'expire'), context);
    return;
  }
  const periodEnd = input.periodEnd ?? addMonthsUtc(draft.now, MANUAL_PRO_TERM_MONTHS);
  if (periodEnd <= draft.now) throw new ValidationError('periodEnd must be in the future');
  expirePlanRemainder(draft, adminKey(audit, 'expire'), context);
  draft.account.plan = { id: 'pro', status: 'active', source: 'manual', periodStart: draft.now, periodEnd };
  const grantKey = openCycle(draft, 0, adminKey(audit, 'grant'), context);
  if (grantKey) repayDebt(draft, grantKey, context);
}

function newLot(draft: AccountDraft, tokens: number, expiresAt: Date | null, audit: AdminAudit): CreditLotDocument {
  return {
    _id: new ObjectId(),
    accountId: draft.accountId,
    tokensGranted: tokens,
    tokensRemaining: tokens,
    source: { kind: 'admin', ref: audit.auditId.toHexString() },
    expiresAt,
    createdAt: draft.now,
  };
}

/** A credit lot of `tokens`; no expiry unless one is given. Repays debt first. Returns the lot id. */
export function applyGrantLot(draft: AccountDraft, input: GrantLotInput, audit: AdminAudit): ObjectId {
  const expiresAt = input.expiresAt ?? null;
  if (expiresAt && expiresAt <= draft.now) throw new ValidationError('expiresAt must be in the future');
  const lot = newLot(draft, input.tokens, expiresAt, audit);
  const context = auditContext(audit);
  draft.addLot(lot);
  draft.addEntry({
    type: 'grant', bucket: 'credit', tokens: input.tokens, lotId: lot._id,
    idempotencyKey: adminKey(audit), ...context,
  });
  repayDebt(draft, adminKey(audit), context);
  return lot._id;
}

const CAP_FIELDS = ['maxVideoMinutes', 'maxConcurrent', 'blocked'] as const;

function mergeCap<K extends keyof AccountCaps>(caps: AccountCaps, patch: Partial<AccountCaps>, field: K): void {
  const value = patch[field];
  if (value === undefined) return;
  if (value === null) delete caps[field];
  else caps[field] = value;
}

/** setCaps writes no ledger entry, so the account remembers this many of its auditIds to spot a retry (I1). */
const CAPS_AUDIT_MEMORY = 20;

/** True when this auditId's caps change was already applied (a retried call). */
export function capsAlreadyApplied(draft: AccountDraft, audit: AdminAudit): boolean {
  return (draft.account.capsAuditIds ?? []).includes(audit.auditId.toHexString());
}

/** Merge a caps patch: a value sets the field, null clears it, an absent key leaves it. */
export function applySetCaps(draft: AccountDraft, patch: Partial<AccountCaps>, audit: AdminAudit): void {
  const caps: AccountCaps = { ...draft.account.caps };
  for (const field of CAP_FIELDS) mergeCap(caps, patch, field);
  draft.account.caps = caps;
  const applied = [...(draft.account.capsAuditIds ?? []), audit.auditId.toHexString()];
  draft.account.capsAuditIds = applied.slice(-CAPS_AUDIT_MEMORY);
  draft.markChanged();
}

function adjustPlan(draft: AccountDraft, tokens: number, audit: AdminAudit): void {
  // The draft is rolled to now, so a Pro account always has a current cycle.
  if (draft.account.plan.id !== 'pro') throw new ValidationError('This account has no plan cycle to adjust');
  if (tokens < 0 && planAvailable(draft.account, draft.now) < -tokens) {
    throw new ValidationError('The adjustment would leave a negative plan balance');
  }
  draft.account.buckets.plan.tokensGranted += tokens;
  draft.markChanged();
  draft.addEntry({ type: 'adjust', bucket: 'plan', tokens, idempotencyKey: adminKey(audit), ...auditContext(audit) });
}

function adjustCredit(draft: AccountDraft, tokens: number, audit: AdminAudit): void {
  const context = auditContext(audit);
  if (tokens > 0) {
    const lot = newLot(draft, tokens, null, audit);
    draft.addLot(lot);
    draft.addEntry({ type: 'adjust', bucket: 'credit', tokens, lotId: lot._id, idempotencyKey: adminKey(audit), ...context });
    return;
  }
  const lots = spendableLots(draft.lots.map(toAllocationLot), draft.now);
  const available = lots.reduce((sum, lot) => sum + lot.tokensRemaining, 0);
  if (available < -tokens) throw new ValidationError('The adjustment would leave a negative credit balance');
  let left = -tokens;
  for (const view of lots) {
    if (left === 0) break;
    const take = Math.min(left, view.tokensRemaining);
    const lot = draft.lots.find((candidate) => candidate._id.toHexString() === view.lotId);
    if (!lot) continue;
    draft.changeLot(lot, -take);
    draft.addEntry({
      type: 'adjust', bucket: 'credit', tokens: -take, lotId: lot._id,
      idempotencyKey: adminKey(audit, `lot:${view.lotId}`), ...context,
    });
    left -= take;
  }
}

/**
 * A signed correction; never takes a bucket below zero (Q17). Positive credit
 * = a new non-expiring lot. A positive adjustment repays debt first.
 */
export function applyAdjust(draft: AccountDraft, input: AdjustInput, audit: AdminAudit): void {
  if (input.bucket === 'plan') adjustPlan(draft, input.tokens, audit);
  else adjustCredit(draft, input.tokens, audit);
  if (input.tokens > 0) repayDebt(draft, adminKey(audit), auditContext(audit));
}
