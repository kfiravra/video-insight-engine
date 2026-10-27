import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import http from 'node:http';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, getAuthHeader, testUser, type MockContainer } from '../test/helpers.js';

// The shared mock container predates library mode, so it lacks
// `videoRepository.getUserVideos` and the library assistant-client methods.
// Augment a fresh container per suite without touching the shared helper.
type LibraryMockContainer = MockContainer & {
  videoRepository: MockContainer['videoRepository'] & {
    getUserVideos: ReturnType<typeof vi.fn>;
  };
  assistantClient: MockContainer['assistantClient'] & {
    libraryChat: ReturnType<typeof vi.fn>;
    librarySearch: ReturnType<typeof vi.fn>;
  };
};

function createLibraryMockContainer(): LibraryMockContainer {
  const base = createMockContainer();
  return {
    ...base,
    videoRepository: {
      ...base.videoRepository,
      getUserVideos: vi.fn().mockResolvedValue([]),
    },
    assistantClient: {
      ...base.assistantClient,
      libraryChat: vi.fn(),
      librarySearch: vi.fn(),
    },
  } as LibraryMockContainer;
}

describe('assistant library routes', () => {
  let app: FastifyInstance;
  let mockContainer: LibraryMockContainer;
  let authHeader: string;

  beforeAll(async () => {
    mockContainer = createLibraryMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    authHeader = await getAuthHeader(app);
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('POST /api/assistant/library/chat', () => {
    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        payload: { message: 'Hello' },
      });

      expect(response.statusCode).toBe(401);
      expect(mockContainer.assistantClient.libraryChat).not.toHaveBeenCalled();
    });

    it('should return 400 when message is missing', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: {},
      });

      expect(response.statusCode).toBe(400);
      expect(response.json().error).toBe('VALIDATION_ERROR');
      expect(mockContainer.assistantClient.libraryChat).not.toHaveBeenCalled();
    });

    it('should return 400 when message is empty string', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: { message: '' },
      });

      expect(response.statusCode).toBe(400);
      expect(response.json().error).toBe('VALIDATION_ERROR');
      expect(mockContainer.assistantClient.libraryChat).not.toHaveBeenCalled();
    });

    it('should resolve owned youtube ids via getUserVideos and forward exactly those', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([
        { youtubeId: 'vidA' },
        { youtubeId: 'vidB' },
        // Duplicate + empty should be de-duped / filtered out by the route.
        { youtubeId: 'vidA' },
        { youtubeId: '' },
      ]);

      const encoder = new TextEncoder();
      const sseData = 'data: {"type":"text","content":"Hi"}\n\ndata: [DONE]\n\n';
      const mockStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(sseData));
          controller.close();
        },
      });
      mockContainer.assistantClient.libraryChat.mockResolvedValue(mockStream);

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: {
          message: 'What did I learn?',
          conversationHistory: [{ role: 'user', content: 'Hi' }],
        },
      });

      expect(response.statusCode).toBe(200);
      expect(response.headers['content-type']).toBe('text/event-stream');
      expect(response.body).toBe(sseData);

      // Ownership derived server-side from the authenticated user id.
      expect(mockContainer.videoRepository.getUserVideos).toHaveBeenCalledWith(
        'test-user-id',
        undefined,
        { limit: 200 },
      );
      expect(mockContainer.assistantClient.libraryChat).toHaveBeenCalledWith(
        expect.objectContaining({
          youtubeIds: ['vidA', 'vidB'],
          message: 'What did I learn?',
          conversationHistory: [{ role: 'user', content: 'Hi' }],
          userId: 'test-user-id',
          requestId: expect.any(String),
        }),
      );
    });

    it('should ignore any client-supplied id list and derive ids from req.user (cross-user isolation)', async () => {
      // User B owns only vidB. Even though the request body smuggles user A's
      // ids, the route must derive the filter from the authenticated user.
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([
        { youtubeId: 'vidB' },
      ]);

      const encoder = new TextEncoder();
      const mockStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode('data: {"type":"done"}\n\n'));
          controller.close();
        },
      });
      mockContainer.assistantClient.libraryChat.mockResolvedValue(mockStream);

      await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        // Attacker-controlled fields that MUST be ignored by the gateway.
        payload: {
          message: 'leak A',
          video_ids: ['victimA1', 'victimA2'],
          youtubeIds: ['victimA1'],
          videoIds: ['victimA1'],
        },
      });

      const arg = mockContainer.assistantClient.libraryChat.mock.calls[0][0];
      expect(arg.youtubeIds).toEqual(['vidB']);
      expect(arg.youtubeIds).not.toContain('victimA1');
      expect(arg.youtubeIds).not.toContain('victimA2');
      expect(arg.userId).toBe('test-user-id');
    });

    it('should pass message without conversationHistory when not provided', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      const encoder = new TextEncoder();
      const mockStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode('data: {"type":"done"}\n\n'));
          controller.close();
        },
      });
      mockContainer.assistantClient.libraryChat.mockResolvedValue(mockStream);

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: { message: 'Summarize my library' },
      });

      expect(response.statusCode).toBe(200);
      expect(mockContainer.assistantClient.libraryChat).toHaveBeenCalledWith(
        expect.objectContaining({
          message: 'Summarize my library',
          conversationHistory: undefined,
        }),
      );
    });

    it('should forward the Fastify request id as the assistant requestId', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      const encoder = new TextEncoder();
      const mockStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode('data: {"type":"done"}\n\n'));
          controller.close();
        },
      });
      mockContainer.assistantClient.libraryChat.mockResolvedValue(mockStream);

      const incoming = 'edge-lib-chat-req-789xyz';
      await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader, 'x-request-id': incoming },
        payload: { message: 'hi' },
      });

      const arg = mockContainer.assistantClient.libraryChat.mock.calls[0][0];
      expect(arg.requestId).toBe(incoming);
    });

    it('should return 502 when assistant service is unavailable', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      const { ServiceUnavailableError } = await import('../utils/errors.js');
      mockContainer.assistantClient.libraryChat.mockRejectedValue(new ServiceUnavailableError('Assistant'));

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: { message: 'Explain my library' },
      });

      expect(response.statusCode).toBe(502);
      expect(response.json().error).toBe('SERVICE_UNAVAILABLE');
    });

    it('should return 429 RATE_LIMITED when the assistant rate-limits the user', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      const { RateLimitedError } = await import('../utils/errors.js');
      mockContainer.assistantClient.libraryChat.mockRejectedValue(new RateLimitedError());

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: { message: 'Explain my library' },
      });

      expect(response.statusCode).toBe(429);
      expect(response.json()).toEqual({
        error: 'RATE_LIMITED',
        message: 'Too many messages. Please wait a moment and try again.',
        statusCode: 429,
      });
    });

    it('should pass an abort signal tied to the request to the assistant client', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      const encoder = new TextEncoder();
      const mockStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode('data: {"type":"done"}\n\n'));
          controller.close();
        },
      });
      mockContainer.assistantClient.libraryChat.mockResolvedValue(mockStream);

      await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: { message: 'hi' },
      });

      expect(mockContainer.assistantClient.libraryChat).toHaveBeenCalledWith(
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    it('should abort the upstream assistant stream when the client disconnects mid-stream', async () => {
      // Live server: light-my-request can't simulate a mid-stream client
      // disconnect, so listen on a real port and destroy the raw socket.
      const liveContainer = createLibraryMockContainer();
      const liveApp = await buildTestApp(liveContainer);
      await liveApp.listen({ port: 0 });
      const address = liveApp.server.address();
      const port = typeof address === 'object' && address !== null ? address.port : 0;
      const token = liveApp.jwt.sign({ userId: testUser.userId, email: testUser.email, type: 'access' });

      liveContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      let upstreamSignal: AbortSignal | undefined;
      liveContainer.assistantClient.libraryChat.mockImplementation(
        async (opts: { signal?: AbortSignal }) => {
          upstreamSignal = opts.signal;
          // Upstream never sends a byte — the proxy parks on read(). Mirror a
          // real aborted fetch body: error the stream when the signal aborts
          // so the read loop exits.
          return new ReadableStream<Uint8Array>({
            start(controller) {
              opts.signal?.addEventListener(
                'abort',
                () => controller.error(new Error('aborted')),
                { once: true },
              );
            },
          });
        },
      );

      const body = JSON.stringify({ message: 'hello library' });
      const clientReq = http.request({
        port,
        method: 'POST',
        path: '/api/assistant/library/chat',
        headers: {
          authorization: `Bearer ${token}`,
          'content-type': 'application/json',
          'content-length': Buffer.byteLength(body),
        },
      });
      clientReq.on('error', () => undefined); // destroy() emits expected noise
      clientReq.end(body);

      await vi.waitFor(() => expect(liveContainer.assistantClient.libraryChat).toHaveBeenCalled());
      clientReq.destroy();

      await vi.waitFor(() => {
        expect(upstreamSignal?.aborted).toBe(true);
      });

      await liveApp.close();
    });

    it('should still forward all derived ids when the library is at the truncation cap', async () => {
      const atCap = Array.from({ length: 200 }, (_, i) => ({ youtubeId: `vid${i}` }));
      mockContainer.videoRepository.getUserVideos.mockResolvedValue(atCap);

      const encoder = new TextEncoder();
      const mockStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode('data: {"type":"done"}\n\n'));
          controller.close();
        },
      });
      mockContainer.assistantClient.libraryChat.mockResolvedValue(mockStream);

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/chat',
        headers: { authorization: authHeader },
        payload: { message: 'summarize everything' },
      });

      expect(response.statusCode).toBe(200);
      const arg = mockContainer.assistantClient.libraryChat.mock.calls[0][0];
      expect(arg.youtubeIds).toHaveLength(200);
    });
  });

  describe('POST /api/assistant/library/search', () => {
    const searchEnvelope = { results: [{ video_id: 'vidA', text: 'snippet', score: 0.9 }] };

    it('should return 401 without auth token', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/search',
        payload: { query: 'react hooks' },
      });

      expect(response.statusCode).toBe(401);
      expect(mockContainer.assistantClient.librarySearch).not.toHaveBeenCalled();
    });

    it('should return 400 when query is missing', async () => {
      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/search',
        headers: { authorization: authHeader },
        payload: {},
      });

      expect(response.statusCode).toBe(400);
      expect(response.json().error).toBe('VALIDATION_ERROR');
      expect(mockContainer.assistantClient.librarySearch).not.toHaveBeenCalled();
    });

    it('should resolve owned youtube ids and forward exactly those to librarySearch', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([
        { youtubeId: 'vidA' },
        { youtubeId: 'vidB' },
        { youtubeId: 'vidA' },
      ]);
      mockContainer.assistantClient.librarySearch.mockResolvedValue({
        status: 200,
        body: searchEnvelope,
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/search',
        headers: { authorization: authHeader },
        payload: { query: 'react hooks', topK: 5, sources: ['transcript'] },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual(searchEnvelope);

      expect(mockContainer.videoRepository.getUserVideos).toHaveBeenCalledWith(
        'test-user-id',
        undefined,
        { limit: 200 },
      );
      expect(mockContainer.assistantClient.librarySearch).toHaveBeenCalledWith(
        expect.objectContaining({
          youtubeIds: ['vidA', 'vidB'],
          query: 'react hooks',
          topK: 5,
          sources: ['transcript'],
          userId: 'test-user-id',
          requestId: expect.any(String),
        }),
      );
    });

    it('should ignore a client-supplied id list and derive ids from req.user (cross-user isolation)', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidB' }]);
      mockContainer.assistantClient.librarySearch.mockResolvedValue({
        status: 200,
        body: searchEnvelope,
      });

      await app.inject({
        method: 'POST',
        url: '/api/assistant/library/search',
        headers: { authorization: authHeader },
        payload: { query: 'leak', video_ids: ['victimA1'], youtubeIds: ['victimA1'] },
      });

      const arg = mockContainer.assistantClient.librarySearch.mock.calls[0][0];
      expect(arg.youtubeIds).toEqual(['vidB']);
      expect(arg.youtubeIds).not.toContain('victimA1');
      expect(arg.userId).toBe('test-user-id');
    });

    it('should propagate non-200 status from assistant', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);
      mockContainer.assistantClient.librarySearch.mockResolvedValue({
        status: 422,
        body: { error: 'bad query' },
      });

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/search',
        headers: { authorization: authHeader },
        payload: { query: 'react hooks' },
      });

      expect(response.statusCode).toBe(422);
      expect(response.json().error).toBe('bad query');
    });

    it('should return 502 when assistant service is unavailable', async () => {
      mockContainer.videoRepository.getUserVideos.mockResolvedValue([{ youtubeId: 'vidA' }]);

      const { ServiceUnavailableError } = await import('../utils/errors.js');
      mockContainer.assistantClient.librarySearch.mockRejectedValue(new ServiceUnavailableError('Assistant'));

      const response = await app.inject({
        method: 'POST',
        url: '/api/assistant/library/search',
        headers: { authorization: authHeader },
        payload: { query: 'react hooks' },
      });

      expect(response.statusCode).toBe(502);
      expect(response.json().error).toBe('SERVICE_UNAVAILABLE');
    });
  });
});
