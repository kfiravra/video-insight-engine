import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { mockLogger } from '../../../test/setup.js';
import { ShadowRunner } from '../shadow-runner.js';

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

describe('ShadowRunner', () => {
  let runner: ShadowRunner;

  beforeEach(() => {
    vi.clearAllMocks();
    runner = new ShadowRunner(mockLogger as unknown as FastifyBaseLogger);
  });

  it('should run steps for one key in order even when the first is slower', async () => {
    const order: string[] = [];

    runner.enqueue('req-1', 'hold', async () => {
      await tick();
      order.push('hold');
    });
    runner.enqueue('req-1', 'release', async () => {
      order.push('release');
    });
    await runner.drain();

    expect(order).toEqual(['hold', 'release']);
  });

  it('should not order steps across different keys', async () => {
    const order: string[] = [];

    runner.enqueue('slow', 'a', async () => {
      await tick();
      order.push('slow');
    });
    runner.enqueue('fast', 'b', async () => {
      order.push('fast');
    });
    await runner.drain();

    expect(order).toEqual(['fast', 'slow']);
  });

  it('should log a failing step at warn and keep running the chain', async () => {
    const after = vi.fn();

    runner.enqueue('req-1', 'hold', async () => {
      throw new Error('mongo down');
    });
    runner.enqueue('req-1', 'release', async () => after());
    await runner.drain();

    expect(after).toHaveBeenCalledTimes(1);
    expect(mockLogger.warn).toHaveBeenCalledWith(expect.objectContaining({ key: 'req-1', step: 'hold' }), expect.any(String));
  });

  it('should return before the step runs', () => {
    const step = vi.fn(async () => undefined);

    runner.enqueue('req-1', 'hold', step);

    expect(step).not.toHaveBeenCalled();
    expect(runner.pending).toBe(1);
  });

  it('should wait in drain for steps queued by other steps', async () => {
    const nested = vi.fn(async () => undefined);

    runner.enqueue('video:1', 'stamp', async () => {
      runner.enqueue('req-1', 'settle', nested);
    });
    await runner.drain();

    expect(nested).toHaveBeenCalledTimes(1);
    expect(runner.pending).toBe(0);
  });
});
