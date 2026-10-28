import type { FastifyBaseLogger } from 'fastify';
import type { AdminAuditTargetType, AdminAuditView } from '@vie/types';

import type {
  AdminAuditCursor,
  AdminAuditRepository,
  AdminAuditDocument,
} from '../repositories/admin-audit.repository.js';
import { ValidationError } from '../utils/errors.js';

export interface RecordAuditInput {
  /** Pre-generated ObjectId hex, when a ledger row already references the audit row. */
  auditId?: string;
  /** `users._id` hex of the acting admin, or `system:<name>`. */
  actorId: string;
  /** Stored in the row only; never logged. */
  actorEmail?: string;
  /** Dotted verb: `users.delete`, `videos.purge`, `queue.replay`, `roles.seed.promote`, … */
  action: string;
  targetType: AdminAuditTargetType;
  targetId: string;
  /** Small plain-JSON snapshots. Never put secrets, credentials or emails in them. */
  before?: unknown;
  after?: unknown;
  reason?: string;
  requestId?: string;
  ip?: string;
  /** sha256 of the request, so a repeated Idempotency-Key with a different request can be told apart. */
  requestHash?: string;
}

export type { AdminAuditCursor };

export interface ListAuditFilter {
  targetId?: string;
  actorId?: string;
  limit?: number;
  /** Cursor: a bare instant lists rows created strictly before it; `{ createdAt, id }` continues exactly after that row. */
  before?: Date | AdminAuditCursor;
}

const DEFAULT_LIST_LIMIT = 50;
const MAX_LIST_LIMIT = 200;
const OBJECT_ID_HEX = /^[a-f0-9]{24}$/i;

/**
 * The admin audit trail: one append-only `adminAudit` row per admin mutation.
 *
 * `record` throws when the insert fails. Route handlers call
 * `recordBestEffort` instead: the mutation has already happened by then, so a
 * failed audit write is logged loudly (with the audit id) rather than turned
 * into a failed request.
 */
export class AuditService {
  constructor(
    private readonly auditRepository: AdminAuditRepository,
    private readonly logger: FastifyBaseLogger,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  /**
   * Insert one audit row and return its id (the `auditId` when one was
   * supplied). A supplied `auditId` that already has a row is a retried action
   * the ledger has already deduplicated by the same id: nothing is written and
   * the id is returned.
   */
  async record(input: RecordAuditInput): Promise<string> {
    if (input.auditId !== undefined && !OBJECT_ID_HEX.test(input.auditId)) {
      throw new ValidationError('auditId must be a 24-char hex ObjectId');
    }
    const { auditId, before, after, ...rest } = input;
    let doc: AdminAuditDocument;
    try {
      doc = await this.auditRepository.insert({
        ...rest,
        auditId,
        before: toPlainJson(before),
        after: toPlainJson(after),
        createdAt: this.clock(),
      });
    } catch (err) {
      if (auditId === undefined || !isDuplicateKey(err)) throw err;
      this.logger.info({ auditId, action: input.action }, 'admin_audit_already_recorded');
      return auditId.toLowerCase();
    }
    const id = doc._id.toString();
    this.logger.info(
      { auditId: id, action: input.action, targetType: input.targetType, targetId: input.targetId, actorId: input.actorId },
      'admin_audit_recorded',
    );
    return id;
  }

  /**
   * `record` for callers whose mutation already succeeded: a failure is logged
   * at error level (never silently dropped) and returns null.
   */
  async recordBestEffort(input: RecordAuditInput): Promise<string | null> {
    try {
      return await this.record(input);
    } catch (err) {
      this.logger.error(
        {
          err,
          auditId: input.auditId ?? null,
          action: input.action,
          targetType: input.targetType,
          targetId: input.targetId,
        },
        'admin_audit_write_failed',
      );
      return null;
    }
  }

  /** The request hash recorded under `auditId`, or null when no row exists or it predates the field. */
  findRequestHash(auditId: string): Promise<string | null> {
    return this.auditRepository.findRequestHash(auditId);
  }

  /** Newest first. Filters combine with AND; `limit` defaults to 50 and is capped at 200. */
  async list(filter: ListAuditFilter = {}): Promise<AdminAuditView[]> {
    const requested =
      filter.limit !== undefined && Number.isFinite(filter.limit) ? Math.trunc(filter.limit) : DEFAULT_LIST_LIMIT;
    const limit = Math.min(Math.max(requested, 1), MAX_LIST_LIMIT);
    const rows = await this.auditRepository.find({
      targetId: filter.targetId,
      actorId: filter.actorId,
      before: filter.before instanceof Date ? { createdAt: filter.before } : filter.before,
      limit,
    });
    return rows.map(toView);
  }
}

function isDuplicateKey(err: unknown): boolean {
  return (err as { code?: number }).code === 11000;
}

/** Round-trips through JSON so ObjectIds and Dates are stored as the strings the view returns. */
function toPlainJson(value: unknown): unknown {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function toView(doc: AdminAuditDocument): AdminAuditView {
  const { _id, createdAt, requestHash: _requestHash, ...rest } = doc;
  return { ...rest, id: _id.toString(), createdAt: createdAt.toISOString() };
}
