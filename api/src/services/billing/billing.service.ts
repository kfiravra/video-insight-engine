import { FastifyBaseLogger } from 'fastify';
import { ValidationError } from '../../utils/errors.js';
import { allocate } from './allocation.js';
import { BillingAdmin } from './billing-admin.js';
import { HoldFinalizedError, rejectionToError } from './billing-errors.js';
import { BillingUnits } from './billing-unit.js';
import { applyHold, applyRelease, applySettle, findHold } from './hold-ops.js';
import { chargeMinutes } from './minutes.js';
import { planAvailable } from './plan-cycle.js';
import { checkProjection } from './projection.js';
import { toAllocationLot, toHoldView, toSnapshot, toUsageSummary } from './views.js';
import type { AccountCaps, AccountDetail, AccountSnapshot, HoldView, PlanId, UsageSummary } from '@vie/types';
import type { AccountDraft } from './account-draft.js';
import type { AllocationResult } from './allocation.js';
import type { AccountDetailOptions, AdminMutationContext, MutationResult } from './billing-admin.js';
import type { ReleaseReason } from './hold-ops.js';
import type { ProjectionCheck } from './projection.js';
import type { BillingRepository } from '../../repositories/billing.repository.js';
import type { UserRepository } from '../../repositories/user.repository.js';

export type { AccountDetailOptions, AdminMutationContext, MutationResult } from './billing-admin.js';
export type { ReleaseReason } from './hold-ops.js';

/** A priced, not-yet-held decision for one video. Holding re-decides on fresh state. */
export interface Quote {
  userId: string;
  youtubeId: string;
  durationSec: number;
  minutes: number;
  decision: AllocationResult;
  quotedAt: Date;
}

export interface CostOptions {
  /** LLM $ attributed to the run (margin only). */
  costUsd?: number;
}

/** What happened to a run's hold. Settle and release never throw for a missing or finished hold. */
export type HoldOutcomeStatus = 'debited' | 'released' | 'already_debited' | 'already_released' | 'no_hold';

export interface HoldOutcome {
  requestId: string;
  status: HoldOutcomeStatus;
  /** Minutes debited or released by this call. */
  minutes?: number;
}

const MAX_REQUEST_ID_LENGTH = 200;

function validateRequestId(requestId: string): void {
  if (requestId.length === 0 || requestId.length > MAX_REQUEST_ID_LENGTH) {
    throw new ValidationError(`requestId must be 1–${MAX_REQUEST_ID_LENGTH} characters`);
  }
}

function finishedStatus(entries: Array<{ type: string }>): HoldOutcomeStatus {
  if (entries.some((entry) => entry.type === 'debit')) return 'already_debited';
  if (entries.some((entry) => entry.type === 'release')) return 'already_released';
  return 'no_hold';
}

function validateCost(costUsd: number | undefined): void {
  if (costUsd !== undefined && (!Number.isFinite(costUsd) || costUsd < 0)) {
    throw new ValidationError('costUsd must be a finite, non-negative number');
  }
}

/**
 * The minutes ledger (billing-roles P1.4). quote → hold at submit, settle on
 * completion, release on failure or when the video was served without a run
 * (cached / attached). Every change is one transaction over the account, its
 * lots and append-only ledger entries; the account's buckets and the lots'
 * balances are projections that always equal Σ ledger (`verifyAccount`).
 * The admin API (Contract 1) is delegated to BillingAdmin.
 */
export class BillingService {
  private readonly units: BillingUnits;
  private readonly admin: BillingAdmin;

  constructor(
    private readonly repo: BillingRepository,
    userRepository: UserRepository,
    private readonly logger: FastifyBaseLogger,
    private readonly clock: () => Date = () => new Date(),
  ) {
    this.units = new BillingUnits(repo, clock);
    this.admin = new BillingAdmin(repo, this.units, userRepository);
  }

  // ─── Runs ───

  /** Price a video and decide where its minutes would come from, without holding anything. */
  async quote(userId: string, youtubeId: string, durationSec: number): Promise<Quote> {
    const minutes = chargeMinutes(durationSec);
    const draft = await this.units.read(userId);
    return { userId, youtubeId, durationSec, minutes, decision: this.allocateFor(draft, minutes), quotedAt: draft.now };
  }

  /**
   * Take the quoted minutes out of the account for run `requestId`, deciding
   * again on the account's current state. One hold per requestId: a second
   * call returns the open hold. Throws InsufficientMinutesError,
   * VideoTooLongForPlanError, AccountBlockedError, TooManyRunsError, or
   * HoldFinalizedError when the run was already settled or released.
   */
  async hold(requestId: string, quote: Quote): Promise<HoldView> {
    validateRequestId(requestId);
    const held = await this.repo.findAccountByHold(requestId);
    const existing = held?.holds.find((hold) => hold.requestId === requestId);
    if (existing) return toHoldView(existing);

    const minutes = chargeMinutes(quote.durationSec);
    const outcome = await this.units.write(quote.userId, async ({ draft, session }) => {
      const open = findHold(draft, requestId);
      if (open) return open;
      const prior = await this.repo.findLedgerBySourceRef(requestId, session);
      if (prior.length > 0) throw new HoldFinalizedError();
      const decision = this.allocateFor(draft, minutes);
      if (!decision.ok) throw rejectionToError(decision);
      const { youtubeId, durationSec } = quote;
      const request = { requestId, userId: draft.original.ownerId, youtubeId, durationSec, minutes };
      return applyHold(draft, request, decision.split);
    });
    return toHoldView(outcome.value);
  }

