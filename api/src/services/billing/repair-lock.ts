import { randomUUID } from 'node:crypto';
import type { Redis } from 'ioredis';
import type { FastifyBaseLogger } from 'fastify';

export const BILLING_REPAIR_LOCK_KEY = 'vie:api:billing-repair';

const RELEASE_IF_OWNER_LUA = `
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
`;

/** One repair run at a time across vie-api processes. `acquire` returns a token, or null when someone else runs. */
export interface RepairLock {
  acquire(): Promise<string | null>;
  release(token: string): Promise<void>;
}

/**
 * `SET vie:api:billing-repair <token> NX EX <ttl>`, released with a
 * compare-and-delete so a run that outlived its TTL never frees a newer
 * holder's lock. Fails closed: when Redis errors, the run is skipped — every
 * step is idempotent and the next tick retries, so skipping costs nothing,
 * while running unlocked could overlap another process.
 */
export class RedisRepairLock implements RepairLock {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds: number,
    private readonly logger: FastifyBaseLogger,
  ) {}

  async acquire(): Promise<string | null> {
    const token = randomUUID();
    try {
      const result = await this.redis.set(BILLING_REPAIR_LOCK_KEY, token, 'EX', this.ttlSeconds, 'NX');
      return result === 'OK' ? token : null;
    } catch (err) {
      this.logger.warn({ err }, 'billing_repair_lock_unavailable');
      return null;
    }
  }

  async release(token: string): Promise<void> {
    try {
      await this.redis.eval(RELEASE_IF_OWNER_LUA, 1, BILLING_REPAIR_LOCK_KEY, token);
    } catch (err) {
      // The TTL frees it; the next tick at worst waits one interval.
      this.logger.warn({ err }, 'billing_repair_lock_release_failed');
    }
  }
}

/** No Redis (tests, a bare process): only this process runs the job, and IntervalJob never overlaps itself. */
export const singleProcessRepairLock: RepairLock = {
  acquire: async () => randomUUID(),
  release: async () => undefined,
};
