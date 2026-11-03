import { PLANS } from '@vie/types';
import { addMonthsUtc, monthsElapsed } from './tokens.js';
import type { AccountDraft, EntryFields } from './account-draft.js';
import type { BillingAccountDocument, PlanBucketDocument } from '../../repositories/billing.repository.js';

// Pro runs in a term (manual Pro: 12 months) split into monthly cycles. Each
// cycle grants PLANS.pro.periodTokens; whatever is left when the cycle ends
// expires — no rollover. Every transition writes ledger entries, so the plan
// bucket always equals Σ plan-bucket entries (hold-mirroring debits excluded).
// A new cycle's tokens repay debt first: the caller runs `repayDebt` with the
// grant key `rollPlanCycle` / `openCycle` return.

/** Who caused a transition: carried onto the entries it writes. */
export type EntryContext = Pick<EntryFields, 'source' | 'actorId' | 'auditId' | 'reason'>;

const EMPTY_PLAN_BUCKET: PlanBucketDocument = { cycleStart: null, cycleEnd: null, tokensGranted: 0, tokensUsed: 0 };

/** Plan tokens spendable now. Assumes the account was rolled to `now` first. */
export function planAvailable(account: BillingAccountDocument, now: Date): number {
  const { plan, buckets } = account;
  if (plan.id !== 'pro' || !buckets.plan.cycleEnd || buckets.plan.cycleEnd <= now) return 0;
  return Math.max(buckets.plan.tokensGranted - buckets.plan.tokensUsed, 0);
}

/** True when `cycleStart` is the account's current plan cycle (a hold's plan slice can go back to it). */
export function isCurrentCycle(account: BillingAccountDocument, cycleStart: Date | null): boolean {
  const current = account.buckets.plan.cycleStart;
  return cycleStart !== null && current !== null && current.getTime() === cycleStart.getTime();
}

/** Expire what is left of the current cycle and clear the bucket. */
export function expirePlanRemainder(draft: AccountDraft, idempotencyKey: string, context: EntryContext): void {
  const bucket = draft.account.buckets.plan;
  const remaining = bucket.tokensGranted - bucket.tokensUsed;
  if (remaining > 0) {
    draft.addEntry({ type: 'expire', bucket: 'plan', tokens: -remaining, idempotencyKey, ...context });
  }
  if (bucket.cycleStart !== null || bucket.tokensGranted !== 0 || bucket.tokensUsed !== 0) {
    draft.account.buckets.plan = { ...EMPTY_PLAN_BUCKET };
    draft.markChanged();
  }
}

/** Open cycle `index` of the current term and grant its tokens. Returns the grant's key (null when nothing opened). */
export function openCycle(draft: AccountDraft, index: number, idempotencyKey: string, context: EntryContext): string | null {
  const { periodStart, periodEnd } = draft.account.plan;
  if (!periodStart) return null;
  const cycleStart = addMonthsUtc(periodStart, index);
  const naturalEnd = addMonthsUtc(periodStart, index + 1);
  const cycleEnd = periodEnd && periodEnd < naturalEnd ? periodEnd : naturalEnd;
  const tokens = PLANS.pro.periodTokens;
  draft.account.buckets.plan = { cycleStart, cycleEnd, tokensGranted: tokens, tokensUsed: 0 };
  draft.markChanged();
  draft.addEntry({ type: 'grant', bucket: 'plan', tokens, idempotencyKey, ...context });
  return idempotencyKey;
}

/** Back to the free plan: plan tokens stop now. */
export function endPlan(draft: AccountDraft, expireKey: string, context: EntryContext): void {
  expirePlanRemainder(draft, expireKey, context);
  draft.account.plan = { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null };
  draft.markChanged();
}

/**
 * Bring the plan up to `draft.now`: an ended cycle expires its remainder and
 * the cycle containing `now` opens with a fresh grant (idle months in between
 * grant nothing); an ended term reverts to free. Idempotency keys name the
 * cycle, so concurrent rollers write each transition once. Returns the new
 * cycle's grant key, or null when no cycle opened.
 */
export function rollPlanCycle(draft: AccountDraft): string | null {
  const { plan, buckets } = draft.account;
  const now = draft.now;
  if (plan.id !== 'pro' || !plan.periodStart) return null;
  const termEnded = plan.periodEnd !== null && plan.periodEnd <= now;
  if (!termEnded && buckets.plan.cycleEnd && buckets.plan.cycleEnd > now) return null;

  const account = draft.accountId.toHexString();
  const context = rollContext(draft);
  const endedCycle = buckets.plan.cycleStart?.toISOString() ?? 'none';
  const expireKey = `roll:${account}:expire:${endedCycle}`;
  if (termEnded) {
    endPlan(draft, expireKey, context);
    return null;
  }
  expirePlanRemainder(draft, expireKey, context);
  const index = monthsElapsed(plan.periodStart, now);
  const cycleStart = addMonthsUtc(plan.periodStart, index).toISOString();
  return openCycle(draft, index, `roll:${account}:grant:${cycleStart}`, context);
}

/** The context entries of a cycle roll carry. */
export function rollContext(draft: AccountDraft): EntryContext {
  return { source: { kind: 'system', ref: `plan-cycle:${draft.accountId.toHexString()}` } };
}
