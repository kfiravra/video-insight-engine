import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import type { ConfirmChannel } from 'amqplib';
import {
  adminActorRequest,
  buildTestApp,
  createMockContainer,
  TEST_ADMIN_USER_ID,
  type MockContainer,
} from '../../../test/helpers.js';
import { config } from '../../../config.js';

interface FakeDlqMessage {
  content: Buffer;
  properties: {
    priority?: number;
    messageId?: string;
    headers?: Record<string, unknown>;
  };
}

interface FakeChannel {
  get: ReturnType<typeof vi.fn>;
  publish: ReturnType<typeof vi.fn>;
  waitForConfirms: ReturnType<typeof vi.fn>;
  ack: ReturnType<typeof vi.fn>;
  nack: ReturnType<typeof vi.fn>;
}

function fakeDlqMessage(payload: unknown, props: FakeDlqMessage['properties'] = {}): FakeDlqMessage {
  const content =
    typeof payload === 'string' ? Buffer.from(payload, 'utf8') : Buffer.from(JSON.stringify(payload), 'utf8');
  return { content, properties: props };
}

/** Fake AMQP channel: `get` drains the provided messages, then returns false. */
function createFakeChannel(messages: FakeDlqMessage[]): FakeChannel {
  let idx = 0;
  return {
    get: vi.fn(async () => (idx < messages.length ? messages[idx++] : false)),
    publish: vi.fn(() => true),
    waitForConfirms: vi.fn(async () => undefined),
    ack: vi.fn(),
    nack: vi.fn(),
  };
}

const REPLAY_URL = '/api/admin/queue/replay';

/** A replay signed the way vie-admin signs it: method, exact target and body bound. */
function replayRequest(body?: Record<string, unknown>, options: { nowSeconds?: number } = {}) {
  const signed = adminActorRequest('POST', REPLAY_URL, body, options);
  return { method: 'POST' as const, url: REPLAY_URL, headers: signed.headers, payload: signed.payload };
}

