import { ZodError } from 'zod';
import type { FastifyError } from 'fastify';
import { config } from '../config.js';
import {
  AppError,
  ChatLimitReachedError,
  InsufficientMinutesError,
  RateLimitedError,
  TooManyRunsError,
  VideoTooLongForPlanError,
} from './errors.js';

/**
 * The documented error body (docs/ERROR-HANDLING.md): `{ error, message,
 * statusCode }`, plus `details` when structured context exists (Zod issue
 * list), `nextAt` for the free chat limit and the minutes gate's numbers.
 */
export interface ErrorEnvelope {
  error: string;
  message: string;
  statusCode: number;
  details?: { issues: Array<{ path: string; message: string }> };
  nextAt?: string;
  /** INSUFFICIENT_MINUTES: the video's minutes, the paid minutes there are, when the free video reopens. */
  needed?: number;
  available?: number;
  nextFreeAt?: string;
  /** VIDEO_TOO_LONG_FOR_PLAN */
  maxMinutes?: number;
  /** TOO_MANY_RUNS */
  maxConcurrent?: number;
  /** RATE_LIMITED: whole seconds until the limiter lets the caller back in; clients build their own copy from it. */
  retryAfterSec?: number;
}

type BillingFields = Pick<
  ErrorEnvelope,
  'needed' | 'available' | 'nextFreeAt' | 'maxMinutes' | 'maxConcurrent' | 'retryAfterSec'
>;

/** The minutes gate's numbers and a limiter's wait, as top-level body fields (the web reads them from the body). */
function billingFields(error: AppError): BillingFields {
  if (error instanceof RateLimitedError) {
    return error.retryAfterSec === undefined ? {} : { retryAfterSec: error.retryAfterSec };
  }
  if (error instanceof InsufficientMinutesError) {
    const { needed, available, nextFreeAt } = error;
    return { needed, available, ...(nextFreeAt && { nextFreeAt }) };
  }
  if (error instanceof VideoTooLongForPlanError) return { maxMinutes: error.maxMinutes };
  if (error instanceof TooManyRunsError) return { maxConcurrent: error.maxConcurrent };
  return {};
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
      body: { error: error.code, message: error.message, statusCode: error.status, ...billingFields(error) },
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
    const retryAfterSec = pluginRetryAfterSec(error);
    return {
      statusCode: pluginStatus,
      body: {
        error: pluginErrorCode(error),
        message: error.message,
        statusCode: pluginStatus,
        ...(retryAfterSec !== undefined && { retryAfterSec }),
      },
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

/** The wait a rate-limit builder put on the thrown envelope (`rateLimitedBody`); other plugin errors have none. */
function pluginRetryAfterSec(error: HandledError): number | undefined {
  const seconds = (error as { retryAfterSec?: unknown }).retryAfterSec;
  return typeof seconds === 'number' && Number.isFinite(seconds) ? seconds : undefined;
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
