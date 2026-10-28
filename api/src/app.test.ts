import { describe, it, expect, vi } from 'vitest';
import { buildApp, defaultLoggerOptions } from './app.js';

/** A pino destination that keeps every emitted line for assertions. */
function captureLogs() {
  const lines: Array<Record<string, unknown>> = [];
  const stream = {
    write(chunk: string) {
      lines.push(JSON.parse(chunk) as Record<string, unknown>);
    },
  };
  return { lines, stream };
}

describe('buildApp', () => {
  describe('with container override', () => {
    it('should allow partial container override for testing', async () => {
      const mockAssistantClient = {
        chat: vi.fn().mockResolvedValue(new ReadableStream()),
      };

      const app = await buildApp({
        logger: false,
        container: {
          assistantClient: mockAssistantClient as unknown as typeof app.container.assistantClient,
        },
      });

      await app.ready();

      // Verify the mock was injected
      expect(app.container.assistantClient.chat).toBe(mockAssistantClient.chat);

      await app.close();
    });

    it('should use real container when no override provided', async () => {
      const app = await buildApp({
        logger: false,
      });

      await app.ready();

      // Verify container exists and has expected services
      expect(app.container).toBeDefined();
      expect(app.container.assistantClient).toBeDefined();
      expect(app.container.videoService).toBeDefined();
      expect(app.container.queuePublisher).toBeDefined();
      // Redis plugin wires the dispatchGuardService — must be present so
      // dispatchPipeline doesn't go through the no-op fallback in prod.
      expect(app.container.dispatchGuardService).toBeDefined();
      expect(app.redis).toBeDefined();

      await app.close();
    });
  });

  describe('health check', () => {
    it('should respond to /health endpoint', async () => {
      const app = await buildApp({ logger: false });
      await app.ready();

      const response = await app.inject({
        method: 'GET',
        url: '/health',
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toHaveProperty('status', 'ok');

      await app.close();
    });
  });

  describe('HTTP server timeouts', () => {
    it('should apply requestTimeout (slow-loris) and connectionTimeout to the server', async () => {
      const app = await buildApp({ logger: false });
      await app.ready();

      // Config defaults: HTTP_REQUEST_TIMEOUT_MS=30000, HTTP_CONNECTION_TIMEOUT_MS=60000.
      // requestTimeout covers the request phase only, so SSE responses are
      // unaffected; SSE routes additionally opt out of the inactivity timeout
      // via disableSocketInactivityTimeout().
      expect(app.server.requestTimeout).toBe(30000);
      expect(app.server.timeout).toBe(60000);

      await app.close();
    });
  });

  describe('sentry plugin registration', () => {
    it('should boot cleanly when Sentry has no DSN (no-op mode)', async () => {
      // The plugin's onError hook is wired regardless of DSN; verifying the
      // app boots and serves traffic proves the plugin doesn't blow up when
      // Sentry is disabled.
      const app = await buildApp({ logger: false });
      await app.ready();

      const response = await app.inject({ method: 'GET', url: '/health' });
      expect(response.statusCode).toBe(200);

      await app.close();
    });
  });

  describe('request-id wiring', () => {
    it('should set x-request-id on the health response', async () => {
      const app = await buildApp({ logger: false });
      await app.ready();

      const response = await app.inject({
        method: 'GET',
        url: '/health',
      });

      const id = response.headers['x-request-id'];
      expect(typeof id).toBe('string');
      expect(id).toMatch(/^[0-9a-f-]{36}$/);

      await app.close();
    });

    it('should honor an incoming x-request-id header', async () => {
      const app = await buildApp({ logger: false });
      await app.ready();

      const incoming = 'edge-trace-abcdef12';
      const response = await app.inject({
        method: 'GET',
        url: '/health',
        headers: { 'x-request-id': incoming },
      });

      expect(response.headers['x-request-id']).toBe(incoming);

      await app.close();
    });
  });

  describe('error envelope (docs/ERROR-HANDLING.md, project-score-9 4.5b)', () => {
    // Every error funneled through the global handler must emit the
    // documented `{ error, message, statusCode }` envelope; Zod failures
    // additionally carry `details.issues`.
    it('should emit the documented envelope for AppError subclasses', async () => {
      const app = await buildApp({ logger: false });
      const { NotFoundError } = await import('./utils/errors.js');
      app.get('/boom-app', async () => {
        throw new NotFoundError('Video');
      });
      await app.ready();

      const response = await app.inject({ method: 'GET', url: '/boom-app' });

      expect(response.statusCode).toBe(404);
      expect(response.json()).toEqual({
        error: 'NOT_FOUND',
        message: 'Video not found',
        statusCode: 404,
      });

      await app.close();
    });

    it('should emit envelope + details.issues for Zod validation errors', async () => {
      const app = await buildApp({ logger: false });
      const { z } = await import('zod');
      app.post('/boom-zod', async (req) => {
        return z.object({ url: z.string().url() }).parse(req.body);
      });
      await app.ready();

      const response = await app.inject({
        method: 'POST',
        url: '/boom-zod',
        headers: { 'content-type': 'application/json' },
        payload: { url: 123 },
      });

      expect(response.statusCode).toBe(400);
      const body = response.json();
      expect(body).toMatchObject({
        error: 'VALIDATION_ERROR',
        statusCode: 400,
      });
      expect(typeof body.message).toBe('string');
      expect(Array.isArray(body.details.issues)).toBe(true);
      expect(body.details.issues[0]).toHaveProperty('path');
      expect(body.details.issues[0]).toHaveProperty('message');

      await app.close();
    });

    it('should emit the documented envelope for unexpected 500s', async () => {
      const app = await buildApp({ logger: false });
      app.get('/boom-500', async () => {
        throw new Error('kaboom');
      });
      await app.ready();

      const response = await app.inject({ method: 'GET', url: '/boom-500' });

      expect(response.statusCode).toBe(500);
      expect(response.json()).toEqual({
        error: 'INTERNAL_ERROR',
        message: 'kaboom', // non-production echoes the message
        statusCode: 500,
      });

      await app.close();
    });
  });

  describe('error logging (observability-fix 1.4)', () => {
    // Every 4xx branch used to `return` before any log call — a client-
    // reported 400/401/429 could only be diagnosed by reproducing it.
    it('should log 4xx responses at warn with code, status and requestId', async () => {
      const { lines, stream } = captureLogs();
      const app = await buildApp({ logger: { level: 'warn', stream } });
      const { NotFoundError } = await import('./utils/errors.js');
      app.get('/boom-404', async () => {
        throw new NotFoundError('Video');
      });
      await app.ready();

      const response = await app.inject({
        method: 'GET',
        url: '/boom-404',
        headers: { 'x-request-id': '11111111-1111-4111-8111-111111111111' },
      });

      expect(response.statusCode).toBe(404);
      const rejected = lines.find((l) => l.msg === 'request rejected');
      expect(rejected).toMatchObject({
        level: 40,
        statusCode: 404,
        errorCode: 'NOT_FOUND',
        errMessage: 'Video not found',
        method: 'GET',
        route: '/boom-404',
        requestId: '11111111-1111-4111-8111-111111111111',
      });

      await app.close();
    });

    it('should log unexpected 500s at error, not as a rejection', async () => {
      const { lines, stream } = captureLogs();
      const app = await buildApp({ logger: { level: 'warn', stream } });
      app.get('/boom-500', async () => {
        throw new Error('kaboom');
      });
      await app.ready();

      await app.inject({ method: 'GET', url: '/boom-500' });

      expect(lines.some((l) => l.msg === 'request rejected')).toBe(false);
      expect(lines.some((l) => l.level === 50 && l.msg === 'kaboom')).toBe(true);

      await app.close();
    });

    it('should redact credentials that reach a log line', async () => {
      const { lines, stream } = captureLogs();
      // Real production logger config + a capture stream, so this fails if
      // the redact wiring is ever dropped from buildApp's defaults.
      const app = await buildApp({ logger: { ...defaultLoggerOptions(false), stream } });
      await app.ready();

      // `req` is reserved for Fastify's request serializer, so a leaked header
      // set shows up under a plain `headers` key (or inside an error's config).
      app.log.info(
        {
          headers: {
            authorization: 'Bearer secret-jwt',
            cookie: 'sid=abc',
            'x-internal-secret': 'internal-1',
            'x-admin-actor': 'actor-header-1',
            host: 'x',
          },
          body: { password: 'hunter2', refreshToken: 'rt-1' },
          token: 'top-level-jwt',
        },
        'shape',
      );

      const line = lines.find((l) => l.msg === 'shape') as {
        headers: Record<string, string>;
        body: Record<string, string>;
        token: string;
      };
      expect(line.headers.authorization).toBe('[Redacted]');
      expect(line.headers.cookie).toBe('[Redacted]');
      expect(line.headers['x-internal-secret']).toBe('[Redacted]');
      expect(line.headers['x-admin-actor']).toBe('[Redacted]');
      expect(line.headers.host).toBe('x');
      expect(line.body.password).toBe('[Redacted]');
      expect(line.body.refreshToken).toBe('[Redacted]');
      expect(line.token).toBe('[Redacted]');

      await app.close();
    });
  });
});
