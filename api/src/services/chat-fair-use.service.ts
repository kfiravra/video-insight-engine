import { randomUUID } from 'node:crypto';
import type { FastifyBaseLogger, FastifyRequest } from 'fastify';
import type { Redis } from 'ioredis';
import type { UsageSummary } from '@vie/types';
import { ChatLimitReachedError } from '../utils/errors.js';

const WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * Rolling-window check in one step. The set holds one member per ADMITTED
 * message, scored by its time in ms: entries older than the window are dropped,
 * and a message is admitted only while fewer than `limit` remain. A rejected
 * attempt is not recorded, so it never prolongs the wait; when the set is full,
 * the oldest score is the moment the first slot frees (oldest + window).
 * Returns {admitted, count, oldestScore}.
 */
const CONSUME_LUA = `
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, count, tonumber(oldest[2])}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return {1, count + 1, 0}
`;

export function chatFairUseKey(userId: string): string {
  return `vie:api:chat-fair-use:${userId}`;
}

/** The part of BillingService this guard needs: which plan the account is on. */
export interface PlanLookup {
  summary(userId: string): Promise<Pick<UsageSummary, 'plan'>>;
}

/**
 * Fair use for chat on free accounts (Q7, D6). Public contract, extracted so
 * the container can wire the no-op below when there is no Redis.
 */
export interface IChatFairUse {
  /** Count one chat message; throws ChatLimitReachedError (429) once a free account is out. */
  consume(userId: string): Promise<void>;
}

export const noOpChatFairUse: IChatFairUse = {
  consume: async () => {},
};

interface WindowResult {
  admitted: boolean;
  oldestMs: number;
}

function isWindowReply(reply: unknown): reply is [number, number, number] {
  return (
    Array.isArray(reply) &&
    reply.length === 3 &&
    reply.every((part) => typeof part === 'number')
  );
}

/**
 * A free account may send `limitPerDay` chat messages in any rolling 24 hours;
 * Pro (and any other non-free plan) is unlimited. Chat cost is never charged to
 * the minutes ledger: this guard only reads the plan.
 *
 * A sorted set rather than a counter: a counter's fixed window lets a user send
 * the limit just before it resets and the limit again just after, and cannot say
 * when the next message is allowed. The set holds at most `limitPerDay` short
 * members per free user.
 *
 * Fail-open, like the other guards: a Redis or plan-lookup error logs a warning
 * and lets the message through. A limit of 0 or less disables the guard.
 */
export class ChatFairUseService implements IChatFairUse {
  constructor(
    private readonly redis: Redis,
    private readonly plans: PlanLookup,
    private readonly limitPerDay: number,
    private readonly logger: FastifyBaseLogger,
    private readonly clock: () => number = Date.now,
  ) {}

  async consume(userId: string): Promise<void> {
    if (this.limitPerDay <= 0) return;
    if (!(await this.isFreePlan(userId))) return;

    const result = await this.record(userId);
    if (result === null || result.admitted) return;

    throw new ChatLimitReachedError(this.limitPerDay, new Date(result.oldestMs + WINDOW_MS));
  }

  /** False on a lookup failure: an unknown plan is not charged against the limit. */
  private async isFreePlan(userId: string): Promise<boolean> {
    try {
      const { plan } = await this.plans.summary(userId);
      return plan.id === 'free';
    } catch (err) {
      this.logger.warn({ userId, err }, 'chat_fair_use_plan_lookup_failed');
      return false;
    }
  }

  private async record(userId: string): Promise<WindowResult | null> {
    const now = this.clock();
    try {
      const reply: unknown = await this.redis.eval(
        CONSUME_LUA,
        1,
        chatFairUseKey(userId),
        now,
        WINDOW_MS,
        this.limitPerDay,
        `${now}:${randomUUID()}`,
      );
      if (isWindowReply(reply)) return { admitted: reply[0] === 1, oldestMs: reply[2] };
      this.logger.warn({ userId }, 'chat_fair_use_unexpected_reply');
    } catch (err) {
      this.logger.warn({ userId, err }, 'chat_fair_use_check_failed');
    }
    return null;
  }
}

/** preHandler for the chat-message routes; run it after `fastify.authenticate`. */
export function chatFairUseHook(guard: IChatFairUse): (req: FastifyRequest) => Promise<void> {
  return async (req) => {
    await guard.consume(req.user.userId);
  };
}
