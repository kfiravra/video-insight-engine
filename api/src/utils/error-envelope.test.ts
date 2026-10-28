import { describe, it, expect } from 'vitest';
import { ZodError, z } from 'zod';
import { resolveErrorEnvelope } from './error-envelope.js';
import {
  AccountBlockedError,
  ChatLimitReachedError,
  InsufficientMinutesError,
  MetadataUnavailableError,
  NotFoundError,
  TooManyRunsError,
  VideoTooLongForPlanError,
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

  it('should carry nextAt for the free chat limit', () => {
    const { statusCode, body } = resolveErrorEnvelope(
      new ChatLimitReachedError(50, new Date('2026-11-02T10:00:00Z')),
    );

    expect(statusCode).toBe(429);
    expect(body).toMatchObject({
      error: 'CHAT_LIMIT_REACHED',
      statusCode: 429,
      nextAt: '2026-11-02T10:00:00.000Z',
    });
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

  describe('minutes gate', () => {
    it('should send 402 INSUFFICIENT_MINUTES with needed, available and nextFreeAt', () => {
      const { statusCode, body } = resolveErrorEnvelope(new InsufficientMinutesError(45, 20, '2026-11-02T10:00:00.000Z'));

      expect(statusCode).toBe(402);
      expect(body).toMatchObject({ error: 'INSUFFICIENT_MINUTES', needed: 45, available: 20, nextFreeAt: '2026-11-02T10:00:00.000Z' });
    });

    it('should leave nextFreeAt out when the free video cannot help', () => {
      const { body } = resolveErrorEnvelope(new InsufficientMinutesError(45, 0, null));

      expect(body).not.toHaveProperty('nextFreeAt');
    });

    it('should send 422 VIDEO_TOO_LONG_FOR_PLAN with maxMinutes', () => {
      const { statusCode, body } = resolveErrorEnvelope(new VideoTooLongForPlanError(180));

      expect([statusCode, body.error, body.maxMinutes]).toEqual([422, 'VIDEO_TOO_LONG_FOR_PLAN', 180]);
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
