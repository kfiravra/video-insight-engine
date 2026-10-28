/**
 * P1.11 — the shadow gate (Kfir 2026-10-27): replay scripted submits, cached /
 * attached serves, failures, refusals and retries through the real submit,
 * playlist and terminal-status paths over the minutes ledger, which decides
 * on the request path. Each scenario's recorded `oldDecision` is the old
 * dollar gate's side (the gate itself is gone, P3.2); refusal reasons come
 * from the submit's error. Zero `disagree`; every `policy_delta` must be the
 * one the scenario names. Also re-judges every real `billingShadow` row in
 * the dev database when it is reachable (read-only).
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { MongoClient } from 'mongodb';
import type { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';
import { createBillingIndexes } from '../../../plugins/mongodb.js';
import { POLICY_DELTAS, classifyShadow, errorReason } from '../shadow-policy.js';
import { ReplayHarness } from './shadow-replay.harness.js';
import { REPLAY_SCENARIOS } from './shadow-replay.scenarios.js';
import type { BillingShadow } from '@vie/types';
import type { RunResult } from './shadow-replay.harness.js';
import type { AccountKind, PolicyId, ReplayExpectation, ReplayScenario } from './shadow-replay.scenarios.js';

/** The parts of a recorded dev `billingShadow` row the policy table re-judges. */
type RecordedShadowRow = Pick<BillingShadow, 'requestId' | 'path' | 'oldDecision' | 'newDecision' | 'classification' | 'policyRef'>;

const DEV_MONGODB_URI =
  process.env.BILLING_REPLAY_DEV_MONGODB_URI ?? 'mongodb://localhost:27017/video-insight-engine?directConnection=true';

function policyRefOf(policy: PolicyId | undefined): string | undefined {
  if (!policy) return undefined;
  const delta = POLICY_DELTAS.find((candidate) => candidate.id === policy);
  if (!delta) throw new Error(`unknown policy id ${policy}`);
  return delta.policyRef;
}

/** The ledger's side: its entries plus the submit's refusal. */
function newDecisionOf(result: RunResult): { admitted: boolean; charged: boolean; reason?: string } {
  const reason = errorReason(result.error);
  return { ...result.ledger, ...(reason && { reason }) };
}

function assertRun(scenario: ReplayScenario, expectation: ReplayExpectation, result: RunResult): void {
  const where = `${scenario.name} / ${expectation.run}`;
  if (expectation.heldRuns !== undefined) expect(result.heldRuns, `${where}: runs holding minutes`).toBe(expectation.heldRuns);
  const newDecision = newDecisionOf(result);
  const verdict = classifyShadow({ path: expectation.path, oldDecision: expectation.oldDecision, newDecision });
  const facts = JSON.stringify({ oldDecision: expectation.oldDecision, newDecision });
  expect(verdict.classification, `${where}: unexplained difference ${facts}`).not.toBe('disagree');
  expect(verdict.classification, `${where}: classification ${facts}`).toBe(expectation.classification);
  expect(verdict.policyRef, `${where}: policy`).toBe(policyRefOf(expectation.policy));
}

/** Every billingShadow row in the dev database, or null when it cannot be reached. */
async function readDevRows(): Promise<RecordedShadowRow[] | null> {
  const client = new MongoClient(DEV_MONGODB_URI, { serverSelectionTimeoutMS: 1500, connectTimeoutMS: 1500 });
  try {
    await client.connect();
    return await client.db().collection<RecordedShadowRow>('billingShadow').find({}).toArray();
  } catch (err) {
    console.warn(`[shadow-replay] dev Mongo not reachable (${(err as Error).message}); real-row replay skipped`);
    return null;
  } finally {
    await client.close();
  }
}

describe('shadow replay harness (P1.11) — the ledger against the recorded old gate', () => {
  let app: FastifyInstance;
  let harness: ReplayHarness;

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    harness = new ReplayHarness(getTestDb());
    vi.stubGlobal('fetch', harness.fetchStub);
    app = await buildTestApp({ ...createMockContainer(), billingEnforcement: harness.enforcement } as never);
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
    },
  );
});

describe('shadow replay harness (P1.11) — coverage and real rows', () => {
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
    // W1: never explained, but not a defect either — the gate reports how many there were.
    const lengthUnknown = rows.filter((row) => row.classification === 'metadata_unavailable').length;
    if (lengthUnknown > 0) console.warn(`[shadow-replay] ${lengthUnknown} of ${rows.length} dev rows are metadata_unavailable`);

    expect(misjudged, 'dev rows whose stored verdict no longer matches the policy table').toEqual([]);
    expect(disagreeing, 'dev rows the policy table cannot explain — turn each into a scenario first').toEqual([]);
  });
});
