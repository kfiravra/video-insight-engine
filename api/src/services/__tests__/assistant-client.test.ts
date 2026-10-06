import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { AssistantClient } from '../assistant-client.js';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const mockLogger = {
  info: vi.fn(),
  error: vi.fn(),
  warn: vi.fn(),
  debug: vi.fn(),
  trace: vi.fn(),
  fatal: vi.fn(),
  child: vi.fn(() => mockLogger),
  level: 'silent',
  silent: vi.fn(),
} as unknown as FastifyBaseLogger;

function jsonResponse<T>(status: number, body: T): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('AssistantClient', () => {
  let client: AssistantClient;

  beforeEach(() => {
    vi.clearAllMocks();
    client = new AssistantClient(mockLogger);
  });

  describe('conversation history forwarding', () => {
    const sseResponse = () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('data: {}\n\n'));
            controller.close();
          },
        }),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      );
    const history: Array<{ role: 'user' | 'assistant'; content: string }> = [
      { role: 'user', content: 'Generate an app from this URL' },
      { role: 'assistant', content: '' },
      { role: 'assistant', content: '   ' },
      { role: 'user', content: 'Yes, do it' },
    ];
    const expected = [
      { role: 'user', content: 'Generate an app from this URL' },
      { role: 'user', content: 'Yes, do it' },
    ];

    it('should drop blank turns from the library chat history when confirming an action', async () => {
      mockFetch.mockResolvedValueOnce(sseResponse());

      await client.libraryChat({
        userId: 'user-1',
        youtubeIds: [],
        message: 'Yes, do it',
        conversationHistory: history,
        confirmToken: 'tok_abcdefghijklmnop',
      });

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.conversation_history).toEqual(expected);
    });

    it('should drop blank turns from the single-video chat history', async () => {
      mockFetch.mockResolvedValueOnce(sseResponse());

      await client.chat({ videoId: 'vid-1', message: 'hi', conversationHistory: history });

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.conversation_history).toEqual(expected);
    });
  });

  describe('chat — request-id propagation', () => {
    it('should forward X-Request-ID header when supplied', async () => {
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {}\n\n'));
          controller.close();
        },
      });
      mockFetch.mockResolvedValueOnce(
        new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
      );

      await client.chat({
        videoId: 'vid-1',
        message: 'hi',
        requestId: 'req-chat-abcdef12',
      });

      const [, options] = mockFetch.mock.calls[0];
      expect(options.headers).toMatchObject({
        'Content-Type': 'application/json',
        'X-Internal-Secret': expect.any(String),
        'X-Request-ID': 'req-chat-abcdef12',
      });
    });

    it('should omit X-Request-ID header when not supplied', async () => {
      const stream = new ReadableStream({
        start(controller) {
          controller.close();
        },
      });
      mockFetch.mockResolvedValueOnce(
        new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
      );

      await client.chat({ videoId: 'vid-1', message: 'hi' });

      const [, options] = mockFetch.mock.calls[0];
      expect(options.headers).not.toHaveProperty('X-Request-ID');
    });
  });

  describe('action — request-id propagation', () => {
    it('should forward X-Request-ID header when supplied', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse(200, {
          success: true,
          action: 'save_note',
          data: { ok: true },
          error: null,
          trace_id: 'tid-1',
        }),
      );

      await client.action({
        videoId: 'vid-1',
        userId: 'user-1',
        action: 'save_note',
        requestId: 'req-action-deadbeef',
      });

      const [, options] = mockFetch.mock.calls[0];
      expect(options.headers).toMatchObject({
        'X-Internal-Secret': expect.any(String),
        'X-User-Id': 'user-1',
        'X-Request-ID': 'req-action-deadbeef',
      });
    });

    it('should omit X-Request-ID header when not supplied', async () => {
      mockFetch.mockResolvedValueOnce(
        jsonResponse(200, {
          success: true,
          action: 'save_note',
          data: null,
          error: null,
          trace_id: 'tid-2',
        }),
      );

      await client.action({
        videoId: 'vid-1',
        userId: 'user-1',
        action: 'save_note',
      });

      const [, options] = mockFetch.mock.calls[0];
      expect(options.headers).not.toHaveProperty('X-Request-ID');
    });
  });
});
