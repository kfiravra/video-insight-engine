import { FastifyBaseLogger } from 'fastify';
import { HoldConflictError, HoldFinalizedError, ValidationError } from '../../utils/errors.js';
import { allocate } from './allocation.js';
import { BillingAdmin } from './billing-admin.js';
import { rejectionToError } from './billing-errors.js';
import { BillingUnits } from './billing-unit.js';
import { applyHold, applyRelease, applySettle, findHold } from './hold-ops.js';
import { chargeMinutes } from './minutes.js';
import { planAvailable } from './plan-cycle.js';
import { applyLotExpiry, applyRecurringRun, lotExpiryKey, recurringRunAt } from './maintenance-ops.js';
import { checkProjection } from './projection.js';
import { StaleAccountVersionError } from '../../repositories/billing.repository.js';
import { toAllocationLot, toHoldView, toSnapshot, toUsageSummary } from './views.js';
import type { AccountCaps, AccountDetail, AccountSnapshot, HoldView, PlanId, UsageSummary } from '@vie/types';
import type { AccountDraft } from './account-draft.js';
import type { AllocationResult } from './allocation.js';
import type { AccountDetailOptions, AdminMutationContext, MutationResult } from './billing-admin.js';
import type { ReleaseReason } from './hold-ops.js';
import type { ProjectionCheck } from './projection.js';
import type { ObjectId } from 'mongodb';
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

/** A playlist priced as a whole, items decided in order as if held one after another. */
export interface PlaylistQuote {
  /** Σ minutes of every item. */
  neededMinutes: number;
  /** Paid minutes (plan + spendable credits) before the first item. */
  availableMinutes: number;
  freeVideoAvailable: boolean;
  nextFreeAt: string | null;
  decisions: AllocationResult[];
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
    private readonly userRepository: UserRepository,
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
   * Price several videos at once (a playlist): each is decided on the account
   * as the earlier ones would leave it, in memory only — nothing is held.
   */
  async quoteMany(userId: string, durationsSec: number[]): Promise<PlaylistQuote> {
    const draft = await this.units.read(userId);
    const before = toUsageSummary(draft.account, draft.lots, draft.now);
    const ownerId = draft.original.ownerId;
    let neededMinutes = 0;
    const decisions = durationsSec.map((durationSec, i) => {
      const minutes = chargeMinutes(durationSec);
      neededMinutes += minutes;
      const decision = this.allocateFor(draft, minutes);
      if (decision.ok) {
        applyHold(draft, { requestId: `quote-${i}`, userId: ownerId, youtubeId: null, durationSec, minutes }, decision.split);
      }
      return decision;
    });
    return {
      neededMinutes,
      availableMinutes: before.planMinutes.remaining + before.credits.available,
      freeVideoAvailable: before.free.available,
      nextFreeAt: before.free.nextFreeAt,
      decisions,
    };
  }

