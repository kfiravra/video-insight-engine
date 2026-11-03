import { spendableLots } from './allocation.js';
import { planAvailable } from './plan-cycle.js';
import { toAllocationLot } from './views.js';
import type { AccountDraft, EntryFields } from './account-draft.js';

// Overdraft: a run that cost more than everything the account had leaves it
// owing (`buckets.debt`). Tokens that land on an owing account afterwards — a
// grant, a recurring lot, a new Pro cycle, an admin adjustment, a released
// hold — repay the debt first, so an account owes only while it has nothing
// spendable (debt > 0 ⇔ balance < 0). Keys, for the inflow keyed `<source>`:
//   repay:<source>             the debt bucket, + what was repaid
//   repay:<source>:plan        the plan cycle, − its part
//   repay:<source>:lot:<id>    a credit lot, − its part

/** What the repay entries carry over from the inflow that triggered them. */
export type RepayContext = Pick<EntryFields, 'source' | 'actorId' | 'auditId' | 'reason' | 'userId'>;

/**
 * Repay as much of the debt as the plan cycle, then spendable lots FIFO,
 * cover. A no-op without debt. Returns the tokens repaid.
 */
export function repayDebt(draft: AccountDraft, sourceKey: string, context: RepayContext): number {
  const { account, now } = draft;
  let left = account.buckets.debt.tokens;
  if (left <= 0) return 0;
  const repaid = left;

  const fromPlan = Math.min(left, planAvailable(account, now));
  if (fromPlan > 0) {
    account.buckets.plan.tokensUsed += fromPlan;
    draft.markChanged();
    draft.addEntry({ ...context, type: 'repay', bucket: 'plan', tokens: -fromPlan, idempotencyKey: `repay:${sourceKey}:plan` });
    left -= fromPlan;
  }
  for (const view of spendableLots(draft.lots.map(toAllocationLot), now)) {
    if (left === 0) break;
    const lot = draft.lots.find((candidate) => candidate._id.toHexString() === view.lotId);
    if (!lot) continue;
    const take = Math.min(left, lot.tokensRemaining);
    draft.changeLot(lot, -take);
    draft.addEntry({
      ...context, type: 'repay', bucket: 'credit', tokens: -take, lotId: lot._id,
      idempotencyKey: `repay:${sourceKey}:lot:${view.lotId}`,
    });
    left -= take;
  }

  const paid = repaid - left;
  if (paid > 0) {
    draft.changeDebt(-paid);
    draft.addEntry({ ...context, type: 'repay', bucket: 'debt', tokens: paid, idempotencyKey: `repay:${sourceKey}` });
  }
  return paid;
}
