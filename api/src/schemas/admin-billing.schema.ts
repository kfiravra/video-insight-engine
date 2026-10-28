import { z } from 'zod';
import { PLAN_IDS } from '@vie/types';

// Request shapes for /api/admin/billing/* and /api/admin/audit. These check
// types, required fields and dates only; the ranges (minutes, caps, expiry in
// the future) belong to BillingService, which validates them again and is the
// one place they are defined. Bodies are strict: a stray field such as
// `adminId` is a 400, never silently ignored.

const objectIdHex = z.string().regex(/^[0-9a-f]{24}$/i, 'must be a 24-character hex id');

/** 1–500 characters, required on every mutation: it lands in the ledger and the audit row. */
const reason = z.string().trim().min(1, 'reason is required').max(500, 'reason is at most 500 characters');

/** ISO 8601 with `Z` or an offset, as a Date. */
const isoDate = z
  .string()
  .datetime({ offset: true, message: 'must be an ISO 8601 timestamp' })
  .transform((value) => new Date(value));

export const accountParamsSchema = z.object({ userId: objectIdHex });

export const recurringGrantParamsSchema = z.object({ userId: objectIdHex, grantId: objectIdHex });

export const accountDetailQuerySchema = z.object({
  ledgerLimit: z.coerce.number().int().min(1).max(200).optional(),
  ledgerBefore: isoDate.optional(),
});

export const setPlanBodySchema = z
  .object({ planId: z.enum(PLAN_IDS), periodEnd: isoDate.optional(), reason })
  .strict();

export const grantCreditsBodySchema = z
  .object({ minutes: z.number().int(), expiresAt: isoDate.nullable().optional(), reason })
  .strict();

/** `null` clears a cap, an absent key leaves it. */
export const setCapsBodySchema = z
  .object({
    maxVideoMinutes: z.number().int().nullable().optional(),
    freeVideosPerDay: z.number().int().nullable().optional(),
    maxConcurrent: z.number().int().nullable().optional(),
    blocked: z.boolean().nullable().optional(),
    reason,
  })
  .strict();

export const adjustBodySchema = z
  .object({ bucket: z.enum(['plan', 'credit']), minutes: z.number().int(), reason })
  .strict();

export const createRecurringGrantBodySchema = z
  .object({ minutes: z.number().int(), every: z.literal('month'), active: z.boolean(), reason })
  .strict();

export const deleteRecurringGrantBodySchema = z.object({ reason }).strict();

const actorFilter = z.string().trim().min(1).max(200);

export const auditListQuerySchema = z.object({
  targetId: actorFilter.optional(),
  actorId: actorFilter.optional(),
  limit: z.coerce.number().int().min(1).max(200).optional(),
  before: isoDate.optional(),
});

/** Optional client-chosen key that makes a retried mutation apply once. */
export const idempotencyKeySchema = z
  .string()
  .regex(/^[A-Za-z0-9_-]{8,128}$/, 'Idempotency-Key must be 8–128 characters of A-Z a-z 0-9 _ -');
