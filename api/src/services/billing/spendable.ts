import { planAvailable } from './plan-cycle.js';
import { toAllocationLot } from './views.js';
import type { AccountDraft } from './account-draft.js';
import type { SpendableState } from './allocation.js';

/** What the account can spend right now (the draft is rolled to `now`). */
export function spendableState(draft: AccountDraft): SpendableState {
  const { account, now } = draft;
  return {
    now,
    planAvailable: planAvailable(account, now),
    lots: draft.lots.map(toAllocationLot),
    debt: account.buckets.debt.tokens,
  };
}
