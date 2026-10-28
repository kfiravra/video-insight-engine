import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { mockLogger } from '../../../test/setup.js';
import { IntervalJob } from '../interval-job.js';
import { RedisRepairLock, BILLING_REPAIR_LOCK_KEY } from '../repair-lock.js';
import type { Redis } from 'ioredis';

const logger = mockLogger as unknown as FastifyBaseLogger;

describe('IntervalJob', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should run after the first delay and then every interval', async () => {
    const task = vi.fn().mockResolvedValue(undefined);
    const job = new IntervalJob('test', task, 1000, logger);

    job.start(100);
    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(2000);

    expect(task).toHaveBeenCalledTimes(3);
    await job.stop();
  });

  it('should never overlap a slow run with the next one', async () => {
    let active = 0;
    let maxActive = 0;
    const task = vi.fn(async () => {
      active++;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, 2500));
      active--;
    });
    const job = new IntervalJob('test', task, 1000, logger);

    job.start(0);
    await vi.advanceTimersByTimeAsync(10_000);

    expect(maxActive).toBe(1);
    await job.stop();
  });

  it('should log a failing run and keep the schedule', async () => {
    const task = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue(undefined);
    const job = new IntervalJob('test', task, 1000, logger);

    job.start(0);
    await vi.advanceTimersByTimeAsync(1000);

    expect(task).toHaveBeenCalledTimes(2);
    expect(mockLogger.error).toHaveBeenCalledWith(expect.objectContaining({ job: 'test' }), 'interval_job_failed');
    await job.stop();
  });

  it('should wait in stop for the run in flight and schedule nothing after it', async () => {
    let finished = false;
    const task = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      finished = true;
    });
    const job = new IntervalJob('test', task, 1000, logger);
    job.start(0);
    await vi.advanceTimersByTimeAsync(1);

    const stopped = job.stop();
    await vi.advanceTimersByTimeAsync(500);
    await stopped;
    await vi.advanceTimersByTimeAsync(5000);

    expect(finished).toBe(true);
    expect(task).toHaveBeenCalledTimes(1);
  });
});

describe('RedisRepairLock', () => {
  let redis: { set: ReturnType<typeof vi.fn>; eval: ReturnType<typeof vi.fn> };
  let lock: RedisRepairLock;

  beforeEach(() => {
    vi.clearAllMocks();
    redis = { set: vi.fn(), eval: vi.fn().mockResolvedValue(1) };
    lock = new RedisRepairLock(redis as unknown as Redis, 300, logger);
  });

  it('should take the lock with SET NX EX and return its token', async () => {
    redis.set.mockResolvedValue('OK');

    const token = await lock.acquire();

    expect(token).toEqual(expect.any(String));
    expect(redis.set).toHaveBeenCalledWith(BILLING_REPAIR_LOCK_KEY, token, 'EX', 300, 'NX');
  });

  it('should return null while another process holds it', async () => {
    redis.set.mockResolvedValue(null);

    expect(await lock.acquire()).toBeNull();
  });

  it('should fail closed (skip the run) when Redis errors', async () => {
    redis.set.mockRejectedValue(new Error('ECONNREFUSED'));

    expect(await lock.acquire()).toBeNull();
    expect(mockLogger.warn).toHaveBeenCalledWith(expect.any(Object), 'billing_repair_lock_unavailable');
  });

  it('should release only its own token', async () => {
    await lock.release('token-1');

    expect(redis.eval).toHaveBeenCalledWith(expect.stringContaining("redis.call('get', KEYS[1]) == ARGV[1]"), 1, BILLING_REPAIR_LOCK_KEY, 'token-1');
  });
});
