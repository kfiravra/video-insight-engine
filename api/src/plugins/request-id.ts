import { randomUUID } from 'crypto';
import type { IncomingHttpHeaders, IncomingMessage } from 'http';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import fp from 'fastify-plugin';

export const REQUEST_ID_HEADER = 'x-request-id';
/** The web's chat session id (one per chat surface, D8), forwarded to the assistant. */
export const SESSION_ID_HEADER = 'x-session-id';

// Tightly scoped: only ASCII safe chars, 8–128 long. Loose enough to accept any
// UUID variant or short opaque ids, strict enough to reject log-injection
// attempts (newlines, semicolons, unicode tricks) before the value reaches pino.
const REQUEST_ID_PATTERN = /^[A-Za-z0-9_-]{8,128}$/;

/**
 * Fastify `genReqId` hook. Honors an incoming `x-request-id` header when it
 * matches the safe pattern; otherwise generates a fresh UUID v4. Exported so
 * `buildApp` can wire it into the Fastify constructor (plugins can't change
 * constructor options after Fastify is built).
 */
export function genRequestId(req: IncomingMessage): string {
  const headerValue = req.headers[REQUEST_ID_HEADER];
  if (typeof headerValue === 'string' && REQUEST_ID_PATTERN.test(headerValue)) {
    return headerValue;
  }
  return randomUUID();
}

/**
 * The request's chat session id: the `x-session-id` header when it matches the
 * same safe pattern as request ids, otherwise undefined. A bad value is
 * dropped rather than refused, so a stale client never loses its chat.
 */
export function readSessionId(headers: IncomingHttpHeaders): string | undefined {
  const headerValue = headers[SESSION_ID_HEADER];
  return typeof headerValue === 'string' && REQUEST_ID_PATTERN.test(headerValue)
    ? headerValue
    : undefined;
}

type ChildArgs = Parameters<FastifyBaseLogger['child']>;

/**
 * Fastify child logger factory: every request logger binds `sessionId` next
 * to `requestId` when the request carries a valid session header, so one chat
 * session's gateway lines are greppable by its id (D11).
 */
export function childLoggerWithSession(
  logger: FastifyBaseLogger,
  bindings: ChildArgs[0],
  options: ChildArgs[1],
  rawReq: IncomingMessage,
): FastifyBaseLogger {
  const sessionId = readSessionId(rawReq.headers);
  return logger.child(sessionId ? { ...bindings, sessionId } : bindings, options);
}

async function requestIdSetup(fastify: FastifyInstance): Promise<void> {
  // Registered first (app.ts), so every route's request logger is built by it.
  fastify.setChildLoggerFactory(childLoggerWithSession);
  fastify.addHook('onSend', async (request, reply) => {
    reply.header(REQUEST_ID_HEADER, request.id);
  });
}

export const requestIdPlugin = fp(requestIdSetup, { name: 'request-id-plugin' });
