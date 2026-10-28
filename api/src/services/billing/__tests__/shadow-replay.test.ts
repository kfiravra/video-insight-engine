/**
 * P1.11 — the shadow gate (Kfir 2026-10-27): replay scripted submits, cached /
 * attached serves, failures, refusals and retries through the real submit,
 * playlist and terminal-status paths with the old dollar gate and the minutes
 * ledger side by side. Zero `disagree`; every `policy_delta` must be the one
 * the scenario names. Also re-judges every real `billingShadow` row in the
 * dev database when it is reachable (read-only).
 *
 * The one switch is MODE. P1/P3 shadow: the live old gate is asserted against
 * each scenario's recorded `oldDecision` and the shadow rows against the
 * ledger. After P3.2 deletes the old gate: set MODE = 'enforced' (the
 * recorded `oldDecision` is the old side, the ledger decides on the request
 * path and refusal reasons come from the submit's error) and rewire
 * shadow-replay.harness.ts to the enforced services; the scenarios and the
 * assertions below stay as they are.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient } from 'mongodb';
import type { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { POLICY_DELTAS, classifyShadow, errorReason } from '../shadow-policy.js';
import { ReplayHarness } from './shadow-replay.harness.js';
import { REPLAY_SCENARIOS } from './shadow-replay.scenarios.js';
import type { BillingShadowService } from '../billing-shadow.service.js';
import type { CostMonitorService } from '../../cost-monitor.service.js';
import type { RunResult } from './shadow-replay.harness.js';
import type { AccountKind, PolicyId, ReplayExpectation, ReplayScenario } from './shadow-replay.scenarios.js';
import type { BillingShadowDocument } from '../../../repositories/billing-shadow.repository.js';

const MODE: 'shadow' | 'enforced' = 'shadow';

const DEV_MONGODB_URI =
  process.env.BILLING_REPLAY_DEV_MONGODB_URI ?? 'mongodb://localhost:27017/video-insight-engine?directConnection=true';

function policyRefOf(policy: PolicyId | undefined): string | undefined {
  if (!policy) return undefined;
  const delta = POLICY_DELTAS.find((candidate) => candidate.id === policy);
  if (!delta) throw new Error(`unknown policy id ${policy}`);
  return delta.policyRef;
}

/** The shadow row must say what the old gate and the ledger actually did. */
function assertShadowRow(where: string, expectation: ReplayExpectation, result: RunResult): void {
  const { row } = result;
  expect(result.liveOld, `${where}: the live old gate no longer matches the recorded oldDecision`).toEqual(expectation.oldDecision);
  expect(row, `${where}: no billingShadow row`).not.toBeNull();
  expect(row?.path, `${where}: path`).toBe(expectation.path);
  expect(row?.oldDecision, `${where}: stored oldDecision`).toEqual(expectation.oldDecision);
  // A run the old gate refused never ran: the ledger only quoted, so it must show nothing.
  const ledgerShows = expectation.path === 'refused'
    ? { admitted: false, charged: false }
    : { admitted: row?.newDecision.admitted, charged: row?.newDecision.charged };
  expect(result.ledger, `${where}: ledger entries vs shadow row`).toEqual(ledgerShows);
}

/** The ledger's side: the shadow row (P1, cross-checked above) or, enforced, the entries plus the submit's refusal. */
function newDecisionOf(result: RunResult): { admitted: boolean; charged: boolean; reason?: string } {
  if (MODE === 'shadow' && result.row) {
    const { admitted, charged, reason } = result.row.newDecision;
    return { admitted, charged, ...(reason && { reason }) };
  }
  const reason = errorReason(result.error);
  return { ...result.ledger, ...(reason && { reason }) };
}

function assertRun(scenario: ReplayScenario, expectation: ReplayExpectation, result: RunResult): void {
  const where = `${scenario.name} / ${expectation.run}`;
  if (MODE === 'shadow') assertShadowRow(where, expectation, result);
  const newDecision = newDecisionOf(result);
  const verdict = classifyShadow({ path: expectation.path, oldDecision: expectation.oldDecision, newDecision });
  const facts = JSON.stringify({ oldDecision: expectation.oldDecision, newDecision });
  expect(verdict.classification, `${where}: unexplained difference ${facts}`).not.toBe('disagree');
  expect(verdict.classification, `${where}: classification ${facts}`).toBe(expectation.classification);
  expect(verdict.policyRef, `${where}: policy`).toBe(policyRefOf(expectation.policy));
  if (MODE === 'shadow') {
    expect({ classification: result.row?.classification, policyRef: result.row?.policyRef }, `${where}: stored verdict`).toEqual(verdict);
  }
}

