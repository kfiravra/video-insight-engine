import type { ObjectId } from 'mongodb';
import type { BillingBucket, BillingSource, ExecutionKind, LedgerEntryType } from '@vie/types';
import type {
  BillingAccountDocument,
  BillingHoldDocument,
  CreditLotDocument,
  NewLedgerEntry,
} from '../../repositories/billing.repository.js';

/** What a state transition says about one ledger entry; the draft fills account, user and time. */
export interface EntryFields {
  type: LedgerEntryType;
  bucket: BillingBucket;
  /** Signed: + adds headroom, − consumes it. */
  tokens: number;
  idempotencyKey: string;
  source: BillingSource;
  /** A debit that posts tokens its hold already took. */
  mirrorsHold?: boolean;
  lotId?: ObjectId;
  executionKind?: ExecutionKind;
  executionId?: string;
  /** Defaults to the account owner. */
  userId?: ObjectId;
  youtubeId?: string;
  durationSec?: number;
  costUsd?: number;
  actorId?: string;
  auditId?: ObjectId;
  reason?: string;
}

export interface LotChange {
  lotId: ObjectId;
  delta: number;
}

function cloneHold(hold: BillingHoldDocument): BillingHoldDocument {
  return { ...hold, split: { ...hold.split, lots: hold.split.lots.map((slice) => ({ ...slice })) } };
}

/**
 * Deep copy of the mutable parts (ObjectId / Date values are shared — never
 * mutated in place). The bucket spread keeps any pre-migration minute fields.
 */
function cloneAccount(doc: BillingAccountDocument): BillingAccountDocument {
  return {
    ...doc,
    plan: { ...doc.plan },
    buckets: {
      ...doc.buckets,
      plan: { ...doc.buckets.plan },
      credit: { ...doc.buckets.credit },
      debt: { ...doc.buckets.debt },
    },
    holds: doc.holds.map(cloneHold),
    caps: { ...doc.caps },
    ...(doc.capsAuditIds && { capsAuditIds: [...doc.capsAuditIds] }),
  };
}

/**
 * An account's next state, built in memory by the pure transitions (roll,
 * hold, settle, release, charges, admin ops) and written by BillingService in
 * one transaction: the conditional account update, new lots, lot deltas, and
 * the ledger entries (all `unit: 'tokens'`). Every credit movement goes
 * through `changeLot` / `addLot` so `buckets.credit.tokensAvailable` can
 * never drift from the lots it summarises.
 */
export class AccountDraft {
  readonly account: BillingAccountDocument;
  /** Working copies of the lots this unit touches (open lots first, FIFO). */
  readonly lots: CreditLotDocument[];
  readonly newLots: CreditLotDocument[] = [];
  readonly entries: NewLedgerEntry[] = [];
  private readonly lotDeltas = new Map<string, LotChange>();
  private readonly newLotIds = new Set<string>();
  private accountChanged = false;

  constructor(
    readonly original: BillingAccountDocument,
    lots: CreditLotDocument[],
    readonly now: Date,
  ) {
    this.account = cloneAccount(original);
    this.lots = lots.map((lot) => ({ ...lot }));
  }

  get accountId(): ObjectId {
    return this.original._id;
  }

  /** Anything to write? A read-only pass (quote, summary, a replay) leaves the draft clean. */
  get dirty(): boolean {
    return this.accountChanged || this.entries.length > 0 || this.newLots.length > 0 || this.lotDeltas.size > 0;
  }

  markChanged(): void {
    this.accountChanged = true;
  }

  addEntry(fields: EntryFields): void {
    const { userId, ...rest } = fields;
    this.entries.push({
      ...rest,
      unit: 'tokens',
      accountId: this.original._id,
      userId: userId ?? this.original.ownerId,
      createdAt: this.now,
    });
  }

  findLot(lotId: ObjectId): CreditLotDocument | undefined {
    return this.lots.find((lot) => lot._id.equals(lotId));
  }

  /** Bring lots outside the open set (spent ones a release refills) into the unit. */
  includeLots(lots: CreditLotDocument[]): void {
    for (const lot of lots) {
      if (!this.findLot(lot._id)) this.lots.push({ ...lot });
    }
  }

  changeLot(lot: CreditLotDocument, delta: number): void {
    if (delta === 0) return;
    lot.tokensRemaining += delta;
    this.account.buckets.credit.tokensAvailable += delta;
    this.accountChanged = true;
    const key = lot._id.toHexString();
    if (this.newLotIds.has(key)) return; // the insert carries the final value
    const change = this.lotDeltas.get(key) ?? { lotId: lot._id, delta: 0 };
    change.delta += delta;
    this.lotDeltas.set(key, change);
  }

  addLot(lot: CreditLotDocument): void {
    this.newLots.push(lot);
    this.newLotIds.add(lot._id.toHexString());
    this.lots.push(lot);
    this.account.buckets.credit.tokensAvailable += lot.tokensRemaining;
    this.accountChanged = true;
  }

  /** Move what the account owes by `delta` (+ = more debt). */
  changeDebt(delta: number): void {
    if (delta === 0) return;
    this.account.buckets.debt.tokens += delta;
    this.accountChanged = true;
  }

  lotChanges(): LotChange[] {
    return [...this.lotDeltas.values()].filter((change) => change.delta !== 0);
  }
}
