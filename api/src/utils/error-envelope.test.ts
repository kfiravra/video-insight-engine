import { describe, it, expect } from 'vitest';
import { ZodError, z } from 'zod';
import { resolveErrorEnvelope } from './error-envelope.js';
import { ChatLimitReachedError, DailyLimitReachedError, NotFoundError } from './errors.js';

describe('resolveErrorEnvelope', () => {
  it('should map Zod errors to 400 VALIDATION_ERROR with issues', () => {
    const parsed = z.object({ url: z.string().url() }).safeParse({ url: 123 });
    const error = (parsed as { error: ZodError }).error;

    const { statusCode, body } = resolveErrorEnvelope(error);

    expect(statusCode).toBe(400);
    expect(body).toMatchObject({ error: 'VALIDATION_ERROR', statusCode: 400 });
    expect(body.details?.issues[0]).toEqual({ path: 'url', message: expect.any(String) });
  });

  it('should carry resetAt and limitUsd for the daily cost cap', () => {
    const { statusCode, body } = resolveErrorEnvelope(
      new DailyLimitReachedError(1.5, '2026-08-27T00:00:00.000Z'),
    );

    expect(statusCode).toBe(429);
    expect(body).toMatchObject({
      error: 'DAILY_LIMIT_REACHED',
      statusCode: 429,
      resetAt: '2026-08-27T00:00:00.000Z',
      limitUsd: 1.5,
    });
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
});