  /** Turn the hold into debits. Idempotent: a second completion is `already_debited`. */
  async settle(requestId: string, options: CostOptions = {}): Promise<HoldOutcome> {
    const { costUsd } = options;
    validateRequestId(requestId);
    validateCost(costUsd);
    const settled = await this.units.writeByHold(requestId, async (unit) => {
      const hold = unit ? findHold(unit.draft, requestId) : undefined;
      if (!unit || !hold) return null;
      applySettle(unit.draft, hold, costUsd);
      return { requestId, status: 'debited' as const, minutes: hold.minutes };
    });
    return settled ?? this.finishedOutcome(requestId, 'settle');
  }

  /** Give the hold back to exactly the buckets and lots it came from. A no-op after a debit. */
  async release(requestId: string, reason: ReleaseReason, options: CostOptions = {}): Promise<HoldOutcome> {
    const { costUsd } = options;
    validateRequestId(requestId);
    validateCost(costUsd);
    const released = await this.units.writeByHold(requestId, async (unit) => {
      const hold = unit ? findHold(unit.draft, requestId) : undefined;
      if (!unit || !hold) return null;
      const missing = hold.split.lots.map((slice) => slice.lotId).filter((lotId) => !unit.draft.findLot(lotId));
      unit.draft.includeLots(await this.repo.findLotsByIds(missing, unit.session));
      applyRelease(unit.draft, hold, reason, costUsd);
      return { requestId, status: 'released' as const, minutes: hold.minutes };
    });
    return released ?? this.finishedOutcome(requestId, 'release');
  }

  /** Persist the plan-cycle roll (Pro: new monthly cycle, no rollover; ended term → free). */
  async rollPeriod(userId: string): Promise<AccountSnapshot> {
    const outcome = await this.units.write(userId, async ({ draft }) => draft.now);
    return toSnapshot(outcome.account, outcome.lots, outcome.value);
  }

  /** The user's minutes, as they should see them. */
  async summary(userId: string): Promise<UsageSummary> {
    const draft = await this.units.read(userId);
    return toUsageSummary(draft.account, draft.lots, draft.now);
  }

  /** Rebuild the account's projections from its ledger and list any drift. */
  async verifyAccount(userId: string): Promise<ProjectionCheck> {
    const draft = await this.units.read(userId);
    const stored = draft.original;
    const lots = await this.repo.listLots(stored._id);
    const entries = await this.repo.listAllLedger(stored._id);
    return checkProjection(stored, lots, entries, draft.now);
  }

  // ─── Admin (Contract 1) ───

  getAccountDetail(userId: string, opts?: AccountDetailOptions): Promise<AccountDetail> {
    return this.admin.getAccountDetail(userId, opts);
  }

  setPlan(
    userId: string,
    input: { planId: PlanId; periodEnd?: Date },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.setPlan(userId, input, ctx);
  }

  grantLot(
    userId: string,
    input: { minutes: number; expiresAt?: Date | null },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot> & { lotId: string }> {
    return this.admin.grantLot(userId, input, ctx);
  }

  setCaps(userId: string, caps: Partial<AccountCaps>, ctx: AdminMutationContext): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.setCaps(userId, caps, ctx);
  }

  adjust(
    userId: string,
    input: { bucket: 'plan' | 'credit'; minutes: number },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.adjust(userId, input, ctx);
  }

  createRecurringGrant(
    userId: string,
    input: { minutes: number; every: 'month'; active: boolean },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot> & { grantId: string }> {
    return this.admin.createRecurringGrant(userId, input, ctx);
  }

  deleteRecurringGrant(userId: string, grantId: string, ctx: AdminMutationContext): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.deleteRecurringGrant(userId, grantId, ctx);
  }

  // ─── Internals ───

  private allocateFor(draft: AccountDraft, minutes: number): AllocationResult {
    const { account, now } = draft;
    return allocate({
      minutes,
      now,
      recentFreeVideoAt: account.buckets.free.recentFreeVideoAt,
      planAvailable: planAvailable(account, now),
      lots: draft.lots.map(toAllocationLot),
      caps: account.caps,
      openHolds: account.holds.length,
    });
  }

  /** No open hold: say whether the run already finished, and log the cases worth a look. */
  private async finishedOutcome(requestId: string, action: 'settle' | 'release'): Promise<HoldOutcome> {
    const status = finishedStatus(await this.repo.findLedgerBySourceRef(requestId));
    // A second completion (translation) and a release after the debit are routine.
    const routine = action === 'settle' ? status === 'already_debited' : status !== 'no_hold';
    if (!routine) this.logger.warn({ requestId, action, status }, 'billing: no open hold for this run');
    return { requestId, status };
  }
}
