import { createHash, randomUUID } from 'node:crypto';
import type { Db } from 'mongodb';
import { ObjectId } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';

import type { UserRepository, UserDocument } from '../repositories/user.repository.js';
import type {
  UserDeletionRepository,
  UserDeletionDocument,
  DeletionCounts,
  DeletionInitiator,
} from '../repositories/user-deletion.repository.js';
import { config } from '../config.js';
import {
  AccountAlreadyDeletedError,
  DemoRestrictedError,
  UserNotFoundError,
} from '../utils/errors.js';
import * as softDeleteCache from '../utils/soft-delete-cache.js';

/**
 * Collections that are user-scoped and safe for `deleteMany({ userId })`.
 *
 * Narrowed to a union (rather than `string`) so a typo or a future call site
 * can't accidentally aim a `deleteMany` at a shared collection like `users`
 * or `videoSummaryCache` — see `reports/user-data-inventory.md`.
 */
type UserScopedCollection =
  | 'userVideos'
  | 'folders'
  | 'userCosts'
  | 'userCostAdjustments'
  | 'idempotencyKeys';

/**
 * GDPR Article 17 cascade deletion service.
 *
 * **Soft delete** (`requestDeletion`) marks `users.deletedAt` and schedules a
 * hard delete in `graceDays` days. Auth middleware rejects login while the
 * mark is set so the account stops accepting traffic immediately.
 *
 * **Hard delete** (`executeHardDelete`) runs a best-effort saga across every
 * user-scoped data store. Each step is independently idempotent (delete-many
 * by `userId`), so a partial failure can be retried by re-invoking the saga
 * without compensating actions. Failures inside a step are captured as
 * `warnings` on the audit row rather than aborting the whole cascade — the
 * goal is to leave the smallest possible PII footprint even when a downstream
 * store is misbehaving.
 *
 * **Scheduler** (`runScheduledDeletions`) is the long-running daemon that
 * sweeps `users` where `hardDeleteAt <= now()` and `legalHold != true` and
 * triggers the cascade. It returns aggregate counts so a cron driver can log
 * a single line per run.
 *
 * **What is NOT deleted** (documented in `reports/user-data-inventory.md`):
 *   - `videoSummaryCache` (shared cache across users)
 *   - Qdrant `transcript_chunks` (shared, video-scoped)
 *   - S3 objects (shared, video-scoped — no user prefix today)
 *   - `llm_usage` (90-day TTL, anonymized cost reporting)
 */

const DEFAULT_GRACE_DAYS = 30;
const SCHEDULER_BATCH_LIMIT = 50;

export interface RequestDeletionOptions {
  graceDays?: number;
}

export interface RequestDeletionResult {
  userId: string;
  scheduledHardDeleteAt: Date;
  graceDays: number;
}

export interface ExecuteHardDeleteContext {
  initiatedBy: DeletionInitiator;
  adminId?: string | null;
  reason?: string | null;
}

export interface RunScheduledResult {
  processed: number;
  succeeded: number;
  failed: number;
  failures: Array<{ userId: string; error: string }>;
}

