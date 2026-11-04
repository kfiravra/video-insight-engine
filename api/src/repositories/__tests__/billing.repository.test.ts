import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { MongoError, ObjectId } from 'mongodb';
import { getTestDb } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import {
  BillingRepository,
  emptyAccountState,
  normalizeAccount,
  StaleAccountVersionError,
  type BillingAccountDocument,
  type CreditLotDocument,
  type NewLedgerEntry,
} from '../billing.repository.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function transientError(): MongoError {
  const err = new MongoError('simulated write conflict');
  err.addErrorLabel('TransientTransactionError');
  return err;
}

describe('BillingRepository', () => {
  let repo: BillingRepository;
  const accountId = new ObjectId();
  const userId = new ObjectId();

  const makeEntry = (overrides: Partial<NewLedgerEntry> = {}): NewLedgerEntry => ({
    accountId,
    userId,
    type: 'grant',
    bucket: 'credit',
    unit: 'tokens',
    tokens: 10,
    source: { kind: 'admin', ref: 'audit-1' },
    idempotencyKey: `key-${new ObjectId().toHexString()}`,
    createdAt: new Date(),
    ...overrides,
  });

  const makeLot = (overrides: Partial<CreditLotDocument> = {}): CreditLotDocument => ({
    _id: new ObjectId(),
    accountId,
    tokensGranted: 30,
    tokensRemaining: 30,
    source: { kind: 'admin', ref: 'audit-1' },
    expiresAt: null,
    createdAt: new Date(),
    ...overrides,
  });

  const makeAccount = (ownerId = new ObjectId()): BillingAccountDocument => ({
    _id: new ObjectId(), ownerType: 'user', ownerId, ...emptyAccountState(new Date()),
  });

  /** An account as the minutes ledger wrote it, before the token migration. */
  const minuteAccount = (): BillingAccountDocument => ({
    _id: new ObjectId(),
    ownerType: 'user',
    ownerId: new ObjectId(),
    plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
    buckets: {
      free: { recentFreeVideoAt: [new Date('2026-10-30T00:00:00Z')] },
      plan: { cycleStart: null, cycleEnd: null, minutesGranted: 600, minutesUsed: 40 },
      credit: { minutesAvailable: 150 },
    },
    holds: [{ requestId: 'old-run', userId, youtubeId: 'premigrate1', minutes: 10, split: { free: 10, plan: 0, credit: 0, lots: [] }, createdAt: new Date('2026-10-30T00:00:00Z') }],
    caps: {},
    version: 3,
    createdAt: new Date('2026-10-01T00:00:00Z'),
    updatedAt: new Date('2026-10-30T00:00:00Z'),
  } as unknown as BillingAccountDocument);

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(() => {
    repo = new BillingRepository(getTestDb());
  });

  describe('createAccount', () => {
    it('should insert the account with its first lots and entries', async () => {
      const account = makeAccount();
      const lot = makeLot({ accountId: account._id });

      const created = await repo.createAccount(account, [lot], [makeEntry({ accountId: account._id, idempotencyKey: 'grant:signup:x' })]);

      expect(created).toBe(true);
      expect((await repo.findAccount('user', account.ownerId.toHexString()))?._id.equals(account._id)).toBe(true);
      expect(await repo.listOpenLots(account._id)).toHaveLength(1);
      expect(await repo.findLedgerByKeys(['grant:signup:x'])).toHaveLength(1);
    });

    it('should return false for an owner that already has an account and write none of the loser\'s rows', async () => {
      const owner = new ObjectId();
      await repo.createAccount(makeAccount(owner), [], []);
      const loser = makeAccount(owner);

      const created = await repo.createAccount(loser, [makeLot({ accountId: loser._id })], [makeEntry({ idempotencyKey: 'grant:signup:loser' })]);

      expect(created).toBe(false);
      expect(await getTestDb().collection('billingAccounts').countDocuments({ ownerId: owner })).toBe(1);
      expect(await repo.listOpenLots(loser._id)).toEqual([]);
      expect(await repo.findLedgerByKeys(['grant:signup:loser'])).toEqual([]);
    });

    it('should let exactly one of ten racing first writes create the account', async () => {
      const owner = new ObjectId();

      const results = await Promise.all(Array.from({ length: 10 }, () => repo.createAccount(makeAccount(owner), [], [])));

      expect(results.filter(Boolean)).toHaveLength(1);
      expect(await getTestDb().collection('billingAccounts').countDocuments({ ownerId: owner })).toBe(1);
    });
  });

  describe('normalizeAccount', () => {
    it('should read the token fields of a minute-era account as 0 and keep its minute fields', () => {
      const legacy = minuteAccount();

      const account = normalizeAccount(legacy);

      expect(account.buckets).toEqual({
        free: { recentFreeVideoAt: [new Date('2026-10-30T00:00:00Z')] },
        plan: { cycleStart: null, cycleEnd: null, minutesGranted: 600, minutesUsed: 40, tokensGranted: 0, tokensUsed: 0 },
        credit: { minutesAvailable: 150, tokensAvailable: 0 },
        debt: { tokens: 0 },
      });
      expect(account.version).toBe(3);
    });

    it('should read a minute hold as a 0-token hold that keeps its minutes', () => {
      const [hold] = normalizeAccount(minuteAccount()).holds;

      expect(hold).toMatchObject({
        requestId: 'old-run', youtubeId: 'premigrate1', durationSec: 0, tokens: 0,
        split: { plan: 0, credit: 0, lots: [] }, planCycleStart: null, minutes: 10,
      });
    });

    it('should leave a token account as it is', () => {
      const account = makeAccount();
      account.holds = [{
        requestId: 'r', userId, youtubeId: null, durationSec: 60, tokens: 678,
        split: { plan: 0, credit: 678, lots: [{ lotId: new ObjectId(), tokens: 678 }] }, planCycleStart: null, createdAt: new Date(),
      }];

      expect(normalizeAccount(account)).toEqual(account);
    });

    it('should normalize what every account read returns', async () => {
      const legacy = minuteAccount();
      await getTestDb().collection('billingAccounts').insertOne(legacy);

      const reads = [
        await repo.findAccount('user', legacy.ownerId.toHexString()),
        await repo.findAccountById(legacy._id),
        await repo.findAccountByHold('old-run'),
      ];

      expect(reads.map((a) => [a?.buckets.credit.tokensAvailable, a?.buckets.debt.tokens, a?.holds[0].tokens])).toEqual(Array(3).fill([0, 0, 0]));
    });
  });

  describe('updateAccountConditional', () => {
    it('should write and bump the version when the version matches', async () => {
      const account = makeAccount();
      await repo.createAccount(account, [], []);

      const updated = await repo.updateAccountConditional(account._id, 0, { ...account, caps: { blocked: true }, signupGrantAt: new Date() });

      expect(updated?.version).toBe(1);
      expect(updated?.caps.blocked).toBe(true);
      expect(updated?.signupGrantAt).toBeInstanceOf(Date);
    });

    it('should reject a stale version and leave the account unchanged', async () => {
      const account = makeAccount();
      await repo.createAccount(account, [], []);
      await repo.updateAccountConditional(account._id, 0, account);

      const stale = await repo.updateAccountConditional(account._id, 0, { ...account, caps: { blocked: true } });

      expect(stale).toBeNull();
      expect((await repo.findAccountById(account._id))?.caps.blocked).toBeUndefined();
    });
  });

  describe('insertLedgerEntry', () => {
    it('should insert a new entry', async () => {
      const result = await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k1' }));

      expect(result.inserted).toBe(true);
      expect(result.entry).toMatchObject({ idempotencyKey: 'k1', unit: 'tokens', tokens: 10 });
    });

    it('should return the existing entry instead of throwing on a duplicate key', async () => {
      const first = await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k2', tokens: 5 }));

      const second = await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k2', tokens: 99 }));

      expect(second.inserted).toBe(false);
      expect(second.entry._id.equals(first.entry._id)).toBe(true);
      expect(second.entry.tokens).toBe(5);
    });

    it('should return the existing entry inside a transaction without aborting it', async () => {
      await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k3' }));

      const result = await repo.withTransaction(async (session) => {
        const dup = await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k3' }), session);
        await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k4' }), session);
        return dup;
      });

      expect(result.inserted).toBe(false);
      expect(await repo.findLedgerByKeys(['k3', 'k4'])).toHaveLength(2);
    });
  });

  describe('findLedgerBySourceRef', () => {
    it('should return only the entries of that source ref', async () => {
      await repo.insertLedgerEntry(makeEntry({ source: { kind: 'run', ref: 'req-1' } }));
      await repo.insertLedgerEntry(makeEntry({ source: { kind: 'run', ref: 'req-2' } }));

      const entries = await repo.findLedgerBySourceRef('req-1');

      expect(entries.map((e) => e.source.ref)).toEqual(['req-1']);
    });
  });

  describe('findExecutionDebits', () => {
    it('should match the execution\'s own debit keys only, by exact id prefix', async () => {
      for (const key of ['debit:exec:ex-1:plan', 'debit:exec:ex-1:lot:abc', 'debit:exec:ex-10:plan', 'debit:ex-1:plan', 'repay:debit:exec:ex-1:plan']) {
        await repo.insertLedgerEntry(makeEntry({ idempotencyKey: key }));
      }

      const debits = await repo.findExecutionDebits('ex-1');

      expect(debits.map((e) => e.idempotencyKey).sort()).toEqual(['debit:exec:ex-1:lot:abc', 'debit:exec:ex-1:plan']);
    });

    it('should escape regex characters in the id', async () => {
      await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'debit:exec:axb:plan' }));
      await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'debit:exec:a.b:plan' }));

      expect((await repo.findExecutionDebits('a.b')).map((e) => e.idempotencyKey)).toEqual(['debit:exec:a.b:plan']);
      expect(await repo.findExecutionDebits('.*')).toEqual([]);
    });
  });

  describe('listLedger', () => {
    it('should page newest first without splitting entries that share a timestamp', async () => {
      const t1 = new Date('2026-10-01T00:00:00Z');
      const t2 = new Date('2026-10-02T00:00:00Z');
      for (const createdAt of [t1, t2, t2, t2]) {
        await repo.insertLedgerEntry(makeEntry({ createdAt }));
      }

      const firstPage = await repo.listLedger(accountId, { limit: 2 });
      const nextPage = await repo.listLedger(accountId, { limit: 2, before: firstPage.at(-1)?.createdAt });

      expect(firstPage.map((e) => e.createdAt)).toEqual([t2, t2, t2]);
      expect(nextPage.map((e) => e.createdAt)).toEqual([t1]);
    });
  });

  describe('listOpenLots', () => {
    it('should order dated lots by expiry, then never-expiring lots, each by creation time', async () => {
      const now = Date.now();
      const late = makeLot({ expiresAt: new Date(now + 30 * DAY_MS), createdAt: new Date(now - 3 * DAY_MS) });
      const soon = makeLot({ expiresAt: new Date(now + 1 * DAY_MS), createdAt: new Date(now - 1 * DAY_MS) });
      const neverOld = makeLot({ expiresAt: null, createdAt: new Date(now - 9 * DAY_MS) });
      const neverNew = makeLot({ expiresAt: null, createdAt: new Date(now - 2 * DAY_MS) });
      await repo.insertLots([neverNew, late, neverOld, soon]);

      const lots = await repo.listOpenLots(accountId);

      expect(lots.map((l) => l._id.toHexString())).toEqual([soon, late, neverOld, neverNew].map((l) => l._id.toHexString()));
    });

    it('should leave out spent lots and minute lots from before the token migration', async () => {
      await repo.insertLots([makeLot({ tokensRemaining: 0 }), makeLot({ tokensRemaining: 5 })]);
      await getTestDb().collection('creditLots').insertOne({
        accountId, minutesGranted: 100, minutesRemaining: 100, source: { kind: 'admin', ref: 'legacy' }, expiresAt: null, createdAt: new Date(),
      });

      const lots = await repo.listOpenLots(accountId);

      expect(lots.map((l) => l.tokensRemaining)).toEqual([5]);
    });
  });

  describe('listLots', () => {
    it('should list every token lot newest first, spent ones included, minute lots left out', async () => {
      const now = Date.now();
      const spent = makeLot({ tokensRemaining: 0, createdAt: new Date(now - DAY_MS) });
      const open = makeLot({ createdAt: new Date(now) });
      await repo.insertLots([spent, open]);
      await getTestDb().collection('creditLots').insertOne({
        accountId, minutesGranted: 100, minutesRemaining: 100, source: { kind: 'admin', ref: 'legacy' }, expiresAt: null, createdAt: new Date(now + DAY_MS),
      });

      const lots = await repo.listLots(accountId);
      const limited = await repo.listLots(accountId, 1);

      expect(lots.map((l) => l._id.toHexString())).toEqual([open._id.toHexString(), spent._id.toHexString()]);
      expect(limited).toHaveLength(1);
    });
  });

  describe('changeLotRemaining', () => {
    it('should move tokensRemaining both ways', async () => {
      const lot = makeLot({ tokensRemaining: 10 });
      await repo.insertLots([lot]);

      await repo.changeLotRemaining(lot._id, -4);
      await repo.changeLotRemaining(lot._id, 7);

      expect((await repo.findLotsByIds([lot._id]))[0].tokensRemaining).toBe(13);
    });

    it('should refuse to take a lot below zero', async () => {
      const lot = makeLot({ tokensRemaining: 4 });
      await repo.insertLots([lot]);

      await expect(repo.changeLotRemaining(lot._id, -5)).rejects.toBeInstanceOf(StaleAccountVersionError);
      expect((await repo.findLotsByIds([lot._id]))[0].tokensRemaining).toBe(4);
    });
  });

  describe('withTransaction', () => {
    it('should commit every write when the callback resolves', async () => {
      await repo.withTransaction(async (session) => {
        await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'tx-a' }), session);
        await repo.insertLots([makeLot()], session);
      });

      expect(await repo.findLedgerByKeys(['tx-a'])).toHaveLength(1);
      expect(await repo.listOpenLots(accountId)).toHaveLength(1);
    });

    it('should abort every write when the callback throws', async () => {
      await expect(
        repo.withTransaction(async (session) => {
          await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'tx-b' }), session);
          throw new Error('boom');
        }),
      ).rejects.toThrow('boom');

      expect(await repo.findLedgerByKeys(['tx-b'])).toHaveLength(0);
    });

    it('should retry the unit after a transient transaction error', async () => {
      let attempts = 0;

      const result = await repo.withTransaction(async (session) => {
        attempts++;
        await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'tx-c' }), session);
        if (attempts === 1) throw transientError();
        return 'done';
      });

      expect(result).toBe('done');
      expect(attempts).toBe(2);
      expect(await repo.findLedgerByKeys(['tx-c'])).toHaveLength(1);
    });

    it('should retry after a stale account version', async () => {
      let attempts = 0;

      await repo.withTransaction(async () => {
        attempts++;
        if (attempts < 3) throw new StaleAccountVersionError();
      });

      expect(attempts).toBe(3);
    });

    it('should give up after the attempt bound', async () => {
      let attempts = 0;

      await expect(
        repo.withTransaction(async () => {
          attempts++;
          throw transientError();
        }, 3),
      ).rejects.toThrow('simulated write conflict');
      expect(attempts).toBe(3);
    });

    it('should not retry a non-transient error', async () => {
      let attempts = 0;

      await expect(
        repo.withTransaction(async () => {
          attempts++;
          throw new Error('not transient');
        }),
      ).rejects.toThrow('not transient');
      expect(attempts).toBe(1);
    });

    it('should serialise concurrent writers to one document through real write conflicts', async () => {
      const account = makeAccount();
      await repo.createAccount(account, [], []);

      await Promise.all(
        Array.from({ length: 5 }, () =>
          repo.withTransaction(async (session) => {
            const fresh = await repo.findAccountById(account._id, session);
            if (!fresh) throw new Error('account missing');
            const credit = { tokensAvailable: fresh.buckets.credit.tokensAvailable + 1 };
            const updated = await repo.updateAccountConditional(
              fresh._id,
              fresh.version,
              { ...fresh, buckets: { ...fresh.buckets, credit } },
              session,
            );
            if (!updated) throw new StaleAccountVersionError();
          }, 20),
        ),
      );

      const final = await repo.findAccountById(account._id);
      expect(final?.buckets.credit.tokensAvailable).toBe(5);
      expect(final?.version).toBe(5);
    });
  });

  describe('recurring grants', () => {
    const grantBase = () => {
      const now = new Date();
      return {
        accountId, every: 'month' as const, active: true, nextRunAt: now, lastRunAt: null,
        actorId: 'system:test', reason: 'demo', createdAt: now, updatedAt: now, deletedAt: null,
      };
    };

    it('should hide soft-deleted grants from the list', async () => {
      const kept = { ...grantBase(), tokens: 10_000, _id: new ObjectId(), auditId: new ObjectId() };
      const deleted = { ...grantBase(), tokens: 10_000, _id: new ObjectId(), auditId: new ObjectId() };
      await repo.insertRecurringGrant(kept);
      await repo.insertRecurringGrant(deleted);

      await repo.softDeleteRecurringGrant(deleted._id, new Date());

      const grants = await repo.listRecurringGrants(accountId);
      expect(grants.map((g) => g._id.toHexString())).toEqual([kept._id.toHexString()]);
    });

    it('should hide minute grants from before the token migration', async () => {
      await getTestDb().collection('recurringGrants').insertOne({ ...grantBase(), minutes: 300, auditId: new ObjectId() });

      expect(await repo.listRecurringGrants(accountId)).toEqual([]);
    });
  });
});
