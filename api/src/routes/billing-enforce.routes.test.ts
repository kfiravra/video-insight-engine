/**
 * The token ledger on the wire, over a real LedgerEnforcement on the test
 * replica set: the refusals reach the client as 402 / 422 / 403 / 429 with
 * their numbers as body fields; the assistant's generate route runs the same
 * gate; a "completed" status settles nothing and the run-cost callback
 * settles the run once, on its real cost; the video detail shows what the
 * user's run holds or cost.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { ObjectId } from 'mongodb';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { getTestDb } from '../test/setup.js';
import { createBillingIndexes } from '../plugins/mongodb.js';
import { RunNotAllowedError } from '../utils/errors.js';
import { insertUser, makeBillingHarness, makeEnforcement } from '../services/billing/__tests__/billing-fixtures.js';
import type { BillingHarness } from '../services/billing/__tests__/billing-fixtures.js';
import type { LedgerEnforcement } from '../services/billing/ledger-enforcement.js';

const INTERNAL_SECRET = 'dev-internal-secret-change-me';
const ctx = () => ({ actorId: 'system:test', reason: 'test', auditId: new ObjectId().toHexString() });

describe('token ledger — HTTP', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let h: BillingHarness;
  let userId: string;
  let token: string;
  /** The app keeps one object; each test swaps the harness behind it. */
  const enforcementProxy = {
    admit: (s: never) => makeEnforcement(getTestDb(), h).admit(s),
    submitResolved: (id: string, o: never) => makeEnforcement(getTestDb(), h).submitResolved(id, o),
    submitFailed: (id: string) => makeEnforcement(getTestDb(), h).submitFailed(id),
    runFinished: (id: string, s: 'completed' | 'failed') => makeEnforcement(getTestDb(), h).runFinished(id, s),
    runCosted: (id: string, runId: string | null, costUsd: number) => makeEnforcement(getTestDb(), h).runCosted(id, runId, costUsd),
    videoBilling: (user: string, id: string) => makeEnforcement(getTestDb(), h).videoBilling(user, id),
  } as unknown as LedgerEnforcement;

  const submit = (minutes: number, url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ') => {
    h.metadata.getVideoMetadata.mockResolvedValue({ duration: minutes * 60 });
    return app.inject({
      method: 'POST', url: '/api/videos',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      payload: { url },
    });
  };
  const internal = (url: string, payload: Record<string, unknown>) => app.inject({
    method: 'POST', url,
    headers: { 'content-type': 'application/json', 'x-internal-secret': INTERNAL_SECRET },
    payload,
  });
  const relayStatus = (videoSummaryId: string, status: string) =>
    internal('/internal/status', { type: 'video.status', payload: { videoSummaryId, userId, status } });
  const debits = (runId: string) => getTestDb().collection('ledgerEntries').countDocuments({ type: 'debit', 'source.ref': runId });
  /** Submit a 10-minute video and stamp its row with the run, as the dispatch would: the run id and the row id. */
  const dispatched = async (): Promise<{ runId: string; videoSummaryId: string }> => {
    await submit(10);
    const runId: string = mockContainer.videoService.createVideo.mock.calls[0][2].billingRunId;
    const videoSummaryId = new ObjectId().toHexString();
    h.stamps.set(videoSummaryId, runId);
    return { runId, videoSummaryId };
  };

  beforeAll(async () => {
    await createBillingIndexes(getTestDb());
    mockContainer = createMockContainer();
    // A real ledger in place of the mock one createMockContainer brings.
    const overrides = { ...mockContainer, billingEnforcement: enforcementProxy };
    app = await buildTestApp(overrides as unknown as Partial<MockContainer>);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(async () => {
    vi.clearAllMocks();
    app.mongo.db.collection = vi.fn().mockReturnValue({ updateMany: vi.fn().mockResolvedValue({ modifiedCount: 1 }) });
    app.broadcast = vi.fn();
    h = makeBillingHarness(getTestDb());
    userId = await insertUser(getTestDb());
    token = app.jwt.sign({ userId, email: 'user@example.com', type: 'access' });
    mockContainer.videoService.createVideo.mockResolvedValue({
      video: { id: new ObjectId().toHexString(), videoSummaryId: new ObjectId().toHexString(), youtubeId: 'dQw4w9WgXcQ', status: 'pending' },
      cached: false,
    });
  });

  describe('POST /api/videos', () => {
    it('should admit a video with a hold of its estimate on the ledger', async () => {
      const response = await submit(10);

      const holds = await getTestDb().collection('ledgerEntries').find({ userId: new ObjectId(userId), type: 'hold' }).toArray();
      expect(response.statusCode).toBe(201);
      expect(holds.map((hold) => hold.tokens)).toEqual([-840]);
    });

    it('should answer 402 INSUFFICIENT_TOKENS with the estimate needed and the balance available', async () => {
      await submit(10);
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await submit(10, 'https://www.youtube.com/watch?v=bbbbbbbbbbb');

      expect(response.statusCode).toBe(402);
      expect(response.json()).toMatchObject({ error: 'INSUFFICIENT_TOKENS', needed: 840, available: 160 });
      expect(response.json()).not.toHaveProperty('nextFreeAt');
    });

    it('should serve an already-cached video free once the balance cannot pay for a run (C1 / Q6)', async () => {
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

    it('should answer 422 VIDEO_TOO_LONG with maxMinutes', async () => {
      await h.billing.grantLot(userId, { tokens: 10_000 }, ctx());
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await submit(200);

      expect([response.statusCode, response.json().error, response.json().maxMinutes]).toEqual([422, 'VIDEO_TOO_LONG', 180]);
    });

    it('should answer 403 ACCOUNT_BLOCKED', async () => {
      await h.billing.setCaps(userId, { blocked: true }, ctx());

      const response = await submit(10);

      expect([response.statusCode, response.json().error]).toEqual([403, 'ACCOUNT_BLOCKED']);
    });

    it('should answer 429 TOO_MANY_RUNS with maxConcurrent', async () => {
      await h.billing.grantLot(userId, { tokens: 5000 }, ctx());
      await h.billing.setCaps(userId, { maxConcurrent: 1 }, ctx());
      await submit(10);
      mockContainer.videoService.createVideo.mockRejectedValueOnce(new RunNotAllowedError());

      const response = await submit(10, 'https://www.youtube.com/watch?v=bbbbbbbbbbb');

      expect([response.statusCode, response.json().error, response.json().maxConcurrent]).toEqual([429, 'TOO_MANY_RUNS', 1]);
    });

    it('should queue a free account\'s run as free and a Pro account\'s as paid (D18)', async () => {
      await submit(10);
      await h.billing.setPlan(userId, { planId: 'pro' }, ctx());
      await submit(10, 'https://www.youtube.com/watch?v=bbbbbbbbbbb');

      expect(mockContainer.videoService.createVideo.mock.calls.map((call) => call[2].queuePlan)).toEqual(['free', 'paid']);
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

      expect([response.statusCode, response.json()]).toEqual([402, expect.objectContaining({ error: 'INSUFFICIENT_TOKENS', needed: 840, available: 160 })]);
    });
  });

  describe('POST /internal/status', () => {
    it('should settle nothing when "completed" arrives: the hold waits for the run cost', async () => {
      const { runId, videoSummaryId } = await dispatched();

      const responses = [await relayStatus(videoSummaryId, 'completed'), await relayStatus(videoSummaryId, 'completed')];

      expect(responses.map((r) => r.statusCode)).toEqual([200, 200]);
      expect(await debits(runId)).toBe(0);
      expect((await h.billing.summary(userId)).holds).toEqual([{ youtubeId: 'dQw4w9WgXcQ', tokens: 840 }]);
    });

    it('should release a failed run in full', async () => {
      const { videoSummaryId } = await dispatched();

      await relayStatus(videoSummaryId, 'failed');

      expect(await h.billing.summary(userId)).toMatchObject({ balance: 1000, holds: [] });
    });
  });

  describe('POST /internal/run-cost', () => {
    it('should settle the run on its real cost once, however many times the cost arrives', async () => {
      const { runId, videoSummaryId } = await dispatched();
      await relayStatus(videoSummaryId, 'completed');

      const responses = [
        await internal('/internal/run-cost', { videoSummaryId, billingRunId: runId, costUsd: 0.25, llmCalls: 12 }),
        await internal('/internal/run-cost', { videoSummaryId, billingRunId: runId, costUsd: 0.25, llmCalls: 12 }),
      ];

      expect(responses.map((r) => [r.statusCode, r.json().status])).toEqual([[200, 'debited'], [200, 'already_debited']]);
      expect(await debits(runId)).toBe(1);
      expect(await h.billing.summary(userId)).toMatchObject({ balance: 250, holds: [] });
    });

    it('should ignore the cost of a run the row no longer names, settling nothing', async () => {
      const { runId, videoSummaryId } = await dispatched();

      const response = await internal('/internal/run-cost', { videoSummaryId, billingRunId: 'an-older-run', costUsd: 0.25 });

      expect([response.statusCode, response.json().status]).toEqual([200, 'ignored']);
      expect(await debits(runId)).toBe(0);
    });

    it('should leave a negative balance when the run cost more than the account had', async () => {
      const { runId, videoSummaryId } = await dispatched();

      await internal('/internal/run-cost', { videoSummaryId, billingRunId: runId, costUsd: 0.5 });

      expect(await h.billing.summary(userId)).toMatchObject({ balance: -500, debt: 500 });
    });
  });

  describe('GET /api/videos/:id billing', () => {
    const open = async (videoSummaryId: string) => {
      mockContainer.videoService.getVideo.mockResolvedValueOnce({ id: new ObjectId().toHexString(), videoSummaryId, status: 'processing' });
      const response = await app.inject({
        method: 'GET', url: `/api/videos/${new ObjectId().toHexString()}`, headers: { authorization: `Bearer ${token}` },
      });
      expect(response.statusCode).toBe(200);
      return response.json().billing;
    };

    it('should show the estimate held while the run is in flight', async () => {
      const { videoSummaryId } = await dispatched();

      expect(await open(videoSummaryId)).toEqual({ state: 'held', estimatedTokens: 840, chargedTokens: null });
    });

    it('should show what the run was charged once its cost arrived', async () => {
      const { runId, videoSummaryId } = await dispatched();
      await internal('/internal/run-cost', { videoSummaryId, billingRunId: runId, costUsd: 0.25 });

      expect(await open(videoSummaryId)).toEqual({ state: 'charged', estimatedTokens: 840, chargedTokens: 750 });
    });

    it('should show a failed run as released', async () => {
      const { videoSummaryId } = await dispatched();
      await relayStatus(videoSummaryId, 'failed');

      expect(await open(videoSummaryId)).toEqual({ state: 'released', estimatedTokens: 840, chargedTokens: 0 });
    });

    it('should show free to a user who ran nothing on the video (another user\'s run)', async () => {
      const { videoSummaryId } = await dispatched();
      userId = await insertUser(getTestDb());
      token = app.jwt.sign({ userId, email: 'other@example.com', type: 'access' });

      expect(await open(videoSummaryId)).toEqual({ state: 'free', estimatedTokens: 0, chargedTokens: 0 });
    });
  });
});
