import { randomUUID } from 'node:crypto';
import { ObjectId } from 'mongodb';
import type { BillingSourceKind } from '@vie/types';

// Pure helpers for GDPR erasure of the minutes ledger (C1): what replaces the
// ids an erased account's rows carry. No database access here.

/** `source.kind`s whose `ref` is an adminAudit id (admin steps, the P3.8 migration). */
const AUDIT_REF_KINDS: ReadonlySet<BillingSourceKind> = new Set<BillingSourceKind>(['admin', 'migration']);
/** `source.kind`s whose `ref` is a run (or payment) id the user's requests carried. */
const RUN_REF_KINDS: ReadonlySet<BillingSourceKind> = new Set<BillingSourceKind>(['run', 'payment']);

/**
 * Fresh replacements for the ids an erased account's rows carry, one per old
 * value, so rows that shared an id (a hold and its debit, an adminAudit row
 * and its ledger entries) still share the new one. Random, never derived: a
 * value computed from the old id would let anyone who knows the real user id
 * (`userDeletions.originalUserId`, the migration's `auditIdFor`) compute it
 * again and join back. `system` / `recurring` refs name account-internal ids
 * (lots, the account itself, grants) and stay.
 */
export class ErasureIdMap {
  private readonly audits = new Map<string, ObjectId>();
  private readonly runs = new Map<string, string>();

  auditId(old: ObjectId | string): ObjectId {
    const key = typeof old === 'string' ? old.toLowerCase() : old.toHexString();
    let next = this.audits.get(key);
    if (!next) {
      next = new ObjectId();
      this.audits.set(key, next);
    }
    return next;
  }

  runId(old: string): string {
    let next = this.runs.get(old);
    if (!next) {
      next = randomUUID();
      this.runs.set(old, next);
    }
    return next;
  }

  /** The new `source.ref`; unchanged for kinds whose ref links only inside the account. */
  sourceRef(kind: BillingSourceKind, ref: string): string {
    if (AUDIT_REF_KINDS.has(kind)) return this.auditId(ref).toHexString();
    if (RUN_REF_KINDS.has(kind)) return this.runId(ref);
    return ref;
  }
}

/**
 * The idempotency key with the entry's old ref swapped for the new one
 * (`hold:<run>:free`, `admin:<auditId>:lot:<lotId>`, …): unique as before,
 * because the mapping is one-to-one and every new ref is fresh.
 */
export function remapKey(key: string, oldRef: string, newRef: string): string {
  return oldRef === newRef || !key.includes(oldRef) ? key : key.split(oldRef).join(newRef);
}

/** A plain-JSON value (adminAudit `before` / `after`) with every string equal to `from` replaced by `to`. */
export function replaceDeep(value: unknown, from: string, to: string): unknown {
  if (typeof value === 'string') return value.toLowerCase() === from ? to : value;
  if (Array.isArray(value)) return value.map((item) => replaceDeep(item, from, to));
  // Only plain objects are walked: a Date or an ObjectId is kept as it is.
  if (value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replaceDeep(item, from, to)]));
  }
  return value;
}
