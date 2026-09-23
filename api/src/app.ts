import Fastify from 'fastify';
import type { FastifyBaseLogger, FastifyInstance, FastifyServerOptions } from 'fastify';
import { config } from './config.js';
import { resolveErrorEnvelope } from './utils/error-envelope.js';
import { createContainer, Container } from './container.js';

// Plugins
import { helmetPlugin } from './plugins/helmet.js';
import { mongodbPlugin } from './plugins/mongodb.js';
import { jwtPlugin } from './plugins/jwt.js';
import { corsPlugin } from './plugins/cors.js';
import { rateLimitPlugin } from './plugins/rate-limit.js';
import { websocketPlugin } from './plugins/websocket.js';
import { tierPlugin } from './plugins/tier.js';
import { rabbitmqPlugin } from './plugins/rabbitmq.js';
import { redisPlugin } from './plugins/redis.js';
import { genRequestId, requestIdPlugin } from './plugins/request-id.js';
import { sentryFastifyPlugin } from './plugins/sentry.js';

// Routes
import { authRoutes } from './routes/auth.routes.js';
import { foldersRoutes } from './routes/folders.routes.js';
import { videosRoutes } from './routes/videos.routes.js';
import { playlistsRoutes } from './routes/playlists.routes.js';
import { assistantRoutes } from './routes/assistant.routes.js';
import { assistantLibraryRoutes } from './routes/assistant-library.routes.js';
import { assistantActionRoutes } from './routes/assistant-action.routes.js';
import { internalRoutes } from './routes/internal.routes.js';
import { internalAssistantRoutes } from './routes/internal-assistant.routes.js';
import { streamRoutes } from './routes/stream.routes.js';
import { shareRoutes } from './routes/share.routes.js';
import { ssrRoutes } from './routes/ssr.routes.js';
import { overrideRoutes } from './routes/override.routes.js';
import { paymentRoutes } from './routes/payment.routes.js';
import { preferencesRoutes, userUsageRoutes } from './routes/preferences.routes.js';
import { adminQueueRoutes } from './routes/admin/queue.routes.js';
import { userMeRoutes, adminUsersRoutes } from './routes/users.routes.js';
import { adminVideosRoutes } from './routes/admin/videos.routes.js';
import { healthRoutes } from './routes/health.routes.js';

/** The object form of Fastify's `logger` option (pino options + Fastify extras). */
type LoggerConfig = Exclude<FastifyServerOptions['logger'], boolean | undefined | FastifyBaseLogger>;

// Header names that may carry credentials — kept in step with the Sentry
// plugin's SENSITIVE_HEADERS so stdout and Sentry scrub the same set.
const SECRET_HEADER_NAMES = [
  'authorization',
  'cookie',
  'set-cookie',
  'x-internal-secret',
  'x-admin-key',
  'x-csrf-token',
  'x-api-key',
  'proxy-authorization',
];

// Body/field names that may carry credentials wherever they appear.
const SECRET_FIELD_NAMES = ['password', 'token', 'accessToken', 'refreshToken', 'apiKey'];

/**
 * pino redact paths. `req.headers` is Fastify's request serializer; bare
 * `headers` covers a leaked header set logged by hand. pino's `*` matches
 * exactly one level, so each field is listed both top-level and nested.
 * Exported for the log-shape test.
 */
export const LOG_REDACT_PATHS = [
  ...SECRET_HEADER_NAMES.flatMap((name) => [`req.headers["${name}"]`, `headers["${name}"]`]),
  ...SECRET_FIELD_NAMES.flatMap((name) => [name, `*.${name}`]),
];

/**
 * Default logger config for `buildApp`. Exported so tests can wire a capture
 * stream on top of the real production config instead of re-declaring it.
 */
export function defaultLoggerOptions(isDev: boolean): LoggerConfig {
  return {
    level: isDev ? 'debug' : 'info',
    base: { service: 'vie-api' },
    // Sentry output is scrubbed by the plugin; stdout was not. Credentials
    // that reach a log line (a logged request, an error carrying its
    // request config, a payload echoed in a warn) get censored here.
    redact: { paths: LOG_REDACT_PATHS, censor: '[Redacted]' },
    ...(isDev && {
      transport: {
        target: 'pino-pretty',
        options: {
          colorize: true,
          singleLine: true,
          translateTime: 'SYS:HH:MM:ss',
          ignore: 'pid,hostname',
        },
      },
    }),
  };
}

export interface BuildAppOptions {
  logger?: FastifyServerOptions['logger'];
  /** Optional partial container override for testing */
  container?: Partial<Container>;
}