/** Every billingShadow row in the dev database, or null when it cannot be reached. */
async function readDevRows(): Promise<BillingShadowDocument[] | null> {
  const client = new MongoClient(DEV_MONGODB_URI, { serverSelectionTimeoutMS: 1500, connectTimeoutMS: 1500 });
  try {
    await client.connect();
    return await client.db().collection<BillingShadowDocument>('billingShadow').find({}).toArray();
  } catch (err) {
    console.warn(`[shadow-replay] dev Mongo not reachable (${(err as Error).message}); real-row replay skipped`);
    return null;
  } finally {
    await client.close();
  }
}

describe('shadow replay harness (P1.11)', () => {
  let app: FastifyInstance;
  let harness: ReplayHarness;

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    harness = new ReplayHarness(getTestDb());
    vi.stubGlobal('fetch', harness.fetchStub);
    const overrides: Partial<MockContainer> & { billingShadowService: BillingShadowService; costMonitorService: CostMonitorService } = {
      ...createMockContainer(),
      costMonitorService: harness.costMonitorService,
      billingShadowService: harness.shadow.shadow,
    };
    app = await buildTestApp(overrides);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    // The route's own userVideos write must not reach the app's (dev) database.
    app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: vi.fn().mockResolvedValue({ modifiedCount: 1 }) });
    app.broadcast = vi.fn();
  });

  it.each(REPLAY_SCENARIOS.map((scenario) => [scenario.name, scenario] as const))(
    'should explain every difference from the old gate: %s',
    async (_name, scenario) => {
      const results = await harness.run(app, scenario);

      for (const expectation of scenario.expect) {
        const result = results.get(expectation.run);
        expect(result, `${scenario.name} / ${expectation.run}: never ran`).toBeDefined();
        if (result) assertRun(scenario, expectation, result);
      }
      if (MODE === 'shadow') {
        const disagreeing = await getTestDb().collection('billingShadow').find({ classification: 'disagree' }).toArray();
        expect(disagreeing.map((row) => `${row.requestId} (${row.path})`), `${scenario.name}: disagreeing rows`).toEqual([]);
      }
    },
  );

  it('should cover every path, a Pro account, a playlist and all three account kinds', () => {
    const paths = new Set(REPLAY_SCENARIOS.flatMap((s) => s.expect.map((e) => e.path)));
    const accounts = new Set(REPLAY_SCENARIOS.map((s) => s.account));

    expect([...paths].sort()).toEqual(['attached', 'cached', 'completed', 'created', 'failed', 'refused', 'retry']);
    expect(['free', 'credit', 'pro', 'over_limit'].filter((kind) => !accounts.has(kind as AccountKind))).toEqual([]);
    expect(REPLAY_SCENARIOS.some((s) => s.steps.some((step) => step.kind === 'playlist'))).toBe(true);
  });

  it('should re-judge every real billingShadow row in the dev database', async (ctx) => {
    const rows = await readDevRows();
    if (rows === null) {
      ctx.skip();
      return;
    }

    const misjudged = rows.flatMap((row) => {
      const verdict = classifyShadow({ path: row.path, oldDecision: row.oldDecision, newDecision: row.newDecision });
      const stored = { classification: row.classification, policyRef: row.policyRef };
      return JSON.stringify(stored) === JSON.stringify(verdict) ? [] : [`${row.requestId}: stored ${JSON.stringify(stored)}, now ${JSON.stringify(verdict)}`];
    });
    const disagreeing = rows.filter((row) => row.classification === 'disagree').map((row) => `${row.requestId} (${row.path})`);

    expect(misjudged, 'dev rows whose stored verdict no longer matches the policy table').toEqual([]);
    expect(disagreeing, 'dev rows the policy table cannot explain — turn each into a scenario first').toEqual([]);
  });
});
