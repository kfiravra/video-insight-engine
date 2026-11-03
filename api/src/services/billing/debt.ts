import { ObjectId } from 'mongodb';
import { chargeSplit } from './allocation.js';
import { spendableState } from './spendable.js';
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
  const owed = draft.account.buckets.debt.tokens;
  if (owed <= 0) return 0;
  // The same spend order as any charge (plan, then lots FIFO); what they cannot cover stays owed.
  const split = chargeSplit(owed, spendableState(draft));
  if (split.plan > 0) {
    draft.account.buckets.plan.tokensUsed += split.plan;
    draft.markChanged();
    draft.addEntry({ ...context, type: 'repay', bucket: 'plan', tokens: -split.plan, idempotencyKey: `repay:${sourceKey}:plan` });
  }
  for (const slice of split.lots) {
    const lot = draft.findLot(new ObjectId(slice.lotId));
    if (!lot || slice.tokens <= 0) continue;
    draft.changeLot(lot, -slice.tokens);
    draft.addEntry({
      ...context, type: 'repay', bucket: 'credit', tokens: -slice.tokens, lotId: lot._id,
      idempotencyKey: `repay:${sourceKey}:lot:${slice.lotId}`,
    });
  }
  const paid = split.plan + split.credit;
  if (paid > 0) {
    draft.changeDebt(-paid);
    draft.addEntry({ ...context, type: 'repay', bucket: 'debt', tokens: paid, idempotencyKey: `repay:${sourceKey}` });
  }
  return paid;
}
