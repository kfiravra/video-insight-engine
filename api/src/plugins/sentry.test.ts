import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';

const captureExceptionMock = vi.fn();
const initMock = vi.fn();
// Track every scope the plugin actually populated so tests can assert on the
// real scope state, not a freshly-allocated one. Prevents a class of
// false-positive tests where re-invoking the callback masks a regression.
const builtScopes: FakeScope[] = [];
const withScopeMock = vi.fn((cb: (scope: FakeScope) => void) => {
  const scope = new FakeScope();
  builtScopes.push(scope);
  cb(scope);
});

class FakeScope {
  tags: Record<string, string | undefined> = {};
  user: { id?: string } | null = null;
  contextValues: Record<string, unknown> = {};
  setTag(key: string, value: string | undefined): void {
    this.tags[key] = value;
  }
  setUser(user: { id?: string } | null): void {
    this.user = user;
  }
  setContext(key: string, value: unknown): void {
    this.contextValues[key] = value;
  }
}

vi.mock('@sentry/node', () => ({
  init: (...args: unknown[]) => initMock(...args),
  captureException: (...args: unknown[]) => captureExceptionMock(...args),
  withScope: (cb: (scope: FakeScope) => void) => withScopeMock(cb),
  // No-op flush so the plugin's onClose hook is safe in tests.
  close: vi.fn().mockResolvedValue(true),
  flush: vi.fn().mockResolvedValue(true),
}));

// Import AFTER the mock so the module picks up the mocked SDK.
import {
  effectiveStatusCode,
  initSentry,
  scrubBeforeSend,
  sentryFastifyPlugin,
  type SentryEnvelope,
} from './sentry.js';
import { requestIdPlugin, genRequestId } from './request-id.js';
import { z, ZodError } from 'zod';
import {
  NotFoundError,
  ValidationError,
  AccountDeletionPendingError,
  AccountAlreadyDeletedError,
  ServiceUnavailableError,
} from '../utils/errors.js';

describe('scrubBeforeSend (PII filter)', () => {
  function eventWith(overrides: Partial<SentryEnvelope>): SentryEnvelope {
    return {
      request: { headers: {}, data: undefined },
      user: undefined,
      ...overrides,
    };
  }

  it('should strip the Authorization header', () => {
    const event = scrubBeforeSend(
      eventWith({ request: { headers: { authorization: 'Bearer secret-token-xyz' } } }),
    );
    expect(event?.request?.headers?.authorization).toBe('[Filtered]');
  });

  it('should strip the cookie header', () => {
    const event = scrubBeforeSend(
      eventWith({ request: { headers: { cookie: 'session=abcdef' } } }),
    );
    expect(event?.request?.headers?.cookie).toBe('[Filtered]');
  });

  it('should strip internal-secret headers', () => {
    const event = scrubBeforeSend(
      eventWith({ request: { headers: { 'x-internal-secret': 'shh' } } }),
    );
    expect(event?.request?.headers?.['x-internal-secret']).toBe('[Filtered]');
  });

  it('should drop email addresses from user metadata', () => {
    const event = scrubBeforeSend(
      eventWith({ user: { id: 'user-1', email: 'alice@example.com', username: 'alice' } }),
    );
    expect(event?.user?.email).toBeUndefined();
    expect(event?.user?.id).toBe('user-1');
  });

  it('should redact emails embedded in request payloads', () => {
    const event = scrubBeforeSend(
      eventWith({
        request: {
          headers: {},
          data: { contactEmail: 'bob@example.com', note: 'reach me at carl@example.com please' },
        },
      }),
    );
    const data = event?.request?.data as Record<string, unknown>;
    expect(String(data?.contactEmail)).toBe('[email]');
    expect(String(data?.note)).toContain('[email]');
  });

  it('should return null when the event is empty', () => {
    const event = scrubBeforeSend(null as unknown as SentryEnvelope);
    expect(event).toBeNull();
  });

  it('should strip JWT tokens from request.url query strings', () => {
    const event = scrubBeforeSend(
      eventWith({
        request: {
          headers: {},
          url: 'wss://example.com/ws/123?token=eyJhbGc.payload.sig&foo=bar',
        },
      }),
    );
    expect(event?.request?.url).toBe('wss://example.com/ws/123?token=[Filtered]&foo=bar');
  });

  it('should strip credentials from request.query_string', () => {
    const event = scrubBeforeSend(
      eventWith({
        request: {
          headers: {},
          query_string: 'access_token=secret&page=2',
        },
      }),
    );
    expect(event?.request?.query_string).toBe('access_token=[Filtered]&page=2');
  });

  it('should drop user.id when it embeds an email', () => {
    const event = scrubBeforeSend(
      eventWith({ user: { id: 'eve@example.com', username: 'eve' } }),
    );
    expect(event?.user?.id).toBeUndefined();
    expect(event?.user?.username).toBe('eve');
  });

  it('should redact emails in event.extra', () => {
    const event = scrubBeforeSend(
      eventWith({ extra: { note: 'forward to ops@example.com' } }),
    );
    const extra = event?.extra as Record<string, string>;
    expect(extra.note).toBe('forward to [email]');
  });

  it('should redact emails and tokens in exception messages', () => {
    const event = scrubBeforeSend(
      eventWith({
        exception: {
          values: [
            { value: 'auth failed at /ws?token=eyJfoo for dan@example.com' },
          ],
        },
      }),
    );
    expect(event?.exception?.values?.[0].value).toBe(
      'auth failed at /ws?token=[Filtered] for [email]',
    );
  });
});

