import { ObjectId } from 'mongodb';
import { DatabaseError, ValidationError } from '../../utils/errors.js';
import { StaleAccountVersionError } from '../../repositories/billing.repository.js';
import { AccountDraft } from './account-draft.js';
import { KeyedMutex } from './keyed-mutex.js';
import { rollPlanCycle } from './plan-cycle.js';
import type { ClientSession } from 'mongodb';
import type {
  BillingAccountDocument,
  BillingRepository,
  CreditLotDocument,
} from '../../repositories/billing.repository.js';

/** What an operation inside a billing transaction works with. */
export interface BillingUnit {
  draft: AccountDraft;
  session: ClientSession;
}

/** An operation's value plus the account as committed (the original when nothing changed). */
export interface UnitOutcome<T> {
  value: T;
  account: BillingAccountDocument;
  lots: CreditLotDocument[];
}

const OBJECT_ID_HEX = /^[0-9a-f]{24}$/i;

/** Owner ids arrive as strings from routes and the submit path; anything else is a caller bug or bad input. */
export function parseObjectId(value: string, label: string): ObjectId {
  if (!OBJECT_ID_HEX.test(value)) throw new ValidationError(`${label} must be a 24-character hex id`);
  return new ObjectId(value);
}

/**
 * The read → transition → write cycle every billing change goes through:
 * load the account and its open lots inside a transaction, roll the plan
 * cycle to now, let the operation change an AccountDraft, then write the
 * draft — conditional on the version that was read — in the same
 * transaction. A stale version or a write conflict retries the whole unit.
 * Units on the same account are queued in-process first (KeyedMutex), so
 * only writers in other processes ever meet as write conflicts.
 */
export class BillingUnits {
  private readonly accountLocks = new KeyedMutex();

  constructor(
    private readonly repo: BillingRepository,
    private readonly clock: () => Date,
  ) {}

  /** The account rolled to now, in memory only (quotes, summaries, admin reads). */
  async read(ownerId: string): Promise<AccountDraft> {
    parseObjectId(ownerId, 'userId');
    const account = await this.repo.getOrCreateAccount('user', ownerId);
    const lots = await this.repo.listOpenLots(account._id);
    const draft = new AccountDraft(account, lots, this.clock());
    rollPlanCycle(draft);
    return draft;
  }

  async write<T>(ownerId: string, op: (unit: BillingUnit) => Promise<T>): Promise<UnitOutcome<T>> {
    parseObjectId(ownerId, 'userId');
    const { _id: accountId } = await this.repo.getOrCreateAccount('user', ownerId);
    return this.accountLocks.run(accountId.toHexString(), () =>
      this.repo.withTransaction(async (session) => {
        const account = await this.repo.findAccountById(accountId, session);
        if (!account) throw new DatabaseError(`billing account ${accountId.toHexString()} vanished`);
        return this.runUnit(account, session, op);
      }),
    );
  }

  /** Like `write`, on the account holding `requestId`; `op` gets null when no account holds it. */
  async writeByHold<T>(requestId: string, op: (unit: BillingUnit | null) => Promise<T>): Promise<T> {
    const holder = await this.repo.findAccountByHold(requestId);
    if (!holder) return op(null);
    return this.accountLocks.run(holder._id.toHexString(), () =>
      this.repo.withTransaction(async (session) => {
        // Re-read inside the unit: the hold may have been settled meanwhile.
        const account = await this.repo.findAccountByHold(requestId, session);
        if (!account) return op(null);
        const outcome = await this.runUnit(account, session, (unit) => op(unit));
        return outcome.value;
      }),
    );
  }

  private async runUnit<T>(
    account: BillingAccountDocument,
    session: ClientSession,
    op: (unit: BillingUnit) => Promise<T>,
  ): Promise<UnitOutcome<T>> {
    const lots = await this.repo.listOpenLots(account._id, session);
    const draft = new AccountDraft(account, lots, this.clock());
    rollPlanCycle(draft);
    const value = await op({ draft, session });
    const committed = await this.persist(draft, session);
    return { value, account: committed, lots: draft.lots };
  }

  /** Write the draft; the conditional account update first, as the cheap conflict check. */
  private async persist(draft: AccountDraft, session: ClientSession): Promise<BillingAccountDocument> {
    if (!draft.dirty) return draft.original;
    const { _id, version } = draft.original;
    const updated = await this.repo.updateAccountConditional(_id, version, draft.account, session);
    if (!updated) throw new StaleAccountVersionError();
    await this.repo.insertLots(draft.newLots, session);
    for (const { lotId, delta } of draft.lotChanges()) {
      await this.repo.changeLotRemaining(lotId, delta, session);
    }
    for (const entry of draft.entries) {
      const { inserted } = await this.repo.insertLedgerEntry(entry, session);
      // Operations check their keys first; an existing key here means two
      // writers planned the same entry — abort rather than double-apply.
      if (!inserted) throw new DatabaseError(`ledger entry ${entry.idempotencyKey} already exists`);
    }
    return updated;
  }
}