describe('admin queue routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  const adminHeaders = { 'x-admin-key': config.ADMIN_API_KEY };

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
    // restoreAllMocks empties vi.fn() implementations; the signed actor must still hold its permission.
    mockContainer.userRepository.hasPermission.mockResolvedValue(true);
    mockContainer.adminReplayGuard.claim.mockResolvedValue(true);
  });

  describe('auth', () => {
    it('returns 401 when admin key is missing', async () => {
      const res = await app.inject({ method: 'GET', url: '/api/admin/queue/stats' });
      expect(res.statusCode).toBe(401);
    });

    it('returns 401 when admin key is wrong', async () => {
      const res = await app.inject({
        method: 'GET',
        url: '/api/admin/queue/stats',
        headers: { 'x-admin-key': 'nope' },
      });
      expect(res.statusCode).toBe(401);
    });
  });

  describe('GET /api/admin/queue/stats', () => {
    it('returns depth + dlq depth from the management API', async () => {
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
        const u = String(url);
        if (u.includes('vie.pipeline.jobs')) {
          return new Response(JSON.stringify({ messages: 5, messages_unacknowledged: 1 }), { status: 200 });
        }
        if (u.includes('vie.pipeline.dlq')) {
          return new Response(JSON.stringify({ messages: 2, messages_unacknowledged: 0 }), { status: 200 });
        }
        return new Response('not found', { status: 404 });
      });

      const res = await app.inject({
        method: 'GET',
        url: '/api/admin/queue/stats',
        headers: adminHeaders,
      });

      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.main.messages).toBe(5);
      expect(body.main.inFlight).toBe(1);
      expect(body.dlq.messages).toBe(2);
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });

    it('returns 502 when the management API is unreachable', async () => {
      vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('connect ECONNREFUSED'));
      const res = await app.inject({
        method: 'GET',
        url: '/api/admin/queue/stats',
        headers: adminHeaders,
      });
      expect(res.statusCode).toBe(502);
    });
  });

  describe('POST /api/admin/queue/replay (queue disabled)', () => {
    it('returns 503 BEFORE touching the DLQ when the rabbitmq plugin is absent', async () => {
      // Loss-proofing regression: the old flow destructively drained up to 500
      // messages over the management API and only then noticed the plugin was
      // disabled — losing everything it had fetched. The management API must
      // not be called at all on this path.
      const fetchSpy = vi.spyOn(globalThis, 'fetch');

      const res = await app.inject(replayRequest());

      expect(res.statusCode).toBe(503);
      expect(res.json().error).toBe('QUEUE_DISABLED');
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  describe('POST /api/admin/queue/replay (queue enabled)', () => {
    let queueApp: FastifyInstance;
    let channel: FakeChannel;
    let getChannel: ReturnType<typeof vi.fn>;

    async function buildQueueApp(fake: FakeChannel, getChannelImpl?: () => Promise<ConfirmChannel>): Promise<void> {
      channel = fake;
      getChannel = vi.fn(
        getChannelImpl ?? (async () => fake as unknown as ConfirmChannel),
      );
      queueApp = await buildTestApp(mockContainer);
      queueApp.decorate('rabbitmq', {
        getChannel,
        isReady: () => true,
      });
      await queueApp.ready();
    }

    afterEach(async () => {
      await queueApp?.close();
    });

    it('returns 0 replayed when the DLQ is empty', async () => {
      await buildQueueApp(createFakeChannel([]));

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ replayed: 0 });
      expect(channel.publish).not.toHaveBeenCalled();
    });

    it('returns 503 when the channel cannot be acquired — DLQ untouched', async () => {
      const fake = createFakeChannel([fakeDlqMessage({ videoSummaryId: 'x' })]);
      await buildQueueApp(fake, async () => {
        throw new Error('broker down');
      });

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(503);
      expect(res.json().error).toBe('QUEUE_UNAVAILABLE');
      expect(channel.get).not.toHaveBeenCalled();
    });

    it('republishes each message with attempt reset, then acks — confirm before ack', async () => {
      const m1 = fakeDlqMessage({ videoSummaryId: 'a', attempt: 3 }, { priority: 5, messageId: 'm1' });
      const m2 = fakeDlqMessage({ videoSummaryId: 'b', attempt: 2 }, { messageId: 'm2' });
      await buildQueueApp(createFakeChannel([m1, m2]));

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ replayed: 2 });
      expect(channel.publish).toHaveBeenCalledTimes(2);
      expect(channel.ack).toHaveBeenCalledTimes(2);
      expect(channel.nack).not.toHaveBeenCalled();

      // attempt reset so the worker's retry budget starts fresh
      const firstBody = JSON.parse((channel.publish.mock.calls[0][2] as Buffer).toString('utf8'));
      expect(firstBody).toMatchObject({ videoSummaryId: 'a', attempt: 1 });
      // priority survives the round-trip
      expect(channel.publish.mock.calls[0][3]).toMatchObject({ priority: 5, messageId: 'm1' });

      // Per-message ordering: publish → confirm → ack (a crash between any two
      // steps leaves the message safely in the DLQ — at-least-once semantics).
      const publishOrder = channel.publish.mock.invocationCallOrder[0];
      const confirmOrder = channel.waitForConfirms.mock.invocationCallOrder[0];
      const ackOrder = channel.ack.mock.invocationCallOrder[0];
      expect(publishOrder).toBeLessThan(confirmOrder);
      expect(confirmOrder).toBeLessThan(ackOrder);
    });

    it('loses nothing when a republish fails mid-replay: acked before, nacked (requeue) at failure', async () => {
      const m1 = fakeDlqMessage({ videoSummaryId: 'a', attempt: 3 });
      const m2 = fakeDlqMessage({ videoSummaryId: 'b', attempt: 1 });
      const fake = createFakeChannel([m1, m2]);
      fake.waitForConfirms
        .mockResolvedValueOnce(undefined)
        .mockRejectedValueOnce(new Error('channel closed'));
      await buildQueueApp(fake);

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(502);
      expect(res.json().error).toBe('REPLAY_FAILED');
      expect(res.json().replayed).toBe(1);
      // first message fully replayed
      expect(channel.ack).toHaveBeenCalledTimes(1);
      // failed message returned to the DLQ, not dropped
      expect(channel.nack).toHaveBeenCalledTimes(1);
      expect(channel.nack.mock.calls[0][2]).toBe(true); // requeue=true
    });

    it('passes non-JSON payloads through verbatim', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage('not-json{{')]));

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ replayed: 1 });
      expect((channel.publish.mock.calls[0][2] as Buffer).toString('utf8')).toBe('not-json{{');
    });

    it('stops at the max cap', async () => {
      const messages = Array.from({ length: 5 }, (_, i) => fakeDlqMessage({ videoSummaryId: `v${i}`, attempt: 2 }));
      await buildQueueApp(createFakeChannel(messages));

      const res = await queueApp.inject(replayRequest({ max: 3 }));

      expect(res.statusCode).toBe(200);
      expect(res.json()).toEqual({ replayed: 3 });
      expect(channel.ack).toHaveBeenCalledTimes(3);
    });

    it('refuses the shared key alone: a replay needs a named admin', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' })]));

      const res = await queueApp.inject({ method: 'POST', url: '/api/admin/queue/replay', headers: adminHeaders });

      expect(res.statusCode).toBe(403);
      expect(channel.get).not.toHaveBeenCalled();
    });

    it('refuses an admin who no longer holds queue:replay, before touching the DLQ', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' })]));
      mockContainer.userRepository.hasPermission.mockResolvedValue(false);

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(403);
      expect(channel.get).not.toHaveBeenCalled();
    });

    it('checks the signed admin against the queue:replay permission', async () => {
      await buildQueueApp(createFakeChannel([]));

      await queueApp.inject(replayRequest());

      expect(mockContainer.userRepository.hasPermission).toHaveBeenCalledWith(TEST_ADMIN_USER_ID, 'queue:replay');
    });

    it('refuses a stale actor header before touching the DLQ', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' })]));
      const stale = replayRequest(undefined, { nowSeconds: Math.floor(Date.now() / 1000) - 301 });

      const res = await queueApp.inject(stale);

      expect(res.statusCode).toBe(401);
      expect(channel.get).not.toHaveBeenCalled();
    });

    it('refuses a replay carrying a header signed for a read of the queue stats', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' })]));
      const read = adminActorRequest('GET', '/api/admin/queue/stats');

      const res = await queueApp.inject({ method: 'POST', url: REPLAY_URL, headers: read.headers });

      expect(res.statusCode).toBe(401);
      expect(channel.get).not.toHaveBeenCalled();
    });

    it('refuses a replay whose body was changed after signing', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' })]));
      const signed = adminActorRequest('POST', REPLAY_URL, { max: 1 });

      const res = await queueApp.inject({
        method: 'POST',
        url: REPLAY_URL,
        headers: signed.headers,
        payload: JSON.stringify({ max: 500 }),
      });

      expect(res.statusCode).toBe(401);
      expect(channel.get).not.toHaveBeenCalled();
    });

    it('refuses a replay whose signature was already used', async () => {
      await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' })]));
      mockContainer.adminReplayGuard.claim.mockResolvedValue(false);

      const res = await queueApp.inject(replayRequest());

      expect(res.statusCode).toBe(401);
      expect(channel.get).not.toHaveBeenCalled();
    });

    describe('audit', () => {
      it('records the replay with the signed admin, the queue and the replayed count', async () => {
        await buildQueueApp(createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' }), fakeDlqMessage({ videoSummaryId: 'b' })]));

        await queueApp.inject(replayRequest());

        expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith({
          actorId: TEST_ADMIN_USER_ID,
          requestId: expect.any(String),
          ip: expect.any(String),
          action: 'queue.replay',
          targetType: 'queue',
          targetId: 'vie.pipeline.dlq',
          after: { replayed: 2, cap: 100, failed: false },
        });
      });

      it('records a replay that failed part-way, with what was already republished', async () => {
        const fake = createFakeChannel([fakeDlqMessage({ videoSummaryId: 'a' }), fakeDlqMessage({ videoSummaryId: 'b' })]);
        fake.waitForConfirms.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('channel closed'));
        await buildQueueApp(fake);

        await queueApp.inject(replayRequest());

        expect(mockContainer.auditService.recordBestEffort).toHaveBeenCalledWith(
          expect.objectContaining({ action: 'queue.replay', after: { replayed: 1, cap: 100, failed: true } }),
        );
      });

      it('does not record a replay that never reached the DLQ', async () => {
        await buildQueueApp(createFakeChannel([]), async () => {
          throw new Error('broker down');
        });

        await queueApp.inject(replayRequest());

        expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
      });

      it('does not record a replay that was refused', async () => {
        await buildQueueApp(createFakeChannel([]));

        await queueApp.inject({ method: 'POST', url: '/api/admin/queue/replay', headers: adminHeaders });

        expect(mockContainer.auditService.recordBestEffort).not.toHaveBeenCalled();
      });
    });
  });
});
