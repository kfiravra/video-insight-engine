import { apiFetch, qs } from './api';
import type { BillingAccountSnapshot, BillingBucket } from './billing';

// Account actions and the audit log, over vie-admin's proxies to vie-api
// (services/admin/src/routes/billing_actions.py). Every mutation needs a reason
// and a named admin session; who acted comes from the session, never the body.
// One `Idempotency-Key` per submission lets a retry after a timeout apply once.

/** What vie-api answers a mutation with: the account before and after, and the audit row's id. */
export interface MutationResponse {
  before: BillingAccountSnapshot;
  after: BillingAccountSnapshot;
  /** Pairs the ledger entries with their `adminAudit` row. */
  auditId: string;
  lotId?: string;
  grantId?: string;
}

export interface SetPlanRequest {
  planId: 'free' | 'pro';
  /** ISO 8601 with an offset; the end of a manual Pro term. Omitted = 12 months. */
  periodEnd?: string;
  reason: string;
}

/** A cap that is present is set, an explicit `null` clears it, an absent one is left alone. */
export interface SetCapsRequest {
  maxVideoMinutes?: number | null;
  freeVideosPerDay?: number | null;
  maxConcurrent?: number | null;
  blocked?: boolean | null;
  reason: string;
}

export interface AdjustRequest {
  bucket: Exclude<BillingBucket, 'free'>;
  /** Signed whole minutes, never 0. */
  minutes: number;
  reason: string;
}

export interface CreateRecurringGrantRequest {
  minutes: number;
  every: 'month';
  active: boolean;
  reason: string;
}

export interface DeleteRecurringGrantRequest {
  reason: string;
}

/** One row of the admin action log, as vie-api returns it (`AdminAuditView`). */
export interface AuditEntry {
  id: string;
  /** `users._id` of the acting admin, or `system:<name>`. */
  actorId: string;
  actorEmail?: string | null;
  /** Dotted verb such as `billing.plan.set` or `videos.purge`. */
  action: string;
  targetType: 'user' | 'video' | 'queue' | 'alert' | 'role';
  targetId: string;
  before?: unknown;
  after?: unknown;
  reason?: string | null;
  requestId?: string | null;
  ip?: string | null;
  createdAt: string;
}

export interface AuditPage {
  entries: AuditEntry[];
  /** Pass as `before` for the next (older) page; null on the last page. */
  nextBefore: string | null;
}

export interface AuditQuery {
  targetId?: string;
  actorId?: string;
  limit?: number;
  before?: string;
}

function send<T>(method: 'POST' | 'DELETE', path: string, body: object, idempotencyKey?: string) {
  return apiFetch<T>(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
    },
    body: JSON.stringify(body),
  });
}

function accountPath(userId: string, tail: string): string {
  return `/billing/accounts/${encodeURIComponent(userId)}/${tail}`;
}

export const billingActions = {
  setPlan: (userId: string, body: SetPlanRequest, idempotencyKey?: string) =>
    send<MutationResponse>('POST', accountPath(userId, 'plan'), body, idempotencyKey),
  setCaps: (userId: string, body: SetCapsRequest, idempotencyKey?: string) =>
    send<MutationResponse>('POST', accountPath(userId, 'caps'), body, idempotencyKey),
  adjust: (userId: string, body: AdjustRequest, idempotencyKey?: string) =>
    send<MutationResponse>('POST', accountPath(userId, 'adjust'), body, idempotencyKey),
  createRecurringGrant: (userId: string, body: CreateRecurringGrantRequest, idempotencyKey?: string) =>
    send<MutationResponse>('POST', accountPath(userId, 'recurring-grants'), body, idempotencyKey),
  deleteRecurringGrant: (
    userId: string,
    grantId: string,
    body: DeleteRecurringGrantRequest,
    idempotencyKey?: string,
  ) =>
    send<MutationResponse>(
      'DELETE',
      accountPath(userId, `recurring-grants/${encodeURIComponent(grantId)}`),
      body,
      idempotencyKey,
    ),
};

export const auditApi = {
  list: (query: AuditQuery = {}) =>
    apiFetch<AuditPage>(
      `/audit${qs({ targetId: query.targetId, actorId: query.actorId, limit: query.limit, before: query.before })}`,
    ),
};
