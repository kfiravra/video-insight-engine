import { staleHoldAction } from './maintenance-ops.js';
import type { FastifyBaseLogger } from 'fastify';
import type { BillingService } from './billing.service.js';
import type { RepairLock } from './repair-lock.js';
import type { BillingHoldDocument } from '../../repositories/billing.repository.js';
import type { BillingMaintenanceRepository } from '../../repositories/billing-maintenance.repository.js';
import type { LlmAlertsRepository } from '../../repositories/llm-alerts.repository.js';
import type { LlmUsageRepository } from '../../repositories/llm-usage.repository.js';
import type { UserDeletionService } from '../user-deletion.service.js';

// P1.6 — the billing repair job, one run per tick under a Redis lock:
//   1. settle / release holds older than BILLING_HOLD_MAX_AGE_HOURS whose run
//      is terminal, missing, unstamped or stalled
//   2. expire lots past their expiry (`expire` entries)
//   3. apply due recurring grants (a lot that expires at the next run)
//   4. rebuild changed accounts' projections from the ledger; drift → warn + llm_alerts
//   5. drop llm_usage.user_id on rows older than 90 days (docs/PRIVACY.md)
//   6. the GDPR hard-delete sweep (docs/PRIVACY.md's 30-day promise)
// Each step is idempotent and isolated: one failing step (or item) is logged
// and the others still run.

const BATCH = 200;
const LLM_USAGE_BATCH = 1000;
const PRIVACY_WINDOW_MS = 90 * 24 * 60 * 60 * 1000;
const DRIFT_ALERT_COOLDOWN_MS = 24 * 60 * 60 * 1000;
const FIRST_DRIFT_LOOKBACK_MS = 24 * 60 * 60 * 1000;

export interface BillingRepairDeps {
  billing: BillingService;
  maintenance: BillingMaintenanceRepository;
  llmUsage: LlmUsageRepository;
  alerts: LlmAlertsRepository;
  deletions: Pick<UserDeletionService, 'runScheduledDeletions'>;
  lock: RepairLock;
  logger: FastifyBaseLogger;
  holdMaxAgeHours: number;
  /** How long a recurring lot outlives its month, so the next tick grants the new one first (I3). */
  recurringLotGraceMs?: number;
  clock?: () => Date;
}

export interface RepairReport {
  holdsSettled: number;
  holdsReleased: number;
  lotMinutesExpired: number;
  recurringLots: number;
  driftAccounts: number;
  llmUsagePseudonymised: number;
  deletionsProcessed: number;
  failedSteps: string[];
}

export class BillingRepairService {
  /** Accounts changed at or after this were not drift-checked yet. */
  private driftCursor: Date | null = null;

  constructor(private readonly deps: BillingRepairDeps) {}

  private now(): Date {
    return this.deps.clock ? this.deps.clock() : new Date();
  }

  /** One repair run; 'locked' when another process holds the lock. */
  async runOnce(): Promise<RepairReport | 'locked'> {
    const token = await this.deps.lock.acquire();
    if (!token) return 'locked';
    const now = this.now();
    const report: RepairReport = {
      holdsSettled: 0, holdsReleased: 0, lotMinutesExpired: 0, recurringLots: 0,
      driftAccounts: 0, llmUsagePseudonymised: 0, deletionsProcessed: 0, failedSteps: [],
    };
    try {
      await this.step('stale_holds', report, () => this.repairStaleHolds(now, report));
      await this.step('lot_expiry', report, () => this.expireLots(now, report));
      await this.step('recurring_grants', report, () => this.applyRecurringGrants(now, report));
      await this.step('drift', report, () => this.checkDrift(now, report));
      await this.step('llm_usage_privacy', report, () => this.pseudonymiseLlmUsage(now, report));
      await this.step('gdpr_deletions', report, () => this.runDeletions(now, report));
    } finally {
      await this.deps.lock.release(token);
    }
    this.deps.logger.info({ ...report }, 'billing_repair_run');
    return report;
  }

  private async step(name: string, report: RepairReport, run: () => Promise<void>): Promise<void> {
    try {
      await run();
    } catch (err) {
      report.failedSteps.push(name);
      this.deps.logger.warn({ err, step: name }, 'billing_repair_step_failed');
    }
  }

