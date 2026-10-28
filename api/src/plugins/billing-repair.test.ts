import { describe, it, expect, vi } from 'vitest';
import Fastify from 'fastify';
import { billingRepairPlugin } from './billing-repair.js';
import type { BillingRepairPluginOptions } from './billing-repair.js';

function appWith(options: BillingRepairPluginOptions, drain: () => Promise<void> = async () => undefined) {
  const app = Fastify({ logger: false });
  const runOnce = vi.fn().mockResolvedValue('locked');
  const drainSpy = vi.fn(drain);
  app.decorate('container', {
    billingRepairService: { runOnce },
    billingShadowService: { drain: drainSpy },
  } as never);
  void app.register(billingRepairPlugin, options);
  return { app, runOnce, drain: drainSpy };
}

const waitFor = async (check: () => boolean): Promise<void> => {
  for (let i = 0; i < 100 && !check(); i++) await new Promise((resolve) => setTimeout(resolve, 5));
};

describe('billingRepairPlugin', () => {
  it('should start the repair job once the app is ready', async () => {
    const { app, runOnce } = appWith({ enabled: true, intervalMs: 60_000, firstRunDelayMs: 0 });

    await app.ready();
    await waitFor(() => runOnce.mock.calls.length > 0);

    expect(runOnce).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it('should not schedule anything when disabled', async () => {
    const { app, runOnce } = appWith({ enabled: false, intervalMs: 60_000, firstRunDelayMs: 0 });

    await app.ready();
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(runOnce).not.toHaveBeenCalled();
    await app.close();
  });

  it('should drain the shadow ledger on close', async () => {
    const { app, drain } = appWith({ enabled: false });
    await app.ready();

    await app.close();

    expect(drain).toHaveBeenCalledTimes(1);
  });

  it('should finish closing when the drain itself fails', async () => {
    const { app } = appWith({ enabled: false }, async () => {
      throw new Error('mongo down');
    });
    await app.ready();

    await expect(app.close()).resolves.toBeUndefined();
  });

  it('should give up on a drain that outlasts its timeout, so shutdown is bounded', async () => {
    const { app } = appWith({ enabled: false, shadowDrainTimeoutMs: 20 }, () => new Promise(() => undefined));
    await app.ready();
    const started = Date.now();

    await app.close();

    expect(Date.now() - started).toBeLessThan(1000);
  });
});
