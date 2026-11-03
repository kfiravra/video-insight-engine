import { describe, it, expect } from 'vitest';
import { ZodError, z } from 'zod';
import { resolveErrorEnvelope } from './error-envelope.js';
import {
  AccountBlockedError,
  InsufficientTokensError,
  MetadataUnavailableError,
  NotFoundError,
  RateLimitedError,
  TooManyRunsError,
  VideoTooLongError,
} from './errors.js';

describe('resolveErrorEnvelope', () => {
  it('should map Zod errors to 400 VALIDATION_ERROR with issues', () => {
    const parsed = z.object({ url: z.string().url() }).safeParse({ url: 123 });
    const error = (parsed as { error: ZodError }).error;

    const { statusCode, body } = resolveErrorEnvelope(error);

    expect(statusCode).toBe(400);
    expect(body).toMatchObject({ error: 'VALIDATION_ERROR', statusCode: 400 });
    expect(body.details?.issues[0]).toEqual({ path: 'url', message: expect.any(String) });
  });

  it('should map AppError subclasses to their status and code', () => {
    const { statusCode, body } = resolveErrorEnvelope(new NotFoundError('Video'));

    expect(statusCode).toBe(404);
    expect(body).toEqual({ error: 'NOT_FOUND', message: 'Video not found', statusCode: 404 });
  });

  it('should map BSONError to 400 INVALID_ID_FORMAT', () => {
    const error = new Error('bad id');
    error.name = 'BSONError';

    const { statusCode, body } = resolveErrorEnvelope(error);

    expect(statusCode).toBe(400);
    expect(body.error).toBe('INVALID_ID_FORMAT');
  });

  it('should prefer the envelope error string thrown by @fastify/rate-limit', () => {
    const error = Object.assign(new Error('Too many requests'), {
      statusCode: 429,
      error: 'RATE_LIMITED',
      code: 'FST_RATE_LIMIT',
    });

    const { statusCode, body } = resolveErrorEnvelope(error);

    expect(statusCode).toBe(429);
    expect(body.error).toBe('RATE_LIMITED');
  });

  it('should carry the wait a rate-limit builder put on the envelope it threw', () => {
    const error = Object.assign(new Error('Too many requests'), {
      statusCode: 429,
      error: 'RATE_LIMITED',
      retryAfterSec: 42,
    });

    expect(resolveErrorEnvelope(error).body).toMatchObject({ error: 'RATE_LIMITED', retryAfterSec: 42 });
  });

  it('should leave retryAfterSec out when a plugin error has none', () => {
    const error = Object.assign(new Error('Unauthorized'), { statusCode: 401, code: 'FST_JWT_BAD_REQUEST' });

    expect(resolveErrorEnvelope(error).body).not.toHaveProperty('retryAfterSec');
  });

  it('should send retryAfterSec on a RateLimitedError that knows it, and omit it otherwise', () => {
    const known = resolveErrorEnvelope(new RateLimitedError('Slow down.', 90)).body;
    const unknown = resolveErrorEnvelope(new RateLimitedError()).body;

    expect([known.retryAfterSec, 'retryAfterSec' in unknown]).toEqual([90, false]);
  });

  it('should fall back to the plugin code when no envelope string exists', () => {
    const error = Object.assign(new Error('Unauthorized'), {
      statusCode: 401,
      code: 'FST_JWT_NO_AUTHORIZATION_IN_HEADER',
    });

    const { body } = resolveErrorEnvelope(error);

    expect(body.error).toBe('FST_JWT_NO_AUTHORIZATION_IN_HEADER');
  });

  it('should treat a plugin statusCode of 500 as an unexpected error', () => {
    const error = Object.assign(new Error('kaboom'), { statusCode: 500 });

    const { statusCode, body } = resolveErrorEnvelope(error);

    expect(statusCode).toBe(500);
    expect(body.error).toBe('INTERNAL_ERROR');
  });

  it('should map unknown errors to 500 INTERNAL_ERROR', () => {
    const { statusCode, body } = resolveErrorEnvelope(new Error('kaboom'));

    expect(statusCode).toBe(500);
    expect(body).toEqual({ error: 'INTERNAL_ERROR', message: 'kaboom', statusCode: 500 });
  });

  describe('token gate', () => {
    it('should send 402 INSUFFICIENT_TOKENS with needed and available', () => {
      const { statusCode, body } = resolveErrorEnvelope(new InsufficientTokensError(840, 320));

      expect(statusCode).toBe(402);
      expect(body).toMatchObject({ error: 'INSUFFICIENT_TOKENS', needed: 840, available: 320 });
      expect(body).not.toHaveProperty('nextFreeAt');
    });

    it('should carry a negative balance for an account in debt', () => {
      const { body } = resolveErrorEnvelope(new InsufficientTokensError(1, -120));

      expect([body.needed, body.available]).toEqual([1, -120]);
    });

    it('should send 422 VIDEO_TOO_LONG with maxMinutes', () => {
      const { statusCode, body } = resolveErrorEnvelope(new VideoTooLongError(180));

      expect([statusCode, body.error, body.maxMinutes]).toEqual([422, 'VIDEO_TOO_LONG', 180]);
    });

    it('should send 403 ACCOUNT_BLOCKED', () => {
      const { statusCode, body } = resolveErrorEnvelope(new AccountBlockedError());

      expect([statusCode, body.error]).toEqual([403, 'ACCOUNT_BLOCKED']);
    });

    it('should send 429 TOO_MANY_RUNS with maxConcurrent', () => {
      const { statusCode, body } = resolveErrorEnvelope(new TooManyRunsError(2));

      expect([statusCode, body.error, body.maxConcurrent]).toEqual([429, 'TOO_MANY_RUNS', 2]);
    });

    it.each([
      ['not_found', 404],
      ['timeout', 503],
      ['upstream', 503],
      ['unsupported', 422],
    ] as const)('should send METADATA_UNAVAILABLE (%s) as %i', (reason, status) => {
      const { statusCode, body } = resolveErrorEnvelope(new MetadataUnavailableError(reason));

      expect([statusCode, body.error]).toEqual([status, 'METADATA_UNAVAILABLE']);
    });
  });
});
