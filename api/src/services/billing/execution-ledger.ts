import { AccountBlockedError, InsufficientTokensError } from '../../utils/errors.js';
import { balanceOf } from './allocation.js';
import { validateCost, validateRequestId } from './billing-validation.js';
import { applyExecutionCharge } from './hold-ops.js';
import { spendableState } from './spendable.js';
import { tokensForUsd } from './tokens.js';
import type { ClientSession } from 'mongodb';
import type { ExecutionKind, VideoBilling } from '@vie/types';
import type { BillingUnits } from './billing-unit.js';
import type { BillingRepository, LedgerEntryDocument } from '../../repositories/billing.repository.js';
import type { UserRepository } from '../../repositories/user.repository.js';

/** One chat turn, library chat or action, as the assistant reported its cost. */
export interface ExecutionCost {
  userId: string;
  /** The api's per-request id (`X-Execution-Id`); the debit's idempotency key. */
  executionId: string;
  kind: ExecutionKind;
  /** The request id on the assistant's llm_usage rows. */
  requestId: string;
  costUsd: number;
  /** Why it is debited outside the usage callback (`backfill`: the repair job). */
  reason?: string;
}

export interface ExecutionOutcome {
  executionId: string;
  status: 'debited' | 'already_debited' | 'free';
  tokens: number;
}

/** An assistant request the usage callback never reported, as its llm_usage rows add up. */
export interface BackfillRequest {
  userId: string;
  requestId: string;
  kind: ExecutionKind;
  costUsd: number;
  /** The request's newest llm_usage row. */
  lastAt: Date;
}

/** The repair backfill names its execution after the request id. */
const BACKFILL_PREFIX = 'req:';

function chargedBy(entries: LedgerEntryDocument[]): number {
  return entries.reduce((sum, entry) => sum - (entry.tokens ?? 0), 0);
}

/**
 * Chat turns, library chats and actions on the token ledger: admitted on a
 * positive balance, debited their real cost once afterwards (the assistant's
 * usage callback, or the repair job's backfill when the callback never came),
 * plus the per-video `billing` view. Writes go through the BillingService's
 * units, so every change keeps the account equal to its ledger.
 */
export class ExecutionLedger {
  constructor(
    private readonly repo: BillingRepository,
    private readonly units: BillingUnits,
    private readonly userRepository: UserRepository,
  ) {}

  /**
   * May the user start a chat turn / action? Blocked → AccountBlockedError;
   * nothing left to spend (balance ≤ 0, debt included) → InsufficientTokensError.
   * No hold: the turn is debited its real cost afterwards. Returns the balance.
   */
  async admit(userId: string): Promise<number> {
    const draft = await this.units.read(userId);
    if (draft.account.caps.blocked) throw new AccountBlockedError();
    const balance = balanceOf(spendableState(draft));
    if (balance <= 0) throw new InsufficientTokensError(1, balance);
    return balance;
  }

  /**
   * Debit an execution's real cost: plan, then lots FIFO, then debt (the turn
   * already happened). Once per request, whichever of the usage callback and
   * the backfill lands first: both check, inside the same transaction, the
   * other's entries. A free execution writes nothing.
   */
  async debit(cost: ExecutionCost): Promise<ExecutionOutcome> {
    const { userId, executionId, kind, requestId, costUsd, reason } = cost;
    validateRequestId(executionId, 'executionId');
    validateRequestId(requestId);
    validateCost(costUsd);
    const tokens = tokensForUsd(costUsd);
    if (tokens === 0) return { executionId, status: 'free', tokens: 0 };
    const outcome = await this.units.write(userId, async ({ draft, session }) => {
      const prior = await this.priorDebits(executionId, requestId, session);
      if (prior.length > 0) return { executionId, status: 'already_debited' as const, tokens: chargedBy(prior) };
      const ownerId = draft.original.ownerId;
      applyExecutionCharge(draft, { executionId, kind, requestId, userId: ownerId, tokens, costUsd, reason });
      return { executionId, status: 'debited' as const, tokens };
    });
    return outcome.value;
  }

  /**
   * The repair job's backfill of an assistant request whose usage callback
   * never arrived: debit its llm_usage total once (`debit:exec:req:<requestId>`).
   * Only a request made after its account entered the token ledger (the
   * signup grant's date: account creation, or the token migration) — older
   * chats were free under minutes. Never for a missing account or a user who
   * is gone. Returns whether it debited.
   */
  async backfill(request: BackfillRequest): Promise<boolean> {
    const { userId, requestId, kind, costUsd, lastAt } = request;
    const account = await this.repo.findAccount('user', userId);
    if (!account?.signupGrantAt || lastAt.getTime() < account.signupGrantAt.getTime()) return false;
    const owner = await this.userRepository.findById(userId);
    if (!owner || owner.deletedAt) return false;
    const executionId = `${BACKFILL_PREFIX}${requestId}`;
    const outcome = await this.debit({ userId, executionId, kind, requestId, costUsd, reason: 'backfill' });
    return outcome.status === 'debited';
  }

  /**
   * What `userId`'s run `runId` (a cache row's billing stamp) holds or cost:
   * held while it runs, charged once settled, released when given back, free
   * when this user has no hold on that run (cached, attached, someone else's).
   */
  async videoBilling(runId: string | null, userId: string): Promise<VideoBilling> {
    const free: VideoBilling = { state: 'free', estimatedTokens: 0, chargedTokens: 0 };
    if (!runId) return free;
    const entries = (await this.repo.findLedgerBySourceRef(runId)).filter(
      (entry) => entry.unit === 'tokens' && !entry.executionId && entry.userId.toHexString() === userId,
    );
    const sum = (type: string): number => chargedBy(entries.filter((entry) => entry.type === type));
    const estimatedTokens = sum('hold');
    if (estimatedTokens === 0) return free;
    if (entries.some((entry) => entry.type === 'debit')) {
      return { state: 'charged', estimatedTokens, chargedTokens: sum('debit') };
    }
    if (entries.some((entry) => entry.type === 'release')) return { state: 'released', estimatedTokens, chargedTokens: 0 };
    return { state: 'held', estimatedTokens, chargedTokens: null };
  }

  /**
   * The debits already written for this request: its own key, and the other
   * path's — the backfill's `req:<requestId>` for a callback, the callback's
   * entries on the same request for a backfill.
   */
  private async priorDebits(executionId: string, requestId: string, session: ClientSession): Promise<LedgerEntryDocument[]> {
    const own = await this.repo.findExecutionDebits(executionId, session);
    if (own.length > 0) return own;
    if (!executionId.startsWith(BACKFILL_PREFIX)) {
      return this.repo.findExecutionDebits(`${BACKFILL_PREFIX}${requestId}`, session);
    }
    const sameRequest = await this.repo.findLedgerBySourceRef(requestId, session);
    return sameRequest.filter((entry) => entry.type === 'debit' && entry.executionId !== undefined);
  }
}