describe('initSentry', () => {
  beforeEach(() => {
    initMock.mockClear();
  });

  it('should no-op when DSN is empty', () => {
    const enabled = initSentry({ dsn: '', environment: 'test', release: 'abc' });
    expect(enabled).toBe(false);
    expect(initMock).not.toHaveBeenCalled();
  });

  it('should call Sentry.init when DSN is provided', () => {
    const enabled = initSentry({
      dsn: 'https://abc@sentry.io/1',
      environment: 'production',
      release: 'sha-deadbeef',
      tracesSampleRate: 0.1,
    });
    expect(enabled).toBe(true);
    expect(initMock).toHaveBeenCalledTimes(1);
    const arg = initMock.mock.calls[0][0];
    expect(arg.dsn).toBe('https://abc@sentry.io/1');
    expect(arg.environment).toBe('production');
    expect(arg.release).toBe('sha-deadbeef');
    expect(arg.tracesSampleRate).toBe(0.1);
    expect(typeof arg.beforeSend).toBe('function');
  });

  it('should tag the service name so cross-service events are filterable', () => {
    initSentry({ dsn: 'https://abc@sentry.io/1', environment: 'test', release: 'r' });
    const arg = initMock.mock.calls[0][0];
    expect(arg.initialScope?.tags?.service).toBe('vie-api');
  });
});

