/**
 * P3.1 on the wire, with BILLING_LEDGER_ENFORCE on (a real LedgerEnforcement
 * on the test replica set): the refusals reach the client as 402 / 422 / 403 /
 * 429 with their numbers as body fields; the assistant's generate route runs
 * the same gate; the terminal status settles the run exactly once.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { RunNotAllowedError } from '../utils/errors.js';
import { insertUser, makeEnforcement, makeShadowHarness } from '../services/billing/__tests__/billing-fixtures.js';
import type { ShadowHarness } from '../services/billing/__tests__/billing-fixtures.js';
import type { LedgerEnforcement } from '../services/billing/ledger-enforcement.js';

const INTERNAL_SECRET = 'dev-internal-secret-change-me';
const ctx = () => ({ actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });

describe('BILLING_LEDGER_ENFORCE on — HTTP', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let h: ShadowHarness;
  let userId: string;
  let token: string;
  /** The app keeps one object; each test swaps the harness behind it. */
  const enforcementProxy = {
    admit: (s: never) => makeEnforcement(getTestDb(), h).admit(s),
    submitResolved: (id: string, o: never) => makeEnforcement(getTestDb(), h).submitResolved(id, o),
    submitFailed: (id: string) => makeEnforcement(getTestDb(), h).submitFailed(id),
    runFinished: (id: string, s: 'completed' | 'failed') => makeEnforcement(getTestDb(), h).runFinished(id, s),
  } as unknown as LedgerEnforcement;

  const submit = (minutes: number, url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ') => {
    h.metadata.getVideoMetadata.mockResolvedValue({ duration: minutes * 60 });
    return app.inject({
      method: 'POST', url: '/api/videos',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      payload: { url },
    });
  };

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    mockContainer = createMockContainer();
    const overrides: Partial<MockContainer> & { billingEnforcement: LedgerEnforcement } = {
      ...mockContainer,
      billingEnforcement: enforcementProxy,
    };
    app = await buildTestApp(overrides);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: vi.fn().mockResolvedValue({ modifiedCount: 1 }) });
    app.broadcast = vi.fn();
    h = makeShadowHarness(getTestDb());
    userId = await insertUser(getTestDb());
    token = app.jwt.sign({ userId, email: 'user@example.com', type: 'access' });
    mockContainer.videoService.createVideo.mockResolvedValue({
      video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), youtubeId: 'dQw4w9WgXcQ', status: 'pending' },
      cached: false,
    });
  });

  describe('POST /api/videos', () => {
    it('should admit a free video and skip the old dollar gate', async () => {
      const response = await submit(10);

      expect(response.statusCode).toBe(201);
      expect(mockContainer.costMonitorService.reserveUserCost).not.toHaveBeenCalled();
    });

    it('should answer 402 INSUFFICIENT_MINUTES with needed, available and nextFreeAt', async () => {
      await submit(10);
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await submit(10, 'https://www.youtube.com/watch?v=bbbbbbbbbbb');

      expect(response.statusCode).toBe(402);
      expect(response.json()).toMatchObject({ error: 'INSUFFICIENT_MINUTES', needed: 10, available: 0, nextFreeAt: expect.any(String) });
    });

    it('should serve an already-cached video free once the free video is used (C1 / Q6)', async () => {
      await submit(10);
      mockContainer.videoService.createVideo.mockResolvedValueOnce({
        video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), youtubeId: 'bbbbbbbbbbb', status: 'completed' },
        cached: true,
      });

      const response = await submit(10, 'https://www.youtube.com/watch?v=bbbbbbbbbbb');

      expect(response.statusCode).toBe(201);
      expect(response.json()).toMatchObject({ cached: true });
      expect(mockContainer.videoService.createVideo.mock.calls[1][2]).toMatchObject({ serveOnly: true });
    });

    it('should answer 422 VIDEO_TOO_LONG_FOR_PLAN with maxMinutes', async () => {
      await h.billing.grantLot(userId, { minutes: 1000 }, ctx());
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await submit(200);

      expect([response.statusCode, response.json().error, response.json().maxMinutes]).toEqual([422, 'VIDEO_TOO_LONG_FOR_PLAN', 180]);
    });

    it('should answer 403 ACCOUNT_BLOCKED', async () => {
      await h.billing.setCaps(userId, { blocked: true }, ctx());

      const response = await submit(10);

      expect([response.statusCode, response.json().error]).toEqual([403, 'ACCOUNT_BLOCKED']);
    });

    it('should answer 429 TOO_MANY_RUNS with maxConcurrent', async () => {
      await h.billing.grantLot(userId, { minutes: 100 }, ctx());
      await h.billing.setCaps(userId, { maxConcurrent: 1 }, ctx());
      await submit(10);
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await submit(10, 'https://www.youtube.com/watch?v=bbbbbbbbbbb');

      expect([response.statusCode, response.json().error, response.json().maxConcurrent]).toEqual([429, 'TOO_MANY_RUNS', 1]);
    });
  });

  describe('POST /internal/assistant/generate', () => {
    it('should run the same gate', async () => {
      await submit(10);
      h.metadata.getVideoMetadata.mockResolvedValue({ duration: 600 });
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await app.inject({
        method: 'POST', url: '/internal/assistant/generate',
        headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET, 'x-user-id': userId },
        payload: { url: 'https://youtu.be/ccccccccccc' },
      });

      expect([response.statusCode, response.json().error]).toEqual([402, 'INSUFFICIENT_MINUTES']);
    });
  });

  describe('POST /internal/status', () => {
    it('should settle the run once however many times "completed" arrives', async () => {
      await submit(10);
      const runId = mockContainer.videoService.createVideo.mock.calls[0][2].billingRunId;
      const videoSummaryId = new ObjectId().toHexString();
      h.stamps.set(videoSummaryId, runId);
      const relay = () => app.inject({
        method: 'POST', url: '/internal/status',
        headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET },
        payload: { type: 'video.status', payload: { videoSummaryId, userId, status: 'completed' } },
      });

      const responses = [await relay(), await relay()];

      expect(responses.map((r) => r.statusCode)).toEqual([200, 200]);
      expect(await getTestDb().collection('ledgerEntries').countDocuments({ type: 'debit', 'source.ref': runId })).toBe(1);
    });
  });
});
