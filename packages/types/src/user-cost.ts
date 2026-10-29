// ═══════════════════════════════════════════════════
// Per-User Daily Cost Tracking (legacy, read-only)
// ═══════════════════════════════════════════════════
//
// `userCosts` / `userCostAdjustments` were the per-user dollar gate's
// counters. Since billing-roles P3.2 nothing writes them: there is no
// per-user dollar cap. They stay as history (vie-admin's cost views, the
// P3.8 migration reads the adjustments) and are erased by the GDPR cascade.

/** Daily cost aggregate for a single user.
 *  Compound unique index: `{ userId, date }`.
 *  `date` is a UTC `YYYY-MM-DD` string for deterministic grouping/sorting.
 */
export interface UserDailyCost {
  _id: string;
  userId: string;
  /** UTC date, formatted `YYYY-MM-DD` */
  date: string;
  /** Sum of raw LLM cost in USD for the day. Denormalized from `llm_usage`. */
  totalCostUsd: number;
  /** Number of completed videos billed against this day. */
  videoCount: number;
  /** Admin credit adjustments applied to this day (negative reduces usage). */
  creditAdjustmentUsd: number;
  updatedAt: string;
}

/** Admin audit record for credit adjustments.
 *
 *  Storage convention (CANONICAL, shared by Node + Python):
 *    - `amountUsd < 0` → credit granted (lowers effective spend)
 *    - `amountUsd > 0` → manual charge (raises effective spend)
 *
 *  The admin-facing HTTP endpoint accepts a user-friendly "positive = grant"
 *  input and inverts the sign before storage. Anything reading this collection
 *  directly should treat the value as already-signed.
 */
export interface UserCostAdjustment {
  _id: string;
  userId: string;
  /** UTC `YYYY-MM-DD`. */
  date: string;
  /** Signed USD as stored. Negative = credit, positive = charge. */
  amountUsd: number;
  reason: string;
  adminId: string;
  createdAt: string;
}
