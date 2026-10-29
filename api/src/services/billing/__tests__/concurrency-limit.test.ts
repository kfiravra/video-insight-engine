import { describe, it, expect, vi } from 'vitest';
import { ConcurrencyLimit } from '../concurrency-limit.js';
import { BillingGate } from '../billing-gate.js';
import type { BillingService } from '../billing.service.js';

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

describe('ConcurrencyLimit', () => {
  it('should run at most `limit` tasks at once and every task eventually', async () => {
    const limit = new ConcurrencyLimit(2);
    let active = 0;
    let maxActive = 0;
    const task = async (n: number) => {
      active++;
      maxActive = Math.max(maxActive, active);
      await tick();
      active--;
      return n;
    };

    const results = await Promise.all([1, 2, 3, 4, 5].map((n) => limit.run(() => task(n))));

    expect([maxActive, results]).toEqual([2, [1, 2, 3, 4, 5]]);
  });

  it('should free its slot when a task fails', async () => {
    const limit = new ConcurrencyLimit(1);

    await expect(limit.run(async () => { throw new Error('boom'); })).rejects.toThrow('boom');

    expect(await limit.run(async () => 'next')).toBe('next');
  });

  it('should refuse a limit below 1', () => {
    expect(() => new ConcurrencyLimit(0)).toThrow();
  });
});

describe('BillingGate.durationOf (I9)', () => {
  it('should run no more than 3 metadata pre-flights at once', async () => {
    let active = 0;
    let maxActive = 0;
    const metadata = {
      getVideoMetadata: vi.fn(async () => {
        active++;
        maxActive = Math.max(maxActive, active);
        await tick();
        active--;
        return { duration: 600 };
      }),
    };
    const gate = new BillingGate({} as BillingService, metadata);

    await Promise.all(Array.from({ length: 10 }, (_, i) => gate.durationOf(`video${i}`)));

    expect(metadata.getVideoMetadata).toHaveBeenCalledTimes(10);
    expect(maxActive).toBe(3);
  });

  it('should not call the pre-flight when the length is known', async () => {
    const metadata = { getVideoMetadata: vi.fn() };

    expect(await new BillingGate({} as BillingService, metadata).durationOf('v', 120)).toBe(120);
    expect(metadata.getVideoMetadata).not.toHaveBeenCalled();
  });
});
