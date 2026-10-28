import { ZodError } from 'zod';
import type { FastifyError } from 'fastify';
import { config } from '../config.js';
import { AppError, ChatLimitReachedError, DailyLimitReachedError } from './errors.js';

/**
 * The documented error body (docs/ERROR-HANDLING.md): `{ error, message,
 * statusCode }`, plus `details` when structured context exists (Zod issue
 * list), the `resetAt`/`limitUsd` extras for the daily cost cap and `nextAt`
 * for the free chat limit.
 */
export interface ErrorEnvelope {
  error: string;
  message: string;
  statusCode: number;
  details?: { issues: Array<{ path: string; message: string }> };
  resetAt?: string;
  limitUsd?: number;
  nextAt?: string;
}

export interface ResolvedError {
  statusCode: number;
  body: ErrorEnvelope;
}

/** Anything a Fastify error handler can receive — plugins throw plain objects with a statusCode. */
export type HandledError = Error | FastifyError;

/**
 * Map a thrown error to its HTTP status + envelope. Pure: no logging, no
 * reply — the handler in app.ts owns those, so every branch (including the
 * 4xx ones that used to `return` before any log line) gets logged uniformly.
 */
export function resolveErrorEnvelope(error: HandledError): ResolvedError {
  if (error instanceof ZodError) {
    return {
      statusCode: 400,
      body: {
        error: 'VALIDATION_ERROR',
        message: error.errors[0]?.message || 'Invalid input',
        statusCode: 400,
        details: {
          issues: error.errors.map((issue) => ({
            path: issue.path.join('.'),
            message: issue.message,
          })),
        },
      },
    };
  }

  // Daily cost limit reached — surface resetAt for the UI countdown
  if (error instanceof DailyLimitReachedError) {
    return {
      statusCode: error.status,
      body: {
        error: error.code,
        message: error.message,
        statusCode: error.status,
        resetAt: error.resetAt,
        limitUsd: error.limitUsd,
      },
    };
  }

  // Free chat limit reached — surface nextAt so the UI can say when chat reopens
  if (error instanceof ChatLimitReachedError) {
    return {
      statusCode: error.status,
      body: {
        error: error.code,
        message: error.message,
        statusCode: error.status,
        nextAt: error.nextAt,
      },
    };
  }

  if (error instanceof AppError) {
    return {
      statusCode: error.status,
      body: { error: error.code, message: error.message, statusCode: error.status },
    };
  }

  // MongoDB BSONError (invalid ObjectId format)
  if (error.name === 'BSONError') {
    return {
      statusCode: 400,
      body: { error: 'INVALID_ID_FORMAT', message: 'Invalid ID format', statusCode: 400 },
    };
  }

  const pluginStatus = pluginStatusCode(error);
  if (pluginStatus !== undefined) {
    return {
      statusCode: pluginStatus,
      body: { error: pluginErrorCode(error), message: error.message, statusCode: pluginStatus },
    };
  }

  // Don't expose internal error details in production
  const message = config.NODE_ENV === 'production' ? 'Internal server error' : error.message;
  return {
    statusCode: 500,
    body: { error: 'INTERNAL_ERROR', message, statusCode: 500 },
  };
}

/** Fastify plugin errors (rate-limit, auth, …) carry their own non-500 statusCode. */
function pluginStatusCode(error: HandledError): number | undefined {
  const statusCode = (error as { statusCode?: unknown }).statusCode;
  return typeof statusCode === 'number' && statusCode !== 500 ? statusCode : undefined;
}

/**
 * @fastify/rate-limit THROWS its errorResponseBuilder result, so the documented
 * envelope arrives here as `{ error, message, statusCode }`. Prefer that `error`
 * string over the generic fallback — without this every 429 left the API as
 * `error: 'ERROR'` and broke client-side error-code maps expecting RATE_LIMITED.
 */
function pluginErrorCode(error: HandledError): string {
  const envelopeCode = (error as { error?: unknown }).error;
  if (typeof envelopeCode === 'string') {
    return envelopeCode;
  }
  const code = (error as { code?: unknown }).code;
  return typeof code === 'string' ? code : 'ERROR';
}