  /**
   * Take the quoted minutes out of the account for run `requestId`, deciding
   * again on the account's current state. One hold per requestId: a second
   * call returns the open hold. Throws InsufficientMinutesError,
   * VideoTooLongForPlanError, AccountBlockedError, TooManyRunsError, or
   * HoldFinalizedError when the run was already settled or released.
   */
  async hold(requestId: string, quote: Quote): Promise<HoldView> {
    const existing = await this.openHoldFor(requestId, quote);
    if (existing) return existing;

    const minutes = chargeMinutes(quote.durationSec);
    const outcome = await this.units.write(quote.userId, async ({ draft, session }) => {
      const open = findHold(draft, requestId);
      if (open && open.youtubeId !== quote.youtubeId) throw new HoldConflictError();
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

  /** The open hold of run `requestId`, if any. */
  async openHold(requestId: string): Promise<HoldView | null> {
    validateRequestId(requestId);
    const held = await this.repo.findAccountByHold(requestId);
    const hold = held?.holds.find((candidate) => candidate.requestId === requestId);
    return hold ? toHoldView(hold) : null;
  }

  /**
   * The open hold of run `requestId` when it is this user's hold for this
   * video; HoldConflictError when the id is holding for anyone or anything
   * else (W3) — a repeated id never rides on another run's minutes.
   */
  async openHoldFor(requestId: string, run: { userId: string; youtubeId: string }): Promise<HoldView | null> {
    const hold = await this.openHold(requestId);
    if (hold && (hold.userId !== run.userId || hold.youtubeId !== run.youtubeId)) throw new HoldConflictError();
    return hold;
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
    return this.verifyAccountById(draft.accountId);
  }

  /** Same, by account id, from one consistent snapshot (the repair job's drift check). */
  async verifyAccountById(accountId: ObjectId): Promise<ProjectionCheck> {
    return this.repo.withTransaction(async (session) => {
      const account = await this.repo.findAccountById(accountId, session);
      if (!account) return { ok: true, drift: [] };
      const lots = await this.repo.listLots(accountId, undefined, session);
      const entries = await this.repo.listAllLedger(accountId, session);
      return checkProjection(account, lots, entries, this.clock());
    });
  }

  // ─── Maintenance (repair job, P1.6) ───

  /** Expire what is left of the account's lots past their expiry (`expire` entries). Returns the minutes expired. */
  async expireLots(accountId: ObjectId): Promise<number> {
    const outcome = await this.units.writeAccount(accountId, async ({ draft, session }) => {
      const keys = draft.lots.map((lot) => lotExpiryKey(lot._id));
      const done = await this.repo.findLedgerByKeys(keys, session);
      return applyLotExpiry(draft, new Set(done.map((entry) => entry.idempotencyKey)));
    });
    return outcome.value;
  }

  /**
   * Grant this month's lot of a due recurring grant (expiring at its next
   * run plus `lotGraceMs`) and move the grant on, in one unit. Re-reads the
   * grant inside it, so a run another process already applied is a no-op.
   * Mints nothing for an account whose user is gone or being deleted (I7).
   * Returns the lot id.
   */
  async applyRecurringGrant(accountId: ObjectId, grantId: ObjectId, lotGraceMs = 0): Promise<string | null> {
    if (!(await this.recurringOwnerActive(accountId, grantId))) return null;
    const outcome = await this.units.writeAccount(accountId, async ({ draft, session }) => {
      const grant = await this.repo.findRecurringGrant(accountId, grantId, session);
      if (!grant || !grant.active || grant.deletedAt || grant.nextRunAt > draft.now) return null;
      const run = recurringRunAt(grant, draft.now, lotGraceMs);
      const [done] = await this.repo.findLedgerByKeys([run.idempotencyKey], session);
      const lotId = done ? null : applyRecurringRun(draft, grant, run);
      const next = { lastRunAt: run.runAt, nextRunAt: run.nextRunAt };
      if (!(await this.repo.advanceRecurringGrant(grantId, grant.nextRunAt, next, session))) {
        throw new StaleAccountVersionError('recurring grant changed concurrently');
      }
      return lotId ? lotId.toHexString() : null;
    });
    return outcome.value;
  }

  /**
   * A recurring grant only mints for a live owner. An erased user (no row)
   * retires the grant for good; a soft-deleted one pauses it, so a restore
   * resumes it. The billing collections join the GDPR cascade in P3.2.
   */
  private async recurringOwnerActive(accountId: ObjectId, grantId: ObjectId): Promise<boolean> {
    const account = await this.repo.findAccountById(accountId);
    if (!account) return false;
    const owner = await this.userRepository.findById(account.ownerId.toHexString());
    if (owner && !owner.deletedAt) return true;
    if (!owner) {
      await this.repo.softDeleteRecurringGrant(grantId, this.clock());
      this.logger.warn({ accountId: accountId.toHexString(), grantId: grantId.toHexString() }, 'recurring_grant_retired_owner_gone');
    }
    return false;
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