export class UserDeletionService {
  constructor(
    private readonly db: Db,
    private readonly userRepository: UserRepository,
    private readonly userDeletionRepository: UserDeletionRepository,
    private readonly logger: FastifyBaseLogger,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  // ─── Phase 2: soft delete ───────────────────────────────────────────────

  async requestDeletion(
    userId: string,
    options: RequestDeletionOptions = {},
  ): Promise<RequestDeletionResult> {
    const { graceDays = DEFAULT_GRACE_DAYS } = options;
    if (graceDays < 0 || !Number.isFinite(graceDays)) {
      throw new Error(`Invalid graceDays: ${graceDays}`);
    }

    const user = await this.userRepository.findById(userId);
    if (!user) {
      throw new UserNotFoundError();
    }
    if (user.deletedAt) {
      throw new AccountAlreadyDeletedError();
    }
    // Every demo visitor holds a token for this one account, so a soft delete
    // by any of them would lock the demo for all. Admin immediate deletion
    // (`executeHardDelete`) stays available.
    if (config.DEMO_USER_EMAIL && user.email === config.DEMO_USER_EMAIL) {
      throw new DemoRestrictedError();
    }

    const now = this.clock();
    const hardDeleteAt = new Date(now.getTime() + graceDays * 24 * 60 * 60 * 1000);
    const updated = await this.userRepository.markSoftDeleted(userId, hardDeleteAt, now);
    if (!updated) {
      // Someone else flipped the flag between our read and our write — surface
      // the conflict rather than silently overwriting their timestamp.
      throw new AccountAlreadyDeletedError();
    }

    // Drop the cached "active" verdict on this instance so the next request
    // sees the 403 immediately. Other instances catch up within the cache TTL.
    softDeleteCache.invalidate(userId);

    this.logger.info(
      { userId, hardDeleteAt: hardDeleteAt.toISOString(), graceDays },
      'user_deletion_requested',
    );

    return { userId, scheduledHardDeleteAt: hardDeleteAt, graceDays };
  }

  async cancelDeletion(userId: string): Promise<UserDocument> {
    const user = await this.userRepository.findById(userId);
    if (!user) {
      throw new UserNotFoundError();
    }
    if (!user.deletedAt) {
      // Already active — no-op, return current state.
      return user;
    }
    const restored = await this.userRepository.clearSoftDelete(userId);
    if (!restored) {
      throw new UserNotFoundError();
    }
    softDeleteCache.invalidate(userId);
    this.logger.info({ userId }, 'user_deletion_cancelled');
    return restored;
  }

  // ─── Phase 3: hard delete saga ──────────────────────────────────────────

  async executeHardDelete(
    userId: string,
    ctx: ExecuteHardDeleteContext,
  ): Promise<UserDeletionDocument> {
    const user = await this.userRepository.findById(userId);
    if (!user) {
      throw new UserNotFoundError();
    }

    const startedAt = this.clock();
    const traceId = randomUUID();
    const counts: DeletionCounts = {
      userVideos: 0,
      folders: 0,
      userCosts: 0,
      userCostAdjustments: 0,
      idempotencyKeys: 0,
      assistantNotes: 0,
      qdrantPoints: 0,
      s3Objects: 0,
    };
    const warnings: string[] = [];

    this.logger.info(
      { traceId, userId, initiatedBy: ctx.initiatedBy, adminId: ctx.adminId ?? null },
      'user_hard_delete_started',
    );

    // Each step swallows its own errors so the cascade always reaches the
    // `users` row deletion. Per-step failure → warnings entry, not an abort.
    await this.runStep('assistantNotes', traceId, async () => {
      counts.assistantNotes = await this.deleteAssistantNotes(userId);
    }, warnings);

    await this.runStep('userVideos', traceId, async () => {
      counts.userVideos = await this.deleteByUserIdObjectId('userVideos', userId);
    }, warnings);

    await this.runStep('folders', traceId, async () => {
      counts.folders = await this.deleteByUserIdObjectId('folders', userId);
    }, warnings);

    await this.runStep('userCosts', traceId, async () => {
      counts.userCosts = await this.deleteByUserIdObjectId('userCosts', userId);
    }, warnings);

    await this.runStep('userCostAdjustments', traceId, async () => {
      counts.userCostAdjustments = await this.deleteByUserIdObjectId(
        'userCostAdjustments',
        userId,
      );
    }, warnings);

    await this.runStep('idempotencyKeys', traceId, async () => {
      counts.idempotencyKeys = await this.deleteByUserIdObjectId(
        'idempotencyKeys',
        userId,
      );
    }, warnings);

    // Qdrant + S3 placeholders — no-ops today, see inventory report. Wired so
    // a future user-keyed payload slots in without changing call sites.
    await this.runStep('qdrant', traceId, async () => {
      counts.qdrantPoints = await this.deleteQdrantUserContent(userId);
    }, warnings);

    await this.runStep('s3', traceId, async () => {
      counts.s3Objects = await this.deleteS3UserObjects(userId);
    }, warnings);

    // Finally, drop the user doc itself. This is the FK anchor — if every
    // other step succeeded but this fails, a retry of the saga is still safe
    // (every prior step is a no-op because rows are gone).
    const removed = await this.userRepository.hardDelete(userId);
    if (!removed) {
      warnings.push('user_doc_not_found_at_final_delete');
    }

    const completedAt = this.clock();

    const audit = await this.userDeletionRepository.insert({
      originalUserId: userId,
      emailHash: hashEmail(user.email),
      initiatedBy: ctx.initiatedBy,
      adminId: ctx.adminId ?? null,
      reason: ctx.reason ?? null,
      counts,
      startedAt,
      completedAt,
      warnings,
    });

    // Escalate to error when any saga step failed so paging/alerting surfaces
    // partial-failure cascades; clean runs stay at info to avoid alert noise.
    const terminalLog = {
      traceId,
      userId,
      durationMs: audit.durationMs,
      counts,
      warnings: warnings.length,
    };
    if (warnings.length > 0) {
      this.logger.error(terminalLog, 'user_hard_delete_completed_with_warnings');
    } else {
      this.logger.info(terminalLog, 'user_hard_delete_completed');
    }

    // The user is gone — drop any cached "active" verdict so a leftover JWT
    // (TTL ≤ 15m) starts being rejected by the auth hook on the next request.
    softDeleteCache.invalidate(userId);

    return audit;
  }

  // ─── Phase 4: scheduler ─────────────────────────────────────────────────

  async runScheduledDeletions(
    now: Date = this.clock(),
    limit: number = SCHEDULER_BATCH_LIMIT,
  ): Promise<RunScheduledResult> {
    const candidates = await this.userRepository.findExpiredSoftDeletes(now, limit);
    const failures: Array<{ userId: string; error: string }> = [];
    let succeeded = 0;

    for (const candidate of candidates) {
      const userId = candidate._id.toString();
      try {
        await this.executeHardDelete(userId, { initiatedBy: 'scheduler' });
        succeeded++;
      } catch (err) {
        // Log the full error for triage; expose only an opaque code in the
        // response so raw driver text (which can include connection strings
        // or query fragments) never reaches the API surface, even on the
        // internal-secret-gated endpoint.
        this.logger.error({ userId, err }, 'scheduled_deletion_failed');
        failures.push({ userId, error: 'cascade_failed' });
      }
    }

    if (candidates.length > 0) {
      this.logger.info(
        { processed: candidates.length, succeeded, failed: failures.length },
        'scheduled_deletion_batch_complete',
      );
    }

    return {
      processed: candidates.length,
      succeeded,
      failed: failures.length,
      failures,
    };
  }

  // ─── private helpers ────────────────────────────────────────────────────

  private async runStep(
    step: string,
    traceId: string,
    op: () => Promise<void>,
    warnings: string[],
  ): Promise<void> {
    try {
      await op();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      warnings.push(`${step}: ${message}`);
      this.logger.warn(
        { traceId, step, err: message },
        'user_hard_delete_step_failed',
      );
    }
  }

  /**
   * Delete documents from a collection by `userId` field, accepting that the
   * stored value is an ObjectId (the project-wide convention). Returns the
   * deleted-row count.
   *
   * `collectionName` is constrained to the `UserScopedCollection` union so a
   * typo can't aim a `deleteMany` at a shared collection.
   */
  private async deleteByUserIdObjectId(
    collectionName: UserScopedCollection,
    userId: string,
  ): Promise<number> {
    const oid = new ObjectId(userId);
    const result = await this.db.collection(collectionName).deleteMany({ userId: oid });
    return result.deletedCount ?? 0;
  }

  /**
   * Assistant notes (`agentNotes`) store `userId` as a string (see
   * services/assistant/src/repositories/notes_repository.py:55). Both ID
   * forms are accepted for forward compatibility if the column ever moves to
   * an ObjectId.
   */
  private async deleteAssistantNotes(userId: string): Promise<number> {
    const filters: Array<Record<string, unknown>> = [{ userId }];
    if (ObjectId.isValid(userId)) {
      filters.push({ userId: new ObjectId(userId) });
    }
    const result = await this.db.collection('agentNotes').deleteMany({ $or: filters });
    return result.deletedCount ?? 0;
  }

  /**
   * Reserved for the future user-keyed Qdrant payload. Today Qdrant chunks
   * are video-scoped (shared across users), so user-only deletion is a no-op
   * — see the upstream `rag-output-chunker-verification` task.
   */
  private async deleteQdrantUserContent(_userId: string): Promise<number> {
    // No user-keyed Qdrant payload yet. Returning 0 keeps the audit honest.
    return 0;
  }

  /**
   * Reserved for the future user-keyed S3 prefix. Today S3 keys are
   * `videos/{youtubeId}/...` (shared), so user-only deletion is a no-op.
   */
  private async deleteS3UserObjects(_userId: string): Promise<number> {
    return 0;
  }
}

/** SHA-256 of the lowercased email — proves deletion without storing PII. */
export function hashEmail(email: string): string {
  return createHash('sha256').update(email.trim().toLowerCase()).digest('hex');
}
