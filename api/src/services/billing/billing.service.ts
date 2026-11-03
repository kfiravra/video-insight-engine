import { FastifyBaseLogger } from 'fastify';
import {
  AccountBlockedError,
  HoldConflictError,
  HoldFinalizedError,
  InsufficientTokensError,
  ValidationError,
} from '../../utils/errors.js';
import { allocate, balanceOf } from './allocation.js';
import { BillingAdmin } from './billing-admin.js';
import { rejectionToError } from './billing-errors.js';
import { BillingUnits } from './billing-unit.js';
import {
  applyExecutionCharge,
  applyHold,
  applyRelease,
  applySettleActual,
  findHold,
  spendableState,
} from './hold-ops.js';
import { applyLotExpiry, applyRecurringRun, lotExpiryKey, recurringRunAt } from './maintenance-ops.js';
import { checkProjection } from './projection.js';
import { estimateTokens, tokensForUsd, videoMinutes } from './tokens.js';
import { StaleAccountVersionError } from '../../repositories/billing.repository.js';
import { toHoldView, toSnapshot, toUsageSummary } from './views.js';
import type { AccountCaps, AccountDetail, AccountSnapshot, ExecutionKind, HoldView, PlanId, UsageSummary } from '@vie/types';
import type { AccountDraft } from './account-draft.js';
import type { AllocationResult } from './allocation.js';
import type { AccountDetailOptions, AdminMutationContext, MutationResult } from './billing-admin.js';
import type { ReleaseReason } from './hold-ops.js';
import type { ProjectionCheck } from './projection.js';
import type { ClientSession, ObjectId } from 'mongodb';
import type { BillingRepository } from '../../repositories/billing.repository.js';
import type { UserRepository } from '../../repositories/user.repository.js';

export type { AccountDetailOptions, AdminMutationContext, MutationResult } from './billing-admin.js';
export type { ReleaseReason } from './hold-ops.js';

/** A priced, not-yet-held decision for one video. Holding re-decides on fresh state. */
export interface Quote {
  userId: string;
  youtubeId: string;
  durationSec: number;
  /** The estimate the run would hold. */
  tokens: number;
  decision: AllocationResult;
  quotedAt: Date;
}

/** A playlist priced as a whole, items decided in order as if held one after another. */
export interface PlaylistQuote {
  /** Σ estimates of every item. */
  neededTokens: number;
  /** The balance before the first item (negative while in debt). */
  availableTokens: number;
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
  /** Tokens debited (settle) or released (release) by this call. */
  tokens?: number;
  /** Settle: the unused estimate given back. */
  released?: number;
  /** Settle: what the run cost beyond everything the account had. */
  debt?: number;
}

/** One chat turn, library chat or action, as the assistant reported its cost. */
export interface ExecutionCost {
  userId: string;
  /** The api's per-request id (`X-Execution-Id`); the debit's idempotency key. */
  executionId: string;
  kind: ExecutionKind;
  /** The request id on the assistant's llm_usage rows. */
  requestId: string;
  costUsd: number;
}

export interface ExecutionOutcome {
  executionId: string;
  status: 'debited' | 'already_debited' | 'free';
  tokens: number;
}

const MAX_REQUEST_ID_LENGTH = 200;

