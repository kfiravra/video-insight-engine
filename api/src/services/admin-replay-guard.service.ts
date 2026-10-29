import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';

import { ServiceUnavailableError } from '../utils/errors.js';

const KEY_PREFIX = 'vie:api:admin-actor-sig:';

/**
 * Lets each signed `X-Admin-Actor` header of a mutation through once.
 *
 * The signature already binds the method, the target and the body, so a
 * captured header could only repeat the very same action; this closes that
 * last gap for mutations, where a repeat can cost money (a credit grant) or
 * data. The signature is recorded with `SET NX` for as long as the header
 * would still verify. Reads are not recorded: repeating them changes nothing.
 *
 * Fails closed: with Redis down (or absent) no mutation can be vouched for, so
 * `claim` throws a 503. Admin mutations are rare enough that this is cheap.
 */
export class AdminReplayGuard {
  constructor(
    private readonly redis: Pick<Redis, 'set'> | undefined,
    private readonly logger: FastifyBaseLogger,
  ) {}

  /**
   * Record `signature` for `ttlSeconds`. True the first time, false when it was
   * already claimed (a replay).
   */
  async claim(signature: string, ttlSeconds: number): Promise<boolean> {
    if (!this.redis) throw new ServiceUnavailableError('Replay protection');
    try {
      const stored = await this.redis.set(`${KEY_PREFIX}${signature}`, '1', 'EX', Math.max(1, Math.ceil(ttlSeconds)), 'NX');
      return stored === 'OK';
    } catch (err) {
      this.logger.error({ err }, 'admin_replay_guard_unavailable');
      throw new ServiceUnavailableError('Replay protection');
    }
  }
}
