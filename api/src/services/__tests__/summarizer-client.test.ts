import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { SummarizerClient, SummarizeRequest } from '../summarizer-client.js';

// Mock the global fetch
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Mock logger for tests
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

describe('SummarizerClient', () => {
  let client: SummarizerClient;
  const baseRequest: SummarizeRequest = {
    videoSummaryId: 'summary-123',
    youtubeId: 'dQw4w9WgXcQ',
    url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    userId: 'user-123',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    client = new SummarizerClient(mockLogger);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('triggerSummarization', () => {
    describe('successful request', () => {
      it('should make HTTP POST request to summarizer service', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        // Allow the async IIFE to execute
        await new Promise(resolve => setImmediate(resolve));

        expect(mockFetch).toHaveBeenCalledWith(
          'http://localhost:8000/summarize',
          expect.objectContaining({
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(baseRequest),
          })
        );
      });

      it('should log success when summarization is triggered successfully', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        await new Promise(resolve => setImmediate(resolve));

        expect(mockLogger.info).toHaveBeenCalledWith(
          { youtubeId: baseRequest.youtubeId },
          'Summarization triggered successfully'
        );
      });

      it('should include providers in request when specified', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        const requestWithProviders: SummarizeRequest = {
          ...baseRequest,
          providers: {
            default: 'anthropic',
            fast: 'openai',
            fallback: 'gemini',
          },
        };

        client.triggerSummarization(requestWithProviders);

        await new Promise(resolve => setImmediate(resolve));

        expect(mockFetch).toHaveBeenCalledWith(
          expect.any(String),
          expect.objectContaining({
            body: JSON.stringify(requestWithProviders),
          })
        );
      });
    });

    describe('run ids (P0b)', () => {
      it('should send the run id, pipeline version and bypass flag in the body when given', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        client.triggerSummarization({ ...baseRequest, runId: 'run-h-1', pipelineVersion: 'v11', bypassCache: true });
        await new Promise(resolve => setImmediate(resolve));

        const body = JSON.parse(mockFetch.mock.calls[0][1].body as string);
        expect([body.runId, body.pipelineVersion, body.bypassCache]).toEqual(['run-h-1', 'v11', true]);
      });
    });

    describe('retry logic', () => {
      beforeEach(() => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
      });

      it('should retry on failure with exponential backoff', async () => {
        mockFetch
          .mockRejectedValueOnce(new Error('Network error'))
          .mockRejectedValueOnce(new Error('Network error'))
          .mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        // First attempt
        await vi.advanceTimersByTimeAsync(0);
        expect(mockFetch).toHaveBeenCalledTimes(1);
        expect(mockLogger.warn).toHaveBeenCalledWith(
          { attempt: 1, maxRetries: 3, error: 'Network error' },
          'Summarization request failed'
        );

        // Wait for first retry delay (1000ms)
        await vi.advanceTimersByTimeAsync(1000);
        expect(mockFetch).toHaveBeenCalledTimes(2);

        // Wait for second retry delay (2000ms exponential backoff)
        await vi.advanceTimersByTimeAsync(2000);
        expect(mockFetch).toHaveBeenCalledTimes(3);

        expect(mockLogger.info).toHaveBeenCalledWith(
          { youtubeId: baseRequest.youtubeId },
          'Summarization triggered successfully'
        );
      });

      it('should retry on non-OK response status', async () => {
        mockFetch
          .mockResolvedValueOnce({ ok: false, status: 500 })
          .mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        await vi.advanceTimersByTimeAsync(0);
        expect(mockFetch).toHaveBeenCalledTimes(1);

        await vi.advanceTimersByTimeAsync(1000);
        expect(mockFetch).toHaveBeenCalledTimes(2);

        expect(mockLogger.info).toHaveBeenCalledWith(
          { youtubeId: baseRequest.youtubeId },
          'Summarization triggered successfully'
        );
      });

      it('should log error after max retries exhausted', async () => {
        mockFetch.mockRejectedValue(new Error('Persistent network error'));

        client.triggerSummarization(baseRequest);

        // First attempt
        await vi.advanceTimersByTimeAsync(0);
        // First retry after 1s
        await vi.advanceTimersByTimeAsync(1000);
        // Second retry after 2s
        await vi.advanceTimersByTimeAsync(2000);
        // Ensure all async work completes
        await vi.advanceTimersByTimeAsync(4000);

        expect(mockFetch).toHaveBeenCalledTimes(3);
        expect(mockLogger.error).toHaveBeenCalledWith(
          { maxRetries: 3, error: 'Persistent network error' },
          'Failed to trigger summarization after retries'
        );
      });

      it('should use exponential backoff with correct delays', async () => {
        // First attempt fails, then second fails, then third fails
        mockFetch.mockRejectedValue(new Error('Network error'));

        client.triggerSummarization(baseRequest);

        // First attempt at t=0
        await vi.advanceTimersByTimeAsync(0);
        expect(mockFetch).toHaveBeenCalledTimes(1);

        // Retry after 1s (2^0 * 1000)
        await vi.advanceTimersByTimeAsync(1000);
        expect(mockFetch).toHaveBeenCalledTimes(2);

        // Retry after 2s (2^1 * 1000)
        await vi.advanceTimersByTimeAsync(2000);
        expect(mockFetch).toHaveBeenCalledTimes(3);
      });
    });

    describe('timeout handling', () => {
      it('should handle AbortError and log timeout', async () => {
        const abortError = new Error('Aborted');
        abortError.name = 'AbortError';
        mockFetch.mockRejectedValueOnce(abortError).mockResolvedValueOnce({ ok: true });

        vi.useFakeTimers({ shouldAdvanceTime: true });
        client.triggerSummarization(baseRequest);

        await vi.advanceTimersByTimeAsync(0);

        expect(mockLogger.warn).toHaveBeenCalledWith(
          { attempt: 1, maxRetries: 3 },
          'Summarization request timed out'
        );
      });

      it('should retry after timeout error', async () => {
        const abortError = new Error('Aborted');
        abortError.name = 'AbortError';
        mockFetch.mockRejectedValueOnce(abortError).mockResolvedValueOnce({ ok: true });

        vi.useFakeTimers({ shouldAdvanceTime: true });
        client.triggerSummarization(baseRequest);

        await vi.advanceTimersByTimeAsync(0);
        await vi.advanceTimersByTimeAsync(1000);

        expect(mockLogger.info).toHaveBeenCalledWith(
          { youtubeId: baseRequest.youtubeId },
          'Summarization triggered successfully'
        );
      });

      it('should pass abort signal to fetch for timeout support', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        await new Promise(resolve => setImmediate(resolve));

        expect(mockFetch).toHaveBeenCalledWith(
          expect.any(String),
          expect.objectContaining({
            signal: expect.any(AbortSignal),
          })
        );
      });
    });

    describe('error transformation', () => {
      beforeEach(() => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
      });

      // TODO: Fix timer interaction with fire-and-forget async code
      // The fake timer doesn't properly flush promises in the async IIFE
      it.skip('should convert non-Error thrown values to Error messages', async () => {
        mockFetch.mockRejectedValueOnce('string error').mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        await vi.advanceTimersByTimeAsync(0);

        expect(mockLogger.warn).toHaveBeenCalledWith(
          { attempt: 1, maxRetries: 3, error: 'string error' },
          'Summarization request failed'
        );
      });

      // TODO: Fix timer interaction with fire-and-forget retry logic
      it.skip('should handle HTTP error responses and retry', async () => {
        mockFetch
          .mockResolvedValueOnce({ ok: false, status: 503 })
          .mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        // First attempt fails with 503
        await vi.advanceTimersByTimeAsync(0);
        expect(mockFetch).toHaveBeenCalledTimes(1);

        // Second attempt succeeds after retry delay
        await vi.advanceTimersByTimeAsync(1000);
        expect(mockFetch).toHaveBeenCalledTimes(2);
        expect(mockLogger.info).toHaveBeenCalledWith(
          { youtubeId: baseRequest.youtubeId },
          'Summarization triggered successfully'
        );
      });
    });

    describe('fire and forget behavior', () => {
      it('should return immediately without blocking', () => {
        // Create a promise that never resolves to simulate slow request
        mockFetch.mockReturnValue(new Promise(() => {}));

        const startTime = performance.now();
        client.triggerSummarization(baseRequest);
        const endTime = performance.now();

        // Should return immediately (within 5ms)
        expect(endTime - startTime).toBeLessThan(5);
      });

      it('should execute async without throwing to caller', () => {
        mockFetch.mockRejectedValue(new Error('Error'));

        // This should not throw
        expect(() => client.triggerSummarization(baseRequest)).not.toThrow();
      });
    });

    describe('request content', () => {
      it('should send correct request body structure', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        const request: SummarizeRequest = {
          videoSummaryId: 'vid-456',
          youtubeId: 'abc123XYZ',
          url: 'https://youtu.be/abc123XYZ',
          userId: 'user-789',
        };

        client.triggerSummarization(request);

        await new Promise(resolve => setImmediate(resolve));

        const [url, options] = mockFetch.mock.calls[0];
        expect(url).toBe('http://localhost:8000/summarize');
        expect(options.method).toBe('POST');
        expect(options.headers).toEqual({ 'Content-Type': 'application/json' });
        expect(JSON.parse(options.body)).toEqual(request);
      });

      it('should forward X-Request-ID header when requestId is supplied', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        const request: SummarizeRequest = {
          ...baseRequest,
          requestId: 'req-edge-abc123def',
        };

        client.triggerSummarization(request);
        await new Promise(resolve => setImmediate(resolve));

        const [, options] = mockFetch.mock.calls[0];
        expect(options.headers).toMatchObject({
          'Content-Type': 'application/json',
          'X-Request-ID': 'req-edge-abc123def',
        });
        // requestId is sent as a header, not in the body — keeps the payload
        // compatible with the existing Pydantic schema on the summarizer side.
        expect(JSON.parse(options.body).requestId).toBeUndefined();
      });

      it('should omit X-Request-ID header when requestId is not supplied', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);
        await new Promise(resolve => setImmediate(resolve));

        const [, options] = mockFetch.mock.calls[0];
        expect(options.headers).not.toHaveProperty('X-Request-ID');
      });

      it('should handle request without userId', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        const request: SummarizeRequest = {
          videoSummaryId: 'vid-456',
          youtubeId: 'abc123XYZ',
          url: 'https://youtu.be/abc123XYZ',
        };

        client.triggerSummarization(request);

        await new Promise(resolve => setImmediate(resolve));

        const [, options] = mockFetch.mock.calls[0];
        const body = JSON.parse(options.body);
        expect(body.userId).toBeUndefined();
      });

      it('should use configured SUMMARIZER_URL', async () => {
        mockFetch.mockResolvedValueOnce({ ok: true });

        client.triggerSummarization(baseRequest);

        await new Promise(resolve => setImmediate(resolve));

        expect(mockFetch).toHaveBeenCalledWith(
          'http://localhost:8000/summarize',
          expect.any(Object)
        );
      });
    });
  });
});