function validateRequestId(requestId: string, label = 'requestId'): void {
  if (requestId.length === 0 || requestId.length > MAX_REQUEST_ID_LENGTH) {
    throw new ValidationError(`${label} must be 1–${MAX_REQUEST_ID_LENGTH} characters`);
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
 * The token ledger. A video holds its estimate at submit (quote → hold) and
 * settles on the run's real LLM cost (settleActual: the unused estimate goes
 * back, an overrun is taken from what is left and then becomes debt), or is
 * released in full on failure or when the video was served without a run
 * (cached / attached). A chat turn, library chat or action is admitted on a
 * positive balance and debited its real cost afterwards (debitExecution).
 * Every change is one transaction over the account, its lots and append-only
 * ledger entries; the account's buckets and the lots' balances are
 * projections that always equal Σ ledger (`verifyAccount`). A new account
 * starts with its signup grant. The admin API (Contract 1) is delegated to
 * BillingAdmin.
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
    this.units = new BillingUnits(repo, clock, async (ownerId) => {
      const owner = await userRepository.findById(ownerId);
      return owner !== null && !owner.deletedAt;
    });
    this.admin = new BillingAdmin(repo, this.units, userRepository);
  }

  // ─── Accounts ───

  /** Create the account with its signup grant if the user has none (registration). Idempotent. */
  async ensureAccount(userId: string): Promise<void> {
    await this.units.ensureAccount(userId);
  }

  // ─── Runs ───

  /** Price a video (its estimate) and decide where the tokens would come from, without holding anything. */
  async quote(userId: string, youtubeId: string, durationSec: number): Promise<Quote> {
    const tokens = estimateTokens(durationSec);
    const draft = await this.units.read(userId);
    const decision = this.allocateFor(draft, tokens, durationSec);
    return { userId, youtubeId, durationSec, tokens, decision, quotedAt: draft.now };
  }

  /**
   * Price several videos at once (a playlist): each is decided on the account
   * as the earlier ones would leave it, in memory only — nothing is held.
   */
  async quoteMany(userId: string, durationsSec: number[]): Promise<PlaylistQuote> {
    const draft = await this.units.read(userId);
    const availableTokens = balanceOf(spendableState(draft));
    const ownerId = draft.original.ownerId;
    let neededTokens = 0;
    const decisions = durationsSec.map((durationSec, i) => {
      const tokens = estimateTokens(durationSec);
      neededTokens += tokens;
      const decision = this.allocateFor(draft, tokens, durationSec);
      if (decision.ok) {
        applyHold(draft, { requestId: `quote-${i}`, userId: ownerId, youtubeId: null, durationSec, tokens }, decision.split);
      }
      return decision;
    });
    return { neededTokens, availableTokens, decisions };
  }

  /**
   * Take the quoted estimate out of the account for run `requestId`,
   * deciding again on the account's current state. One hold per requestId: a
   * second call returns the open hold. Throws InsufficientTokensError,
   * VideoTooLongError, AccountBlockedError, TooManyRunsError, or
   * HoldFinalizedError when the run was already settled or released.
   */
  async hold(requestId: string, quote: Quote): Promise<HoldView> {
    const existing = await this.openHoldFor(requestId, quote);
    if (existing) return existing;

    const tokens = estimateTokens(quote.durationSec);
    const outcome = await this.units.write(quote.userId, async ({ draft, session }) => {
      const open = findHold(draft, requestId);
      if (open && open.youtubeId !== quote.youtubeId) throw new HoldConflictError();
      if (open) return open;
      const prior = await this.repo.findLedgerBySourceRef(requestId, session);
      if (prior.length > 0) throw new HoldFinalizedError();
      const decision = this.allocateFor(draft, tokens, quote.durationSec);
      if (!decision.ok) throw rejectionToError(decision);
      const { youtubeId, durationSec } = quote;
      const request = { requestId, userId: draft.original.ownerId, youtubeId, durationSec, tokens };
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
   * else (W3) — a repeated id never rides on another run's tokens.
   */
  async openHoldFor(requestId: string, run: { userId: string; youtubeId: string }): Promise<HoldView | null> {
    const hold = await this.openHold(requestId);
    if (hold && (hold.userId !== run.userId || hold.youtubeId !== run.youtubeId)) throw new HoldConflictError();
    return hold;
  }

  /**
   * Settle run `requestId` on its real LLM cost: debit `tokensForUsd(costUsd)`
   * — the unused estimate goes back, an overrun is taken from what is left and
   * then becomes debt. Idempotent: a second call is `already_debited`.
   */
  async settleActual(requestId: string, costUsd: number): Promise<HoldOutcome> {
    validateRequestId(requestId);
    validateCost(costUsd);
    const tokens = tokensForUsd(costUsd);
    const settled = await this.units.writeByHold(requestId, async (unit) => {
      const hold = unit ? findHold(unit.draft, requestId) : undefined;
      if (!unit || !hold) return null;
      // A minute hold from before the token migration holds 0 tokens: it was
      // paid in minutes, so it is released, never charged as an overrun.
      if (hold.tokens === 0) {
        applyRelease(unit.draft, hold, 'stale', costUsd);
        return { requestId, status: 'released' as const, tokens: 0 };
      }
      await this.includeHoldLots(unit.draft, hold.split.lots.map((slice) => slice.lotId), unit.session);
      const result = applySettleActual(unit.draft, hold, tokens, costUsd);
      return { requestId, status: 'debited' as const, tokens: result.charged, released: result.released, debt: result.debt };
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
      await this.includeHoldLots(unit.draft, hold.split.lots.map((slice) => slice.lotId), unit.session);
      const tokens = applyRelease(unit.draft, hold, reason, costUsd);
      return { requestId, status: 'released' as const, tokens };
    });
    return released ?? this.finishedOutcome(requestId, 'release');
  }

  // ─── Executions (chat, library chat, actions) ───

  /**
   * May the user start a chat turn / action? Blocked → AccountBlockedError;
   * nothing left to spend (balance ≤ 0, debt included) → InsufficientTokensError.
   * No hold: the turn is debited its real cost afterwards. Returns the balance.
   */
  async admitExecution(userId: string): Promise<number> {
    const draft = await this.units.read(userId);
    if (draft.account.caps.blocked) throw new AccountBlockedError();
    const balance = balanceOf(spendableState(draft));
    if (balance <= 0) throw new InsufficientTokensError(1, balance);
    return balance;
  }

  /**
   * Debit an execution's real cost: plan, then lots FIFO, then debt (the turn
   * already happened). Idempotent on `executionId`; a free execution writes
   * nothing.
   */
  async debitExecution(cost: ExecutionCost): Promise<ExecutionOutcome> {
    const { userId, executionId, kind, requestId, costUsd } = cost;
    validateRequestId(executionId, 'executionId');
    validateRequestId(requestId);
    validateCost(costUsd);
    const tokens = tokensForUsd(costUsd);
    if (tokens === 0) return { executionId, status: 'free', tokens: 0 };
    const outcome = await this.units.write(userId, async ({ draft, session }) => {
      const prior = await this.repo.findExecutionDebits(executionId, session);
      if (prior.length > 0) {
        const charged = prior.reduce((sum, entry) => sum - (entry.tokens ?? 0), 0);
        return { executionId, status: 'already_debited' as const, tokens: charged };
      }
      const ownerId = draft.original.ownerId;
      applyExecutionCharge(draft, { executionId, kind, requestId, userId: ownerId, tokens, costUsd });
      return { executionId, status: 'debited' as const, tokens };
    });
    return outcome.value;
  }

  /** Persist the plan-cycle roll (Pro: new monthly cycle, no rollover; ended term → free). */
  async rollPeriod(userId: string): Promise<AccountSnapshot> {
    const outcome = await this.units.write(userId, async ({ draft }) => draft.now);
    return toSnapshot(outcome.account, outcome.lots, outcome.value);
  }

  /** The user's tokens, as they should see them. */
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
      return checkProjection(account, lots, entries);
    });
  }

  // ─── Maintenance (repair job, P1.6) ───

  /** Expire what is left of the account's lots past their expiry (`expire` entries). Returns the tokens expired. */
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
   * resumes it. The GDPR cascade retires an erased account's grants itself
   * (BillingErasureRepository); this covers an owner gone any other way.
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
    input: { tokens: number; expiresAt?: Date | null },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot> & { lotId: string }> {
    return this.admin.grantLot(userId, input, ctx);
  }

  setCaps(userId: string, caps: Partial<AccountCaps>, ctx: AdminMutationContext): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.setCaps(userId, caps, ctx);
  }

  adjust(
    userId: string,
    input: { bucket: 'plan' | 'credit'; tokens: number },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.adjust(userId, input, ctx);
  }

  createRecurringGrant(
    userId: string,
    input: { tokens: number; every: 'month'; active: boolean },
    ctx: AdminMutationContext,
  ): Promise<MutationResult<AccountSnapshot> & { grantId: string }> {
    return this.admin.createRecurringGrant(userId, input, ctx);
  }

  deleteRecurringGrant(userId: string, grantId: string, ctx: AdminMutationContext): Promise<MutationResult<AccountSnapshot>> {
    return this.admin.deleteRecurringGrant(userId, grantId, ctx);
  }

  // ─── Internals ───

  private allocateFor(draft: AccountDraft, tokens: number, durationSec: number): AllocationResult {
    const { account } = draft;
    return allocate({
      ...spendableState(draft),
      tokens,
      videoMinutes: videoMinutes(durationSec),
      caps: account.caps,
      openHolds: account.holds.length,
    });
  }

  /** Bring a hold's lots that are outside the open set (spent, expired) into the unit, so a release can refill them. */
  private async includeHoldLots(draft: AccountDraft, lotIds: ObjectId[], session: ClientSession): Promise<void> {
    const missing = lotIds.filter((lotId) => !draft.findLot(lotId));
    draft.includeLots(await this.repo.findLotsByIds(missing, session));
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
