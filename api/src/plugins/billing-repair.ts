import { FastifyInstance } from 'fastify';
import fp from 'fastify-plugin';
import { config } from '../config.js';
import { IntervalJob } from '../services/billing/interval-job.js';

/** The first run waits this long after boot (never longer than one interval), off the health-check window. */
const FIRST_RUN_DELAY_MS = 30_000;

export interface BillingRepairPluginOptions {
  /** Defaults to BILLING_REPAIR_ENABLED. */
  enabled?: boolean;
  intervalMs?: number;
  firstRunDelayMs?: number;
}

/**
 * Schedules the billing repair job (P1.6) once the app is ready — after the
 * mongodb plugin's onReady built the indexes — and, on close, stops it
 * (waiting for a run in flight). Ledger writes are on the request path since
 * P3.1, so there is nothing else to drain.
 */
async function billingRepairSetup(fastify: FastifyInstance, options: BillingRepairPluginOptions): Promise<void> {
  const {
    enabled = config.BILLING_REPAIR_ENABLED,
    intervalMs = config.BILLING_REPAIR_INTERVAL_SECONDS * 1000,
    firstRunDelayMs = Math.min(FIRST_RUN_DELAY_MS, intervalMs),
  } = options;
  let job: IntervalJob | null = null;

  fastify.addHook('onReady', async () => {
    if (!enabled) return;
    const repair = fastify.container.billingRepairService;
    job = new IntervalJob('billing-repair', async () => {
      await repair.runOnce();
    }, intervalMs, fastify.log);
    job.start(firstRunDelayMs);
    fastify.log.info({ intervalMs }, 'billing_repair_scheduled');
  });

  fastify.addHook('onClose', async () => {
    await job?.stop();
  });
}

export const billingRepairPlugin = fp(billingRepairSetup, { name: 'billing-repair-plugin' });
