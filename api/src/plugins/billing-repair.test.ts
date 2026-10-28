import { describe, it, expect, vi } from 'vitest';
import Fastify from 'fastify';
import { billingRepairPlugin } from './billing-repair.js';
import type { BillingRepairPluginOptions } from './billing-repair.js';

function appWith(options: BillingRepairPluginOptions) {
  const app = Fastify({ logger: false });
  const runOnce = vi.fn().mockResolvedValue('locked');
  app.decorate('container', { billingRepairService: { runOnce } } as never);
  void app.register(billingRepairPlugin, options);
  return { app, runOnce };
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

  it('should stop the repair job on close, so no run starts after shutdown', async () => {
    const { app, runOnce } = appWith({ enabled: true, intervalMs: 10, firstRunDelayMs: 0 });
    await app.ready();
    await waitFor(() => runOnce.mock.calls.length > 0);

    await app.close();
    const runsAtClose = runOnce.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(runOnce).toHaveBeenCalledTimes(runsAtClose);
  });
});