export async function buildApp(options?: BuildAppOptions): Promise<FastifyInstance> {
  const isDev = config.NODE_ENV === 'development';

  const fastify = Fastify({
    // Plugin reads/validates x-request-id manually via genReqId so we keep the
    // value validation in one place. requestIdHeader is false to opt out of
    // Fastify's built-in header parsing (which doesn't validate the value).
    requestIdHeader: false,
    requestIdLogLabel: 'requestId',
    genReqId: genRequestId,
    // When enabled (TRUST_PROXY env), `req.ip` is derived from X-Forwarded-For
    // instead of the direct TCP connection. Necessary in any deployment behind
    // a CDN, LB, or ingress — otherwise share-view dedup and IP-keyed rate
    // limits all collapse to the proxy's single IP. Default is `false` so
    // local-dev (no proxy) keeps the safe direct-connection behaviour.
    trustProxy: config.TRUST_PROXY_VALUE,
    // Slow-loris protection (request phase only — SSE responses unaffected)
    // and a socket inactivity ceiling. SSE routes opt out of the latter via
    // disableSocketInactivityTimeout(); see config.ts for the full rationale.
    requestTimeout: config.HTTP_REQUEST_TIMEOUT_MS,
    connectionTimeout: config.HTTP_CONNECTION_TIMEOUT_MS,
    logger: options?.logger ?? defaultLoggerOptions(isDev),
    disableRequestLogging: isDev,
  });

  // Dev: single-line request logging (replaces Fastify's verbose two-line default)
  // Skips health check endpoints to reduce noise from Docker/admin polling
  if (isDev) {
    fastify.addHook('onResponse', (req, reply, done) => {
      if (req.url === '/health' || req.url === '/ready') {
        done();
        return;
      }
      const ms = reply.elapsedTime.toFixed(0);
      // Carry the request id so dev logs are greppable by x-request-id too
      // (prod uses Fastify's structured request logging, which binds it).
      req.log.info(`${req.method} ${req.url} ${reply.statusCode} (${ms}ms)`);
      done();
    });
  }

  // Register plugins
  // request-id must be first so every other plugin's logs include it.
  await fastify.register(requestIdPlugin);
  // Sentry plugin registers an onError hook — must come after request-id so
  // captured events carry the requestId tag, but before any route registration
  // so route handler errors are funneled through it.
  await fastify.register(sentryFastifyPlugin);
  await fastify.register(helmetPlugin);
  await fastify.register(corsPlugin);
  await fastify.register(rateLimitPlugin);
  await fastify.register(mongodbPlugin);
  await fastify.register(jwtPlugin);
  await fastify.register(websocketPlugin);
  // Redis is unconditionally registered — the dispatchGuardService wraps every
  // dispatchPipeline call and fails open if Redis is unreachable, so the plugin
  // is harmless even in environments without a working Redis.
  await fastify.register(redisPlugin);

  // RabbitMQ is only required when USE_QUEUE_PIPELINE is on. Registering
  // conditionally means dev environments without RabbitMQ still boot cleanly
  // and route through the legacy HTTP path.
  if (config.USE_QUEUE_PIPELINE) {
    await fastify.register(rabbitmqPlugin);
  }

  // Create container and decorate (allow partial override for testing)
  const channelSupplier = config.USE_QUEUE_PIPELINE
    ? () => fastify.rabbitmq.getChannel()
    : undefined;
  const container = createContainer(fastify.mongo.db, fastify.log, {
    queueChannelSupplier: channelSupplier,
    redisClient: fastify.redis,
  });
  if (options?.container) {
    Object.assign(container, options.container);
  }
  fastify.decorate('container', container);

  // Tier middleware (after JWT, uses container)
  await fastify.register(tierPlugin);

  // Global error handler. The status/envelope mapping lives in
  // utils/error-envelope.ts so this stays a thin log-then-send: 5xx at
  // error with the stack, everything else at warn. 4xx were previously
  // never logged at all — a client-reported 400/401/429 could only be
  // diagnosed by reproducing it.
  fastify.setErrorHandler((error, request, reply) => {
    const { statusCode, body } = resolveErrorEnvelope(error);
    if (statusCode >= 500) {
      request.log.error(error);
    } else {
      request.log.warn(
        {
          statusCode,
          errorCode: body.error,
          // body.message is the client-facing text (first Zod issue for
          // VALIDATION_ERROR) — error.message for a ZodError is a JSON dump.
          errMessage: body.message,
          method: request.method,
          route: request.routeOptions?.url,
          userId: request.user?.userId,
        },
        'request rejected',
      );
    }
    return reply.status(statusCode).send(body);
  });

  // Register routes
  await fastify.register(authRoutes, { prefix: '/api/auth' });
  await fastify.register(foldersRoutes, { prefix: '/api/folders' });
  await fastify.register(videosRoutes, { prefix: '/api/videos' });
  await fastify.register(streamRoutes, { prefix: '/api/videos' });  // Streaming SSE route
  await fastify.register(overrideRoutes, { prefix: '/api/videos' }); // Override category
  await fastify.register(playlistsRoutes, { prefix: '/api/playlists' });
  await fastify.register(assistantRoutes, { prefix: '/api/videos' });
  await fastify.register(assistantLibraryRoutes, { prefix: '/api/assistant' });
  await fastify.register(assistantActionRoutes, { prefix: '/api/assistant' });
  await fastify.register(shareRoutes, { prefix: '/api/share' });
  await fastify.register(paymentRoutes, { prefix: '/api/payments' });
  await fastify.register(preferencesRoutes, { prefix: '/api/users/me/preferences' });
  await fastify.register(userUsageRoutes, { prefix: '/api/users/me/usage' });
  await fastify.register(userMeRoutes, { prefix: '/api/users/me' });
  await fastify.register(internalRoutes, { prefix: '/internal' });
  await fastify.register(internalAssistantRoutes, { prefix: '/internal/assistant' });
  await fastify.register(adminQueueRoutes, { prefix: '/api/admin/queue' });
  await fastify.register(adminUsersRoutes, { prefix: '/api/admin/users' });
  await fastify.register(adminVideosRoutes, { prefix: '/api/admin/videos' });

  // SSR routes (top-level, no /api prefix — for social media crawlers)
  await fastify.register(ssrRoutes);

  // Liveness (/health) + readiness (/ready) probes
  await fastify.register(healthRoutes);

  return fastify;
}
