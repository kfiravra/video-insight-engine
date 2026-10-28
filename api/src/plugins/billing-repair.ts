import { setTimeout as sleep } from 'node:timers/promises';
import { FastifyInstance } from 'fastify';
import fp from 'fastify-plugin';
import { config } from '../config.js';
import { IntervalJob } from '../services/billing/interval-job.js';

/** The first run waits this long after boot (never longer than one interval), off the health-check window. */
const FIRST_RUN_DELAY_MS = 30_000;
/** How long shutdown waits for queued shadow-ledger steps before giving up on them. */
const SHADOW_DRAIN_TIMEOUT_MS = 5_000;

export interface BillingRepairPluginOptions {
  /** Defaults to BILLING_REPAIR_ENABLED. */
  enabled?: boolean;
  intervalMs?: number;
  firstRunDelayMs?: number;
  shadowDrainTimeoutMs?: number;
}

/**
 * Schedules the billing repair job (P1.6) once the app is ready — after the
 * mongodb plugin's onReady built the indexes — and, on close, stops it
 * (waiting for a run in flight) and drains the shadow ledger's queued steps
 * for a bounded time. Steps still queued after that are dropped; the repair
 * job's stale-hold rule settles or releases what they leave open.
 */
async function billingRepairSetup(fastify: FastifyInstance, options: BillingRepairPluginOptions): Promise<void> {
  const {
    enabled = config.BILLING_REPAIR_ENABLED,
    intervalMs = config.BILLING_REPAIR_INTERVAL_SECONDS * 1000,
    firstRunDelayMs = Math.min(FIRST_RUN_DELAY_MS, intervalMs),
    shadowDrainTimeoutMs = SHADOW_DRAIN_TIMEOUT_MS,
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
    try {
      const drained = await Promise.race([
        fastify.container.billingShadowService.drain().then(() => true),
        sleep(shadowDrainTimeoutMs, false, { ref: false }),
      ]);
      if (!drained) fastify.log.warn({ timeoutMs: shadowDrainTimeoutMs }, 'billing_shadow_drain_timed_out');
    } catch (err) {
      // Shutdown must finish; the repair job's stale-hold rule covers dropped steps.
      fastify.log.warn({ err }, 'billing_shadow_drain_failed');
    }
  });
}

export const billingRepairPlugin = fp(billingRepairSetup, { name: 'billing-repair-plugin' });
