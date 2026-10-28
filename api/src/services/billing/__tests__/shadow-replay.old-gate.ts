import { CostMonitorService, type CostReservation } from '../../cost-monitor.service.js';
import { UserCostRepository, getUtcDateKey } from '../../../repositories/user-cost.repository.js';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import type { BillingShadowDecision } from '@vie/types';

// The live old dollar gate for the P1.11 replay harness: the real
// CostMonitorService, with its reservations and refunds attributed to the
// scenario run that caused them. P3.2 deletes the old gate; this file goes
// with it and the harness reads the scenarios' recorded `oldDecision`.

interface RunLedger {
  reserved: CostReservation[];
  refunded: number;
}

export class OldGateRecorder {
  readonly service: CostMonitorService;
  private readonly runs = new Map<string, RunLedger>();
  private readonly owners = new Map<CostReservation, string>();
  private pending: string[] = [];

  constructor(private readonly db: Db, logger: FastifyBaseLogger) {
    this.service = new CostMonitorService(db, logger, new UserCostRepository(db));
    this.wrap();
  }

  /** The runs the next reservations belong to, in order (one per submit, one per playlist item). */
  expectRuns(runs: string[]): void {
    this.pending = [...runs];
  }

  reset(): void {
    this.runs.clear();
    this.owners.clear();
    this.pending = [];
  }

  /** What the old gate did for `run`: admitted = reserved, charged = a reservation was kept. */
  decisionFor(run: string): BillingShadowDecision {
    const ledger = this.runs.get(run);
    if (!ledger || ledger.reserved.length === 0) return { admitted: false, charged: false };
    return { admitted: true, charged: ledger.reserved.length > ledger.refunded };
  }

  /** Put the user's UTC day at `usd` spent — both in userCosts and in llm_usage, so a reconcile keeps it. */
  async seedDaySpend(userId: string, usd: number): Promise<void> {
    await this.db.collection('llm_usage').insertOne({ user_id: userId, request_id: `seed-${userId}`, cost_usd: usd, timestamp: new Date() });
    await this.service.reconcileUserDay(userId, getUtcDateKey());
  }

  private runLedger(run: string): RunLedger {
    const existing = this.runs.get(run);
    if (existing) return existing;
    const created: RunLedger = { reserved: [], refunded: 0 };
    this.runs.set(run, created);
    return created;
  }

  private wrap(): void {
    const reserve = this.service.reserveUserCost.bind(this.service);
    const refund = this.service.refundReservation.bind(this.service);
    this.service.reserveUserCost = async (userId, tier) => {
      const run = this.pending.shift() ?? 'unattributed';
      // A refusal (DailyLimitReachedError) propagates and leaves the run unreserved.
      const reservation = await reserve(userId, tier);
      if (reservation) {
        this.runLedger(run).reserved.push(reservation);
        this.owners.set(reservation, run);
      }
      return reservation;
    };
    this.service.refundReservation = async (reservation) => {
      await refund(reservation);
      const run = reservation ? this.owners.get(reservation) : undefined;
      if (run) this.runLedger(run).refunded += 1;
    };
  }
}