  /** Run `fn` per item; a failing item is logged and the rest still run. */
  private async eachItem<T>(items: T[], label: string, fn: (item: T) => Promise<void>): Promise<void> {
    for (const item of items) {
      await fn(item).catch((err: unknown) => this.deps.logger.warn({ err, step: label }, 'billing_repair_item_failed'));
    }
  }

  private async repairStaleHolds(now: Date, report: RepairReport): Promise<void> {
    const maxAgeMs = this.deps.holdMaxAgeHours * 60 * 60 * 1000;
    const cutoff = new Date(now.getTime() - maxAgeMs);
    const accounts = await this.deps.maintenance.listAccountsWithHoldsBefore(cutoff, BATCH);
    const stale = accounts.flatMap((account) => account.holds.filter((hold) => hold.createdAt < cutoff));
    await this.eachItem(stale, 'stale_holds', (hold) => this.repairHold(hold, now, maxAgeMs, report));
  }

  private async repairHold(hold: BillingHoldDocument, now: Date, maxAgeMs: number, report: RepairReport): Promise<void> {
    const run = hold.youtubeId ? await this.deps.maintenance.findRun(hold.youtubeId, hold.requestId) : null;
    const action = staleHoldAction(run, now, maxAgeMs, hold.createdAt);
    if (action === 'keep') return;
    const costUsd = await this.deps.llmUsage.sumCostByRequestId(run?.requestId ?? hold.requestId);
    if (action === 'settle') {
      const outcome = await this.deps.billing.settle(hold.requestId, { costUsd });
      if (outcome.status === 'debited') report.holdsSettled++;
      return;
    }
    const outcome = await this.deps.billing.release(hold.requestId, 'stale', { costUsd });
    if (outcome.status === 'released') report.holdsReleased++;
  }

  private async expireLots(now: Date, report: RepairReport): Promise<void> {
    const accountIds = await this.deps.maintenance.listAccountIdsWithExpiredLots(now, BATCH);
    await this.eachItem(accountIds, 'lot_expiry', async (accountId) => {
      report.lotMinutesExpired += await this.deps.billing.expireLots(accountId);
    });
  }

  private async applyRecurringGrants(now: Date, report: RepairReport): Promise<void> {
    const grants = await this.deps.maintenance.listDueRecurringGrants(now, BATCH);
    await this.eachItem(grants, 'recurring_grants', async (grant) => {
      const lot = await this.deps.billing.applyRecurringGrant(grant.accountId, grant._id, this.deps.recurringLotGraceMs ?? 0);
      if (lot) report.recurringLots++;
    });
  }

  private async checkDrift(now: Date, report: RepairReport): Promise<void> {
    const since = this.driftCursor ?? new Date(now.getTime() - FIRST_DRIFT_LOOKBACK_MS);
    const accounts = await this.deps.maintenance.listAccountsUpdatedSince(since, BATCH);
    await this.eachItem(accounts, 'drift', async (account) => {
      const check = await this.deps.billing.verifyAccountById(account._id);
      if (check.ok) return;
      report.driftAccounts++;
      const accountId = account._id.toHexString();
      this.deps.logger.warn({ accountId, drift: check.drift }, 'billing_drift');
      await this.deps.alerts.recordOnce(
        {
          type: 'billing_drift', severity: 'warning', service: 'vie-api', timestamp: now,
          message: `Billing account ${accountId} no longer equals its ledger (${check.drift.length} field(s))`,
          accountId, ownerId: account.ownerId.toHexString(), drift: check.drift,
        },
        { accountId },
        new Date(now.getTime() - DRIFT_ALERT_COOLDOWN_MS),
      );
    });
    // A full batch may have left accounts behind: continue from the last one seen.
    this.driftCursor = accounts.length === BATCH ? accounts[accounts.length - 1].updatedAt : now;
  }

  private async pseudonymiseLlmUsage(now: Date, report: RepairReport): Promise<void> {
    const cutoff = new Date(now.getTime() - PRIVACY_WINDOW_MS);
    report.llmUsagePseudonymised = await this.deps.llmUsage.unsetUserIdBefore(cutoff, LLM_USAGE_BATCH);
  }

  private async runDeletions(now: Date, report: RepairReport): Promise<void> {
    const result = await this.deps.deletions.runScheduledDeletions(now);
    report.deletionsProcessed = result.processed;
  }
}