describe('sentryFastifyPlugin', () => {
  let app: FastifyInstance;

  beforeEach(() => {
    captureExceptionMock.mockClear();
    withScopeMock.mockClear();
    builtScopes.length = 0;
  });

  afterEach(async () => {
    if (app) await app.close();
  });

  async function buildAppWithSentry(): Promise<FastifyInstance> {
    const fastify = Fastify({
      requestIdHeader: false,
      requestIdLogLabel: 'requestId',
      genReqId: genRequestId,
      logger: false,
    });
    await fastify.register(requestIdPlugin);
    await fastify.register(sentryFastifyPlugin);

    fastify.get('/ok', async () => ({ ok: true }));
    fastify.get('/boom', async () => {
      throw new Error('intentional');
    });
    fastify.get('/auth-fail', async (_req, reply) => {
      return reply.status(401).send({ error: 'UNAUTHORIZED', message: 'no token' });
    });
    // Throw paths covering the three error shapes that flow through `onError`:
    //   - AppError subclass with 4xx `status`
    //   - ZodError with no statusCode (parser-level throws)
    //   - AppError subclass with 5xx `status` (must still be captured)
    fastify.get('/throw-notfound', async () => {
      throw new NotFoundError('Video');
    });
    fastify.get('/throw-validation', async () => {
      throw new ValidationError('bad input');
    });
    fastify.get('/throw-deletion-pending', async () => {
      throw new AccountDeletionPendingError();
    });
    fastify.get('/throw-already-deleted', async () => {
      throw new AccountAlreadyDeletedError();
    });
    fastify.get('/throw-zod', async () => {
      const schema = z.object({ id: z.string().uuid() });
      schema.parse({ id: 'not-a-uuid' });
    });
    fastify.get('/throw-service-unavailable', async () => {
      // 503 — must be captured as a server-side failure.
      throw new ServiceUnavailableError('assistant');
    });
    return fastify;
  }

  it('should capture unhandled exceptions and tag them with requestId, method, and route', async () => {
    app = await buildAppWithSentry();
    const res = await app.inject({
      method: 'GET',
      url: '/boom',
      headers: { 'x-request-id': 'plugin-test-abc12345' },
    });

    expect(res.statusCode).toBe(500);
    expect(captureExceptionMock).toHaveBeenCalledTimes(1);
    // Assert on the scope the plugin actually populated, not a freshly
    // built one — re-invoking the callback would mask any regression that
    // moved the scope writes elsewhere.
    expect(builtScopes).toHaveLength(1);
    const scope = builtScopes[0];
    expect(scope.tags.requestId).toBe('plugin-test-abc12345');
    expect(scope.tags.method).toBe('GET');
    expect(scope.tags.route).toBe('/boom');
  });

  it('should not capture handled 4xx responses', async () => {
    app = await buildAppWithSentry();
    const res = await app.inject({ method: 'GET', url: '/auth-fail' });

    expect(res.statusCode).toBe(401);
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  it('should not capture successful responses', async () => {
    app = await buildAppWithSentry();
    const res = await app.inject({ method: 'GET', url: '/ok' });

    expect(res.statusCode).toBe(200);
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  // Regression: AppError subclasses use `.status`, NOT `.statusCode`. The
  // previous filter checked only `error.statusCode`, so every 4xx domain
  // throw was captured.
  it.each([
    ['/throw-notfound', 404],
    ['/throw-validation', 400],
    ['/throw-deletion-pending', 403],
    ['/throw-already-deleted', 409],
  ])('should NOT capture %s (4xx AppError throw)', async (url, expected) => {
    app = await buildAppWithSentry();
    const res = await app.inject({ method: 'GET', url });
    expect(res.statusCode).toBe(expected);
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  // Regression: ZodError has no statusCode field; the plugin must classify it
  // as a 4xx so it's not captured. (This standalone test app has no global
  // error handler, so Fastify itself maps the thrown ZodError to 500 — we
  // only care here that Sentry skips it.)
  it('should NOT capture ZodError thrown by route-boundary parsing', async () => {
    app = await buildAppWithSentry();
    await app.inject({ method: 'GET', url: '/throw-zod' });
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  // 5xx AppError MUST still be captured — these are genuine server failures.
  it('should capture 5xx AppError throws (ServiceUnavailableError → 503)', async () => {
    app = await buildAppWithSentry();
    const res = await app.inject({ method: 'GET', url: '/throw-service-unavailable' });
    expect(res.statusCode).toBe(503);
    expect(captureExceptionMock).toHaveBeenCalledTimes(1);
  });
});

describe('effectiveStatusCode', () => {
  it('returns AppError.status', () => {
    expect(effectiveStatusCode(new NotFoundError('x'))).toBe(404);
    expect(effectiveStatusCode(new ValidationError('y'))).toBe(400);
    expect(effectiveStatusCode(new AccountAlreadyDeletedError())).toBe(409);
    expect(effectiveStatusCode(new ServiceUnavailableError('assistant'))).toBe(503);
  });

  it('returns 400 for ZodError', () => {
    const z1 = new ZodError([]);
    expect(effectiveStatusCode(z1)).toBe(400);
  });

  it('falls back to Fastify-style statusCode', () => {
    const err = Object.assign(new Error('rate limited'), { statusCode: 429 });
    expect(effectiveStatusCode(err)).toBe(429);
  });

  it('defaults to 500 for unknown shapes', () => {
    expect(effectiveStatusCode(new Error('mystery'))).toBe(500);
    expect(effectiveStatusCode(null)).toBe(500);
    expect(effectiveStatusCode('plain string')).toBe(500);
  });
});
