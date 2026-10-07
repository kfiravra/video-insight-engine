import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import http from 'node:http';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { ObjectId } from 'mongodb';

// Mock global fetch for summarizer service calls
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('stream routes', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let authHeader: string;

  // Valid ObjectIds for testing - must be valid 24-char hex strings
  const validVideoSummaryId = new ObjectId().toHexString();
  // The stream route uses new ObjectId(req.user.userId), so userId MUST be a valid ObjectId
  const validUserObjectId = new ObjectId().toHexString();

  /**
   * Generate auth header with a valid ObjectId userId
   * The stream route specifically requires userId to be a valid ObjectId string
   */
  async function getValidAuthHeader(fastify: FastifyInstance): Promise<string> {
    const token = fastify.jwt.sign({ userId: validUserObjectId, email: 'test@example.com' });
    return `Bearer ${token}`;
  }

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    // Use our custom auth header with valid ObjectId userId
    authHeader = await getValidAuthHeader(app);
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
  });

  describe('OPTIONS /api/videos/:videoSummaryId/stream', () => {
    it('should handle CORS preflight request with valid origin', async () => {
      const response = await app.inject({
        method: 'OPTIONS',
        url: `/api/videos/${validVideoSummaryId}/stream`,
        headers: {
          origin: 'http://localhost:5173',
          'access-control-request-method': 'GET',
          'access-control-request-headers': 'authorization,accept',
        },
      });

      expect(response.statusCode).toBe(204);
      expect(response.headers['access-control-allow-origin']).toBe('http://localhost:5173');
      expect(response.headers['access-control-allow-credentials']).toBe('true');
      expect(response.headers['access-control-allow-methods']).toContain('GET');
    });

    it('should handle CORS preflight for disallowed origin', async () => {
      const response = await app.inject({
        method: 'OPTIONS',
        url: `/api/videos/${validVideoSummaryId}/stream`,
        headers: {
          origin: 'http://malicious-site.com',
          'access-control-request-method': 'GET',
        },
      });

      // Route still returns 204 but without CORS headers for invalid origin
      expect(response.statusCode).toBe(204);
      // Disallowed origin should not get CORS headers
      expect(response.headers['access-control-allow-origin']).toBeUndefined();
    });
  });

  describe('GET /api/videos/:videoSummaryId/stream', () => {
    describe('authentication', () => {
      it('should return 401 without auth token', async () => {
        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
        });

        expect(response.statusCode).toBe(401);
      });

      it('should return 401 with invalid auth token', async () => {
        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: 'Bearer invalid-token',
          },
        });

        expect(response.statusCode).toBe(401);
      });

      it('should return 401 with malformed auth header', async () => {
        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: 'not-bearer-format',
          },
        });

        expect(response.statusCode).toBe(401);
      });
    });

    describe('authorization', () => {
      it('should return 404 when user does not have access to video', async () => {
        // Mock repository to deny access
        mockContainer.videoRepository.userHasAccessToSummary.mockResolvedValueOnce(false);

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: { authorization: authHeader },
        });

        expect(response.statusCode).toBe(404);
        expect(response.json()).toHaveProperty('error', 'VIDEO_NOT_FOUND');
      });
    });

    describe('parameter validation', () => {
      it('should return 400 for invalid videoSummaryId format', async () => {
        const response = await app.inject({
          method: 'GET',
          url: '/api/videos/invalid-id/stream',
          headers: { authorization: authHeader },
        });

        expect(response.statusCode).toBe(400);
      });

      it('should return 400 for too-short videoSummaryId', async () => {
        const response = await app.inject({
          method: 'GET',
          url: '/api/videos/abc123/stream',
          headers: { authorization: authHeader },
        });

        expect(response.statusCode).toBe(400);
      });
    });

    describe('SSE connection and streaming', () => {
      beforeEach(async () => {
        // Insert actual test data into MongoDB (not mocking)
        const db = app.mongo.db;
        await db.collection('userVideos').insertOne({
          userId: new ObjectId(validUserObjectId),
          videoSummaryId: new ObjectId(validVideoSummaryId),
        });
        // Create the video summary cache record so updateOne has something to update
        await db.collection('videoSummaryCache').insertOne({
          _id: new ObjectId(validVideoSummaryId),
          youtubeId: 'test-youtube-id',
        });
      });

      afterEach(async () => {
        // Clean up test data
        const db = app.mongo.db;
        await db.collection('userVideos').deleteMany({});
        await db.collection('videoSummaryCache').deleteMany({});
      });

      it('should establish SSE connection with proper headers when summarizer responds', async () => {
        // Create a mock ReadableStream
        const mockReader = {
          read: vi.fn()
            .mockResolvedValueOnce({
              done: false,
              value: new TextEncoder().encode('data: {"event":"start"}\n\n'),
            })
            .mockResolvedValueOnce({ done: true, value: undefined }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
            origin: 'http://localhost:5173',
          },
        });

        // Verify fetch was called with correct URL
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining(`/summarize/stream/${validVideoSummaryId}`),
          expect.objectContaining({
            headers: { Accept: 'text/event-stream' },
          })
        );

        // SSE response should contain the streamed data
        expect(response.body).toContain('data: {"event":"start"}');
      });

      it('should forward SSE events from summarizer to client', async () => {
        const sseEvents = [
          'data: {"event":"metadata","title":"Test Video","channel":"Test Channel"}\n\n',
          'data: {"event":"chunk","text":"Hello"}\n\n',
          'data: {"event":"chunk","text":" World"}\n\n',
          'data: {"event":"complete"}\n\n',
        ];

        let readIndex = 0;
        const mockReader = {
          read: vi.fn().mockImplementation(() => {
            if (readIndex < sseEvents.length) {
              const value = new TextEncoder().encode(sseEvents[readIndex]);
              readIndex++;
              return Promise.resolve({ done: false, value });
            }
            return Promise.resolve({ done: true, value: undefined });
          }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Verify all events were forwarded
        expect(response.body).toContain('metadata');
        expect(response.body).toContain('Test Video');
        expect(response.body).toContain('chunk');
        expect(response.body).toContain('complete');
      });

      it('should persist metadata via repository when received', async () => {
        const metadataEvent = 'data: {"event":"metadata","title":"Test Video","channel":"Test Channel","thumbnailUrl":"https://example.com/thumb.jpg","duration":300}\n\n';

        const mockReader = {
          read: vi.fn()
            .mockResolvedValueOnce({
              done: false,
              value: new TextEncoder().encode(metadataEvent),
            })
            .mockResolvedValueOnce({ done: true, value: undefined }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Verify metadata was persisted via videoRepository.updateCacheEntry
        expect(mockContainer.videoRepository.updateCacheEntry).toHaveBeenCalledWith(
          validVideoSummaryId,
          {
            title: 'Test Video',
            channel: 'Test Channel',
            thumbnailUrl: 'https://example.com/thumb.jpg',
            duration: 300,
          }
        );
      });
    });

    describe('synthesis_complete persistence', () => {
      // The container's mock repository predates mergeSynthesis; attach a
      // fresh one per test so call assertions never leak across tests.
      let mergeSynthesis: ReturnType<typeof vi.fn>;

      const earlyEvent = { event: 'synthesis_complete', tldr: 'Memory tldr', keyTakeaways: ['Memory point'] };
      const lateEvent = {
        event: 'synthesis_complete',
        tldr: 'Final tldr',
        keyTakeaways: ['Final point'],
        masterSummary: 'The full master summary.',
        seoDescription: 'SEO description.',
      };

      function mockSummarizerStream(events: Record<string, unknown>[]): void {
        const chunks = events.map((e) => new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`));
        const read = vi.fn();
        for (const value of chunks) read.mockResolvedValueOnce({ done: false, value });
        read.mockResolvedValueOnce({ done: true, value: undefined });
        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: { getReader: () => ({ read, cancel: vi.fn().mockResolvedValue(undefined) }) },
        });
      }

      async function requestStream(): Promise<{ body: string }> {
        return app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: { authorization: authHeader, accept: 'text/event-stream' },
        });
      }

      beforeEach(() => {
        mergeSynthesis = vi.fn().mockResolvedValue(undefined);
        Object.assign(mockContainer.videoRepository, { mergeSynthesis });
      });

      it('should persist the early emission when it has no masterSummary', async () => {
        mockSummarizerStream([earlyEvent]);

        await requestStream();

        expect(mergeSynthesis).toHaveBeenCalledWith(
          validVideoSummaryId,
          expect.objectContaining({ tldr: 'Memory tldr', keyTakeaways: ['Memory point'] }),
        );
      });

      it('should persist both emissions in arrival order when early precedes late', async () => {
        mockSummarizerStream([earlyEvent, lateEvent]);

        await requestStream();

        expect(mergeSynthesis.mock.calls.map(([, fields]) => fields.masterSummary)).toEqual([
          undefined,
          'The full master summary.',
        ]);
      });

      it('should skip persistence when the synthesis_complete payload is malformed', async () => {
        mockSummarizerStream([{ event: 'synthesis_complete', tldr: 'x', keyTakeaways: 'not-a-list' }]);

        await requestStream();

        expect(mergeSynthesis).not.toHaveBeenCalled();
      });

      it('should still forward a malformed synthesis_complete event to the client', async () => {
        mockSummarizerStream([{ event: 'synthesis_complete', tldr: 'x', keyTakeaways: 'not-a-list' }]);

        const response = await requestStream();

        expect(response.body).toContain('not-a-list');
      });
    });

    describe('error handling', () => {
      beforeEach(async () => {
        // Insert actual test data into MongoDB
        const db = app.mongo.db;
        await db.collection('userVideos').insertOne({
          userId: new ObjectId(validUserObjectId),
          videoSummaryId: new ObjectId(validVideoSummaryId),
        });
      });

      afterEach(async () => {
        const db = app.mongo.db;
        await db.collection('userVideos').deleteMany({});
      });

      it('should send error event when summarizer returns 404', async () => {
        mockFetch.mockResolvedValue({
          ok: false,
          status: 404,
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Should contain error event in SSE format
        expect(response.body).toContain('data:');
        expect(response.body).toContain('error');
        expect(response.body).toContain('NOT_FOUND');
      });

      it('should send error event when summarizer returns 500', async () => {
        mockFetch.mockResolvedValue({
          ok: false,
          status: 500,
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        expect(response.body).toContain('data:');
        expect(response.body).toContain('error');
        expect(response.body).toContain('SUMMARIZER_ERROR');
      });

      it('should send error event when summarizer response has no body', async () => {
        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: null,
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        expect(response.body).toContain('data:');
        expect(response.body).toContain('error');
        expect(response.body).toContain('NO_BODY');
      });

      it('should send error event when fetch throws network error', async () => {
        mockFetch.mockRejectedValue(new Error('Network error'));

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        expect(response.body).toContain('data:');
        expect(response.body).toContain('error');
        expect(response.body).toContain('CONNECTION_FAILED');
      });

      it('should handle unknown status codes from summarizer', async () => {
        mockFetch.mockResolvedValue({
          ok: false,
          status: 429, // Rate limited
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        expect(response.body).toContain('data:');
        expect(response.body).toContain('error');
        expect(response.body).toContain('429');
      });
    });

    describe('SSE event format validation', () => {
      beforeEach(async () => {
        const db = app.mongo.db;
        await db.collection('userVideos').insertOne({
          userId: new ObjectId(validUserObjectId),
          videoSummaryId: new ObjectId(validVideoSummaryId),
        });
        await db.collection('videoSummaryCache').insertOne({
          _id: new ObjectId(validVideoSummaryId),
          youtubeId: 'test-youtube-id',
        });
      });

      afterEach(async () => {
        const db = app.mongo.db;
        await db.collection('userVideos').deleteMany({});
        await db.collection('videoSummaryCache').deleteMany({});
      });

      it('should handle SSE events with [DONE] marker', async () => {
        const sseData = 'data: {"event":"chunk","text":"test"}\n\ndata: [DONE]\n\n';

        const mockReader = {
          read: vi.fn()
            .mockResolvedValueOnce({
              done: false,
              value: new TextEncoder().encode(sseData),
            })
            .mockResolvedValueOnce({ done: true, value: undefined }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // [DONE] should be passed through
        expect(response.body).toContain('[DONE]');
      });

      it('should handle malformed JSON in SSE events gracefully', async () => {
        const db = app.mongo.db;

        // Record initial state (only has youtubeId from beforeEach)
        const initialRecord = await db.collection('videoSummaryCache').findOne({
          _id: new ObjectId(validVideoSummaryId),
        });

        // Malformed JSON event
        const sseData = 'data: {invalid json}\n\ndata: {"event":"chunk","text":"valid"}\n\n';

        const mockReader = {
          read: vi.fn()
            .mockResolvedValueOnce({
              done: false,
              value: new TextEncoder().encode(sseData),
            })
            .mockResolvedValueOnce({ done: true, value: undefined }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Stream should still complete despite malformed JSON
        expect(response.body).toContain('valid');

        // Verify record was not updated (no title/channel etc added)
        const finalRecord = await db.collection('videoSummaryCache').findOne({
          _id: new ObjectId(validVideoSummaryId),
        });
        expect(finalRecord?.title).toBeUndefined();
        expect(finalRecord?.channel).toBeUndefined();
      });

      it('should handle chunked SSE events correctly', async () => {
        // Simulate a situation where data comes in incomplete chunks
        const chunk1 = 'data: {"event":"meta';
        const chunk2 = 'data","title":"Test"}\n\n';

        let readCount = 0;
        const mockReader = {
          read: vi.fn().mockImplementation(() => {
            readCount++;
            if (readCount === 1) {
              return Promise.resolve({
                done: false,
                value: new TextEncoder().encode(chunk1),
              });
            } else if (readCount === 2) {
              return Promise.resolve({
                done: false,
                value: new TextEncoder().encode(chunk2),
              });
            }
            return Promise.resolve({ done: true, value: undefined });
          }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Both chunks should be forwarded
        expect(response.body).toContain('meta');
        expect(response.body).toContain('Test');
      });
    });

    describe('connection cleanup', () => {
      beforeEach(async () => {
        const db = app.mongo.db;
        await db.collection('userVideos').insertOne({
          userId: new ObjectId(validUserObjectId),
          videoSummaryId: new ObjectId(validVideoSummaryId),
        });
      });

      afterEach(async () => {
        const db = app.mongo.db;
        await db.collection('userVideos').deleteMany({});
      });

      it('should cancel reader when stream completes normally', async () => {
        const mockCancel = vi.fn().mockResolvedValue(undefined);
        const mockReader = {
          read: vi.fn()
            .mockResolvedValueOnce({
              done: false,
              value: new TextEncoder().encode('data: {"event":"complete"}\n\n'),
            })
            .mockResolvedValueOnce({ done: true, value: undefined }),
          cancel: mockCancel,
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Reader should be cancelled on cleanup
        expect(mockCancel).toHaveBeenCalled();
      });

      it('should pass an abort signal to the upstream fetch', async () => {
        const mockReader = {
          read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
          cancel: vi.fn().mockResolvedValue(undefined),
        };
        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: { getReader: () => mockReader },
        });

        await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: { authorization: authHeader, accept: 'text/event-stream' },
        });

        expect(mockFetch).toHaveBeenCalledWith(
          expect.any(String),
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        );
      });

      it('should abort the upstream fetch when the client disconnects mid-stream', async () => {
        // Live server: light-my-request can't simulate a mid-stream client
        // disconnect, so listen on a real port and destroy the raw socket.
        const liveApp = await buildTestApp(createMockContainer());
        await liveApp.listen({ port: 0 });
        const address = liveApp.server.address();
        const port = typeof address === 'object' && address !== null ? address.port : 0;
        const token = liveApp.jwt.sign({ userId: validUserObjectId, email: 'test@example.com' });

        let upstreamSignal: AbortSignal | undefined;
        let rejectPendingRead: ((err: Error) => void) | undefined;
        const stalledReader = {
          // Upstream never sends a byte — the handler parks on read()
          read: vi.fn(
            () =>
              new Promise((_, reject) => {
                rejectPendingRead = reject;
              }),
          ),
          // Mirror real fetch-body behavior: cancel() rejects the pending read
          cancel: vi.fn(async () => {
            rejectPendingRead?.(new Error('aborted'));
          }),
        };
        mockFetch.mockImplementation(async (_url: string, init: RequestInit) => {
          upstreamSignal = init.signal ?? undefined;
          return { ok: true, status: 200, body: { getReader: () => stalledReader } };
        });

        const clientReq = http.get({
          port,
          path: `/api/videos/${validVideoSummaryId}/stream`,
          headers: { authorization: `Bearer ${token}`, accept: 'text/event-stream' },
        });
        clientReq.on('error', () => undefined); // destroy() emits expected noise

        await vi.waitFor(() => expect(mockFetch).toHaveBeenCalled());
        clientReq.destroy();

        await vi.waitFor(() => {
          expect(upstreamSignal?.aborted).toBe(true);
        });
        expect(stalledReader.cancel).toHaveBeenCalled();

        await liveApp.close();
      });

      it('should handle reader cancellation errors gracefully', async () => {
        const mockCancel = vi.fn().mockRejectedValue(new Error('Cancel failed'));
        const mockReader = {
          read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
          cancel: mockCancel,
        };

        mockFetch.mockResolvedValue({
          ok: true,
          status: 200,
          body: {
            getReader: () => mockReader,
          },
        });

        // Should not throw even if cancel fails
        const response = await app.inject({
          method: 'GET',
          url: `/api/videos/${validVideoSummaryId}/stream`,
          headers: {
            authorization: authHeader,
            accept: 'text/event-stream',
          },
        });

        // Request should complete without error
        expect(response.statusCode).toBeLessThan(500);
      });
    });
  });
});
