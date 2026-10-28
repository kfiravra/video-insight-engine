import { describe, it, expect, beforeEach } from 'vitest';
import type { Collection } from 'mongodb';
import { getTestClient, getTestDb } from '../../test/setup.js';

interface TxDoc {
  key: string;
}

describe('MongoDB transactions (single-node replica set)', () => {
  let first: Collection<TxDoc>;
  let second: Collection<TxDoc>;

  beforeEach(() => {
    first = getTestDb().collection<TxDoc>('tx_first');
    second = getTestDb().collection<TxDoc>('tx_second');
  });

  describe('withTransaction', () => {
    it('should commit every write when the callback resolves', async () => {
      const session = getTestClient().startSession();

      try {
        await session.withTransaction(async () => {
          await first.insertOne({ key: 'a' }, { session });
          await second.insertOne({ key: 'b' }, { session });
        });
      } finally {
        await session.endSession();
      }

      expect(await first.countDocuments({ key: 'a' })).toBe(1);
      expect(await second.countDocuments({ key: 'b' })).toBe(1);
    });

    it('should abort every write when the callback throws', async () => {
      const session = getTestClient().startSession();

      try {
        await expect(
          session.withTransaction(async () => {
            await first.insertOne({ key: 'a' }, { session });
            await second.insertOne({ key: 'b' }, { session });
            throw new Error('boom');
          }),
        ).rejects.toThrow('boom');
      } finally {
        await session.endSession();
      }

      expect(await first.countDocuments()).toBe(0);
      expect(await second.countDocuments()).toBe(0);
    });

    it('should hide uncommitted writes from reads outside the session', async () => {
      const session = getTestClient().startSession();
      let seenOutside = -1;

      try {
        await session.withTransaction(async () => {
          await first.insertOne({ key: 'a' }, { session });
          seenOutside = await first.countDocuments({ key: 'a' });
        });
      } finally {
        await session.endSession();
      }

      expect(seenOutside).toBe(0);
    });
  });
});
