import { PLANS } from '@vie/types';
import { addMonthsUtc, monthsElapsed } from './minutes.js';
import type { AccountDraft, EntryFields } from './account-draft.js';
import type { BillingAccountDocument, PlanBucketDocument } from '../../repositories/billing.repository.js';

// Pro runs in a term (manual Pro: 12 months) split into monthly cycles. Each
// cycle grants PLANS.pro.periodMinutes; whatever is left when the cycle ends
// expires — no rollover (Q5). Every transition writes ledger entries, so the
// plan bucket always equals Σ plan-bucket entries (debits excluded).

/** Who caused a transition: carried onto the entries it writes. */
export type EntryContext = Pick<EntryFields, 'source' | 'actorId' | 'auditId' | 'reason'>;

const EMPTY_PLAN_BUCKET: PlanBucketDocument = { cycleStart: null, cycleEnd: null, minutesGranted: 0, minutesUsed: 0 };

/** Plan minutes spendable now. Assumes the account was rolled to `now` first. */
export function planAvailable(account: BillingAccountDocument, now: Date): number {
  const { plan, buckets } = account;
  if (plan.id !== 'pro' || !buckets.plan.cycleEnd || buckets.plan.cycleEnd <= now) return 0;
  return Math.max(buckets.plan.minutesGranted - buckets.plan.minutesUsed, 0);
}

/** True when `cycleStart` is the account's current plan cycle (a hold's plan slice can go back to it). */
export function isCurrentCycle(account: BillingAccountDocument, cycleStart: Date | null): boolean {
  const current = account.buckets.plan.cycleStart;
  return cycleStart !== null && current !== null && current.getTime() === cycleStart.getTime();
}

/** Expire what is left of the current cycle and clear the bucket. */
export function expirePlanRemainder(draft: AccountDraft, idempotencyKey: string, context: EntryContext): void {
  const bucket = draft.account.buckets.plan;
  const remaining = bucket.minutesGranted - bucket.minutesUsed;
  if (remaining > 0) {
    draft.addEntry({ type: 'expire', bucket: 'plan', minutes: -remaining, idempotencyKey, ...context });
  }
  if (bucket.cycleStart !== null || bucket.minutesGranted !== 0 || bucket.minutesUsed !== 0) {
    draft.account.buckets.plan = { ...EMPTY_PLAN_BUCKET };
    draft.markChanged();
  }
}

/** Open cycle `index` of the current term and grant its minutes. */
export function openCycle(draft: AccountDraft, index: number, idempotencyKey: string, context: EntryContext): void {
  const { periodStart, periodEnd } = draft.account.plan;
  if (!periodStart) return;
  const cycleStart = addMonthsUtc(periodStart, index);
  const naturalEnd = addMonthsUtc(periodStart, index + 1);
  const cycleEnd = periodEnd && periodEnd < naturalEnd ? periodEnd : naturalEnd;
  const minutes = PLANS.pro.periodMinutes;
  draft.account.buckets.plan = { cycleStart, cycleEnd, minutesGranted: minutes, minutesUsed: 0 };
  draft.markChanged();
  draft.addEntry({ type: 'grant', bucket: 'plan', minutes, idempotencyKey, ...context });
}

/** Back to the free plan: plan minutes stop now. */
export function endPlan(draft: AccountDraft, expireKey: string, context: EntryContext): void {
  expirePlanRemainder(draft, expireKey, context);
  draft.account.plan = { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null };
  draft.markChanged();
}

/**
 * Bring the plan up to `draft.now`: an ended cycle expires its remainder and
 * the cycle containing `now` opens with a fresh grant (idle months in between
 * grant nothing); an ended term reverts to free. Idempotency keys name the
 * cycle, so concurrent rollers write each transition once.
 */
export function rollPlanCycle(draft: AccountDraft): void {
  const { plan, buckets } = draft.account;
  const now = draft.now;
  if (plan.id !== 'pro' || !plan.periodStart) return;
  const termEnded = plan.periodEnd !== null && plan.periodEnd <= now;
  if (!termEnded && buckets.plan.cycleEnd && buckets.plan.cycleEnd > now) return;

  const account = draft.accountId.toHexString();
  const context: EntryContext = { source: { kind: 'system', ref: `plan-cycle:${account}` } };
  const endedCycle = buckets.plan.cycleStart?.toISOString() ?? 'none';
  const expireKey = `roll:${account}:expire:${endedCycle}`;
  if (termEnded) {
    endPlan(draft, expireKey, context);
    return;
  }
  expirePlanRemainder(draft, expireKey, context);
  const index = monthsElapsed(plan.periodStart, now);
  const cycleStart = addMonthsUtc(plan.periodStart, index).toISOString();
  openCycle(draft, index, `roll:${account}:grant:${cycleStart}`, context);
}
