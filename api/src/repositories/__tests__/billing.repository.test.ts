import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { MongoError, ObjectId } from 'mongodb';
import { getTestDb } from '../../test/setup.js';
import { createBillingIndexes } from '../../plugins/mongodb.js';
import {
  BillingRepository,
  StaleAccountVersionError,
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
    minutes: 10,
    source: { kind: 'admin', ref: 'audit-1' },
    idempotencyKey: `key-${new ObjectId().toHexString()}`,
    createdAt: new Date(),
    ...overrides,
  });

  const makeLot = (overrides: Partial<CreditLotDocument> = {}): CreditLotDocument => ({
    _id: new ObjectId(),
    accountId,
    minutesGranted: 30,
    minutesRemaining: 30,
    source: { kind: 'admin', ref: 'audit-1' },
    expiresAt: null,
    createdAt: new Date(),
    ...overrides,
  });

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
  });

  beforeEach(() => {
    repo = new BillingRepository(getTestDb());
  });

  describe('getOrCreateAccount', () => {
    it('should create a free account with empty buckets on first call', async () => {
      const account = await repo.getOrCreateAccount('user', userId.toHexString());

      expect(account.plan.id).toBe('free');
      expect(account.buckets.credit.minutesAvailable).toBe(0);
      expect(account.holds).toEqual([]);
      expect(account.version).toBe(0);
    });

    it('should return the same account on a second call', async () => {
      const first = await repo.getOrCreateAccount('user', userId.toHexString());
      const second = await repo.getOrCreateAccount('user', userId.toHexString());

      expect(second._id.equals(first._id)).toBe(true);
    });

    it('should create exactly one account when first calls race', async () => {
      const owner = new ObjectId().toHexString();

      const accounts = await Promise.all(Array.from({ length: 10 }, () => repo.getOrCreateAccount('user', owner)));

      expect(new Set(accounts.map((a) => a._id.toHexString())).size).toBe(1);
      expect(await getTestDb().collection('billingAccounts').countDocuments({ ownerId: new ObjectId(owner) })).toBe(1);
    });
  });

  describe('updateAccountConditional', () => {
    it('should write and bump the version when the version matches', async () => {
      const account = await repo.getOrCreateAccount('user', userId.toHexString());

      const updated = await repo.updateAccountConditional(account._id, 0, { ...account, caps: { blocked: true } });

      expect(updated?.version).toBe(1);
      expect(updated?.caps.blocked).toBe(true);
    });

    it('should reject a stale version and leave the account unchanged', async () => {
      const account = await repo.getOrCreateAccount('user', userId.toHexString());
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
      expect(result.entry.idempotencyKey).toBe('k1');
    });

    it('should return the existing entry instead of throwing on a duplicate key', async () => {
      const first = await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k2', minutes: 5 }));

      const second = await repo.insertLedgerEntry(makeEntry({ idempotencyKey: 'k2', minutes: 99 }));

      expect(second.inserted).toBe(false);
      expect(second.entry._id.equals(first.entry._id)).toBe(true);
      expect(second.entry.minutes).toBe(5);
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

      expect(lots.map((l) => l._id.toHexString())).toEqual(
        [soon, late, neverOld, neverNew].map((l) => l._id.toHexString()),
      );
    });

    it('should leave out spent lots', async () => {
      await repo.insertLots([makeLot({ minutesRemaining: 0 }), makeLot({ minutesRemaining: 5 })]);

      const lots = await repo.listOpenLots(accountId);

      expect(lots.map((l) => l.minutesRemaining)).toEqual([5]);
    });
  });

  describe('changeLotRemaining', () => {
    it('should refuse to take a lot below zero', async () => {
      const lot = makeLot({ minutesRemaining: 4 });
      await repo.insertLots([lot]);

      await expect(repo.changeLotRemaining(lot._id, -5)).rejects.toBeInstanceOf(StaleAccountVersionError);
      expect((await repo.findLotsByIds([lot._id]))[0].minutesRemaining).toBe(4);
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
      const account = await repo.getOrCreateAccount('user', new ObjectId().toHexString());

      await Promise.all(
        Array.from({ length: 5 }, () =>
          repo.withTransaction(async (session) => {
            const fresh = await repo.findAccountById(account._id, session);
            if (!fresh) throw new Error('account missing');
            const credit = { minutesAvailable: fresh.buckets.credit.minutesAvailable + 1 };
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
      expect(final?.buckets.credit.minutesAvailable).toBe(5);
      expect(final?.version).toBe(5);
    });
  });

  describe('recurring grants', () => {
    it('should hide soft-deleted grants from the list', async () => {
      const now = new Date();
      const base = {
        accountId, minutes: 300, every: 'month' as const, active: true, nextRunAt: now, lastRunAt: null,
        actorId: 'system:test', reason: 'demo', createdAt: now, updatedAt: now, deletedAt: null,
      };
      const kept = { ...base, _id: new ObjectId(), auditId: new ObjectId() };
      const deleted = { ...base, _id: new ObjectId(), auditId: new ObjectId() };
      await repo.insertRecurringGrant(kept);
      await repo.insertRecurringGrant(deleted);

      await repo.softDeleteRecurringGrant(deleted._id, now);

      const grants = await repo.listRecurringGrants(accountId);
      expect(grants.map((g) => g._id.toHexString())).toEqual([kept._id.toHexString()]);
    });
  });
});
