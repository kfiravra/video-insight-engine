/**
 * P1.7 / P1.8 through the real `POST /internal/status` route: a terminal
 * status settles or releases the run's hold via the shadow ledger, off the
 * request path, exactly once however many times the status repeats. The
 * shadow service here is real and writes the shared test replica set.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { insertUser, makeShadowHarness } from '../services/billing/__tests__/billing-fixtures.js';
import type { ShadowHarness } from '../services/billing/__tests__/billing-fixtures.js';
import type { BillingShadowService } from '../services/billing/billing-shadow.service.js';

const INTERNAL_SECRET = 'dev-internal-secret-change-me';

describe('POST /internal/status — shadow ledger settle/release', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let h: ShadowHarness;
  let userId: string;
  let videoSummaryId: string;
  /** The test swaps the harness per test; the app keeps one stable shadow object. */
  const shadowProxy = {
    runFinished: (id: string, status: 'completed' | 'failed') => h.shadow.runFinished(id, status),
  } as unknown as BillingShadowService;

  const relay = async (status: 'completed' | 'failed') => {
    const response = await app.inject({
      method: 'POST',
      url: '/internal/status',
      headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET },
      payload: { type: 'video.status', payload: { videoSummaryId, userId, status } },
    });
    await h.shadow.drain();
    return response;
  };
  const ledger = (type: string) => getTestDb().collection('ledgerEntries').find({ type, 'source.ref': 'req-run-1' }).toArray();

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    mockContainer = createMockContainer();
    const overrides: Partial<MockContainer> & { billingShadowService: BillingShadowService } = {
      ...mockContainer,
      billingShadowService: shadowProxy,
    };
    app = await buildTestApp(overrides);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    // The route's own userVideos write must not reach a real database.
    app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: vi.fn().mockResolvedValue({ modifiedCount: 1 }) });
    app.broadcast = vi.fn();
    h = makeShadowHarness(getTestDb());
    userId = await insertUser(getTestDb());
    videoSummaryId = new ObjectId().toHexString();
    h.stamps.set(videoSummaryId, 'req-run-1');
    h.shadow.submitAdmitted({ requestId: 'req-run-1', userId, youtubeId: 'dQw4w9WgXcQ', durationSec: 600 });
    await h.shadow.drain();
  });

  it('should settle the run once when "completed" arrives twice (translation)', async () => {
    const first = await relay('completed');
    const second = await relay('completed');

    expect([first.statusCode, second.statusCode]).toEqual([200, 200]);
    expect(await ledger('debit')).toHaveLength(1);
    expect((await h.rowOf('req-run-1'))?.path).toBe('retry');
  });

  it('should write no release when "failed" arrives after "completed"', async () => {
    await relay('completed');

    await relay('failed');

    expect(await ledger('release')).toEqual([]);
    expect(await ledger('debit')).toHaveLength(1);
  });

  it('should release a failed run and still run the old gate\'s reconcile', async () => {
    const response = await relay('failed');

    expect(response.json()).toEqual({ received: true });
    expect(await ledger('release')).toHaveLength(1);
    expect(mockContainer.costMonitorService.reconcileUserDay).toHaveBeenCalledWith(userId, expect.any(String));
  });

  it('should answer the status relay even when the shadow cannot find the run', async () => {
    h.stamps.clear();

    const response = await relay('completed');

    expect(response.statusCode).toBe(200);
    expect(await ledger('debit')).toEqual([]);
  });
});
