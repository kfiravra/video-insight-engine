import type { FastifyBaseLogger } from 'fastify';
import type { Redis } from 'ioredis';
import { RateLimitedError } from '../../utils/errors.js';

const WINDOW_SECONDS = 60 * 60;

/**
 * INCR and the window's EXPIRE in one step, so a crash can never leave a
 * counter with no TTL (that user would be blocked for good). The window opens
 * at the first submission of the hour. A key found without a TTL is repaired.
 * Returns {count, secondsLeft}.
 */
const CONSUME_LUA = `
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if count == 1 or ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
`;

export function submitBurstKey(userId: string): string {
  return `vie:api:submit-burst:${userId}`;
}

/**
 * Abuse guard for the submit paths — not a billing limit, so it knows nothing
 * about minutes or plans. Public contract, extracted so the container can wire
 * the no-op below when there is no Redis.
 */
export interface ISubmitBurstLimiter {
  /** Count one submission; throws RateLimitedError (429) once the hour's bucket is full. */
  consume(userId: string): Promise<void>;
}

export const noOpSubmitBurstLimiter: ISubmitBurstLimiter = {
  consume: async () => {},
};

interface BurstHit {
  count: number;
  secondsLeft: number;
}

function isBurstHit(reply: unknown): reply is [number, number] {
  return (
    Array.isArray(reply) &&
    reply.length === 2 &&
    typeof reply[0] === 'number' &&
    typeof reply[1] === 'number'
  );
}

/**
 * One fixed one-hour bucket per user, shared by every route that submits work
 * (POST /api/videos, the assistant's generate route, playlist import — one
 * playlist counts once however many videos it holds). Rejected attempts count
 * too but never extend the window.
 *
 * Fail-open: a Redis error logs a warning and lets the submission through,
 * like the dispatch guard and the rate-limit plugin. A limit of 0 or less
 * disables the guard.
 */
export class SubmitBurstLimiter implements ISubmitBurstLimiter {
  constructor(
    private readonly redis: Redis,
    private readonly limitPerHour: number,
    private readonly logger: FastifyBaseLogger,
  ) {}

  async consume(userId: string): Promise<void> {
    if (this.limitPerHour <= 0) return;

    const hit = await this.record(userId);
    if (hit === null || hit.count <= this.limitPerHour) return;

    throw new RateLimitedError(
      `Too many submissions in the last hour. Try again in ${minutesLeft(hit.secondsLeft)} min.`,
    );
  }

  private async record(userId: string): Promise<BurstHit | null> {
    try {
      const reply: unknown = await this.redis.eval(
        CONSUME_LUA,
        1,
        submitBurstKey(userId),
        WINDOW_SECONDS,
      );
      if (isBurstHit(reply)) return { count: reply[0], secondsLeft: reply[1] };
      this.logger.warn({ userId }, 'submit_burst_unexpected_reply');
    } catch (err) {
      this.logger.warn({ userId, err }, 'submit_burst_check_failed');
    }
    return null;
  }
}

/** Whole minutes until the window closes, rounded up, never below 1. */
function minutesLeft(secondsLeft: number): number {
  return Math.max(1, Math.ceil(secondsLeft / 60));
}
