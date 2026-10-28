import * as Sentry from '@sentry/node';
import type { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import fp from 'fastify-plugin';
import { ZodError } from 'zod';
import { AppError } from '../utils/errors.js';

/**
 * Shape of the subset of the Sentry event envelope we inspect in `beforeSend`.
 *
 * Keeping a structural type here (instead of importing `@sentry/types`) means
 * the PII filter can be unit-tested without booting the SDK. The real Sentry
 * `Event` type satisfies this shape.
 */
export interface SentryEnvelope {
  request?: {
    headers?: Record<string, string | undefined>;
    data?: unknown;
    url?: string;
    query_string?: string;
  };
  user?: {
    id?: string;
    email?: string;
    ip_address?: string;
    username?: string;
  } | null;
  extra?: Record<string, unknown>;
  contexts?: Record<string, unknown>;
  breadcrumbs?: unknown;
  exception?: {
    values?: Array<{ value?: string; [key: string]: unknown }>;
  };
}

export interface InitSentryOptions {
  dsn: string;
  environment: string;
  release?: string;
  tracesSampleRate?: number;
}

// Headers that may carry tokens, cookies, or internal secrets — always strip
// before transmitting to a third-party service.
const SENSITIVE_HEADERS = new Set([
  'authorization',
  'cookie',
  'set-cookie',
  'x-internal-secret',
  'x-admin-key',
  'x-admin-actor',
  'x-csrf-token',
  'x-api-key',
  'proxy-authorization',
]);

const EMAIL_PATTERN = /[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi;

// Query-string parameter names that may carry credentials. The WebSocket auth
// endpoint accepts `?token=<JWT>`, so any captured event from that route
// would otherwise mirror a live JWT to Sentry SaaS.
const SENSITIVE_QUERY_KEYS = [
  'token',
  'access_token',
  'refresh_token',
  'id_token',
  'code',
  'state',
  'api_key',
  'apikey',
  'key',
  'password',
  'secret',
];

const QUERY_REDACT_PATTERN = new RegExp(
  // Value runs until `&`, `#`, whitespace, or quote. Without the whitespace
  // guard the regex would keep eating past the URL into surrounding prose in
  // error messages like `auth failed at /ws?token=X for alice@...`.
  `([?&])(${SENSITIVE_QUERY_KEYS.join('|')})=[^&#\\s"']*`,
  'gi',
);

function redactEmails(value: string): string {
  return value.replace(EMAIL_PATTERN, '[email]');
}

function redactUrl(url: string): string {
  return url.replace(QUERY_REDACT_PATTERN, (_match, sep: string, key: string) => `${sep}${key}=[Filtered]`);
}

function scrubObject(value: unknown): unknown {
  if (typeof value === 'string') {
    return redactEmails(value);
  }
  if (Array.isArray(value)) {
    return value.map(scrubObject);
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = scrubObject(v);
    }
    return out;
  }
  return value;
}

function scrubUserIdentifier(value: string | undefined): string | undefined {
  // user.id / user.username bypass scrubObject. Drop if the value embeds an
  // email — some Mongo collections key by an address-like identifier.
  if (typeof value === 'string' && EMAIL_PATTERN.test(value)) {
    EMAIL_PATTERN.lastIndex = 0; // stateful regex: reset after `.test`
    return undefined;
  }
  EMAIL_PATTERN.lastIndex = 0;
  return value;
}

/**
 * `beforeSend` hook. Strips PII so support has actionable stack traces without
 * leaking credentials, emails, or internal-service secrets.
 *
 * Exported so it can be unit-tested independently of the Sentry SDK.
 */
export function scrubBeforeSend(event: SentryEnvelope | null): SentryEnvelope | null {
  if (event == null) return null;

  if (event.request?.headers) {
    for (const key of Object.keys(event.request.headers)) {
      if (SENSITIVE_HEADERS.has(key.toLowerCase())) {
        event.request.headers[key] = '[Filtered]';
      }
    }
  }

  if (event.request?.data !== undefined) {
    event.request.data = scrubObject(event.request.data);
  }

  if (typeof event.request?.url === 'string') {
    event.request.url = redactUrl(event.request.url);
  }
  if (typeof event.request?.query_string === 'string') {
    event.request.query_string = redactUrl(`?${event.request.query_string}`).replace(/^\?/, '');
  }

  if (event.user) {
    // Keep the id (we need it to triage) but drop email — id is enough to look
    // the user up internally, and emails in Sentry are a GDPR liability.
    if ('email' in event.user) {
      event.user.email = undefined;
    }
    // `user.id` and `user.username` bypass scrubObject. Strip them if they
    // embed an email — defensive for collections that key by address.
    if ('id' in event.user) {
      event.user.id = scrubUserIdentifier(event.user.id);
    }
    if ('username' in event.user) {
      event.user.username = scrubUserIdentifier(event.user.username);
    }
  }

  // Recurse the scrubber over the rest of the envelope so emails or tokens
  // that landed in extra/contexts/breadcrumbs/exception payloads via a logger
  // call don't leak through.
  if (event.extra !== undefined) {
    event.extra = scrubObject(event.extra) as Record<string, unknown>;
  }
  if (event.contexts !== undefined) {
    event.contexts = scrubObject(event.contexts) as Record<string, unknown>;
  }
  if (event.breadcrumbs !== undefined) {
    event.breadcrumbs = scrubObject(event.breadcrumbs);
  }
  if (event.exception?.values) {
    for (const entry of event.exception.values) {
      if (typeof entry.value === 'string') {
        // URL-redact FIRST: the token-redaction regex consumes until `&` or
        // `#`, so a preceding `[email]` substring would be swallowed by the
        // URL match.
        entry.value = redactEmails(redactUrl(entry.value));
      }
    }
  }

  return event;
}

/**
 * Boot the Sentry SDK. No-ops when the DSN is empty so tests, CI, and dev
 * environments that haven't provisioned a project don't accidentally call
 * the network. Returns true when Sentry was actually initialized.
 */
export function initSentry(options: InitSentryOptions): boolean {
  if (!options.dsn) {
    return false;
  }

  Sentry.init({
    dsn: options.dsn,
    environment: options.environment,
    release: options.release,
    tracesSampleRate: options.tracesSampleRate ?? 0,
    beforeSend: (event) => scrubBeforeSend(event as SentryEnvelope) as never,
    initialScope: {
      tags: { service: 'vie-api' },
    },
  });
  return true;
}

/**
 * Resolve the effective HTTP status of a thrown error across the three shapes
 * we see in this codebase. Returns 500 when the error has no associable status
 * so unknown throws are treated as server errors and captured.
 */
export function effectiveStatusCode(error: unknown): number {
  if (error instanceof AppError) return error.status;
  if (error instanceof ZodError) return 400;
  const sc = (error as { statusCode?: unknown } | null)?.statusCode;
  if (typeof sc === 'number') return sc;
  return 500;
}

async function sentryFastifySetup(fastify: FastifyInstance): Promise<void> {
  fastify.addHook('onError', (request: FastifyRequest, _reply: FastifyReply, error: Error, done) => {
    // Only capture genuinely-unhandled errors. Three shapes carry the status:
    //   - Fastify-native errors (rate limit, schema validation) → `statusCode`
    //   - Project AppError subclasses (NotFound, Validation, etc.)  → `status`
    //   - ZodError thrown by `.parse()` at route boundaries           → 400
    // Without all three checks, expected client outcomes spam Sentry.
    if (effectiveStatusCode(error) < 500) {
      done();
      return;
    }

    Sentry.withScope((scope) => {
      scope.setTag('requestId', request.id);
      scope.setTag('method', request.method);
      // Prefer the route template (`/api/videos/:id`); fall back to the raw
      // URL with query string scrubbed of credentials.
      scope.setTag('route', request.routeOptions?.url ?? redactUrl(request.url));
      const userId = (request as FastifyRequest & { user?: { userId?: string } }).user?.userId;
      if (userId) {
        scope.setUser({ id: userId });
      }
      Sentry.captureException(error);
    });
    done();
  });

  fastify.addHook('onClose', async () => {
    // Drain the SDK buffer so in-flight events aren't lost on container stop.
    await Sentry.flush(2000).catch(() => undefined);
  });
}

export const sentryFastifyPlugin = fp(sentryFastifySetup, { name: 'sentry-plugin' });
