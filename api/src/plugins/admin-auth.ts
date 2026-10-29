import { createHash } from 'node:crypto';
import { Transform, pipeline } from 'node:stream';
import type { FastifyInstance, FastifyReply, FastifyRequest, RequestPayload } from 'fastify';
import fp from 'fastify-plugin';
import { READ_PERMISSIONS } from '@vie/types';
import type { Permission } from '@vie/types';

import { ACTOR_HEADER_MAX_AGE_SECONDS, EMPTY_BODY_SHA256 } from '../utils/admin-actor.js';
import type { ActorHeaderGrant, ActorRequest } from '../utils/admin-actor.js';
import { isValidAdminKey, verifyActorHeader } from '../utils/admin-auth.js';
import { AdminPermissionError, UnauthorizedError } from '../utils/errors.js';

/** Who an admin request acts as. `machine` is the shared key with no named admin: reads only. */
export type AdminActor = { kind: 'user'; userId: string } | { kind: 'machine' };

/** The preHandler `requireAdmin` returns; it records the permission it demands so tests can audit every route. */
export type AdminGuard = ((req: FastifyRequest) => Promise<void>) & { requiredPermission: Permission };

declare module 'fastify' {
  interface FastifyInstance {
    /**
     * preHandler factory for `/api/admin/*` routes. Requires the shared
     * `x-admin-key`, then resolves the actor: a valid signed `x-admin-actor`
     * header makes the request that user's, provided they still hold
     * `permission`; no actor header makes it the machine key's, allowed only
     * for the read permissions. Sets `request.adminActor`.
     */
    requireAdmin: (permission: Permission) => AdminGuard;
  }
  interface FastifyRequest {
    adminActor: AdminActor | null;
    /** sha256 of the body Fastify parsed, recorded only for admin requests that carry an actor header. */
    actorBodySha256: string | null;
  }
}

const ACTOR_HEADER = 'x-admin-actor';

/**
 * The actor signature covers the exact request bytes, which Fastify has
 * consumed by the time a preHandler runs. This passes the stream through
 * unchanged while hashing it, whenever the caller already proved the shared
 * key and sent an actor header, whatever the URL looks like: Fastify routes
 * percent-encoded and absolute-form targets that a prefix test on the raw URL
 * would miss, and a body that went unhashed would verify as empty. Requests
 * without both headers are never touched.
 */
async function hashAdminRequestBody(
  request: FastifyRequest,
  _reply: FastifyReply,
  payload: RequestPayload,
): Promise<RequestPayload> {
  const signed = request.headers[ACTOR_HEADER] !== undefined;
  if (!signed || !isValidAdminKey(request.headers['x-admin-key'])) return payload;
  const hash = createHash('sha256');
  const tap = new Transform({
    transform(chunk, _encoding, done) {
      hash.update(chunk);
      done(null, chunk);
    },
    flush(done) {
      request.actorBodySha256 = hash.digest('hex');
      done();
    },
  });
  pipeline(payload, tap, (err) => {
    if (err) request.log.debug({ err }, 'admin request body stream ended with an error');
  });
  return tap;
}

function machineActor(permission: Permission): AdminActor {
  if (!READ_PERMISSIONS.has(permission)) {
    throw new AdminPermissionError('The shared admin key may only read; a named admin is required');
  }
  return { kind: 'machine' };
}

/** Reads repeat harmlessly; everything else is a mutation whose signature may be used once. */
function isMutation(method: string): boolean {
  return method !== 'GET' && method !== 'HEAD';
}

/**
 * What the signature must cover for this request. A body Fastify parsed must
 * have been hashed on its way in; a missing hash then means the stream was
 * never tapped, and the empty-body default would vouch for bytes nobody
 * checked, so it fails closed. A request with no parsed body (a GET, or a
 * body-less call) is signed over the empty body.
 */
function signedRequest(req: FastifyRequest): ActorRequest | null {
  if (req.actorBodySha256 === null && req.body !== undefined) return null;
  return {
    method: req.method,
    // The target as received: the signature must not survive decoding or normalisation.
    target: req.raw.url ?? req.url,
    bodySha256: req.actorBodySha256 ?? EMPTY_BODY_SHA256,
  };
}

async function userActor(
  fastify: FastifyInstance,
  req: FastifyRequest,
  header: string | string[],
  permission: Permission,
): Promise<AdminActor> {
  const request = signedRequest(req);
  if (!request) {
    req.log.warn({ reason: 'body_not_hashed' }, 'admin_actor_rejected');
    throw new UnauthorizedError('Invalid admin actor');
  }
  const verified = verifyActorHeader(header, request);
  if (!verified.ok) {
    req.log.warn({ reason: verified.reason }, 'admin_actor_rejected');
    throw new UnauthorizedError('Invalid admin actor');
  }
  if (isMutation(req.method)) await claimSignature(fastify, req, verified);
  // The header proves vie-admin vouched for this user a moment ago; whether
  // they still hold the permission is decided here, from the database.
  if (!(await fastify.container.userRepository.hasPermission(verified.userId, permission))) {
    req.log.warn({ userId: verified.userId, permission }, 'admin_permission_denied');
    throw new AdminPermissionError(`Missing permission: ${permission}`);
  }
  return { kind: 'user', userId: verified.userId };
}

/**
 * Single use for mutations. The record lives as long as the header would still
 * verify (five minutes after its timestamp, which may be up to five minutes in
 * the future), so no window remains in which a replay would pass.
 */
async function claimSignature(fastify: FastifyInstance, req: FastifyRequest, grant: ActorHeaderGrant): Promise<void> {
  const nowSeconds = Math.floor(Date.now() / 1000);
  const ttlSeconds = grant.unixTs + ACTOR_HEADER_MAX_AGE_SECONDS - nowSeconds + 1;
  if (await fastify.container.adminReplayGuard.claim(grant.signature, ttlSeconds)) return;
  req.log.warn({ reason: 'replayed', userId: grant.userId }, 'admin_actor_rejected');
  throw new UnauthorizedError('Invalid admin actor');
}

async function adminAuthSetup(fastify: FastifyInstance): Promise<void> {
  fastify.decorateRequest('adminActor', null);
  fastify.decorateRequest('actorBodySha256', null);
  fastify.addHook('preParsing', hashAdminRequestBody);

  fastify.decorate('requireAdmin', (permission: Permission): AdminGuard => {
    const guard = async (req: FastifyRequest): Promise<void> => {
      if (!isValidAdminKey(req.headers['x-admin-key'])) {
        throw new UnauthorizedError('Admin key required');
      }
      const header = req.headers[ACTOR_HEADER];
      req.adminActor =
        header === undefined ? machineActor(permission) : await userActor(fastify, req, header, permission);
    };
    return Object.assign(guard, { requiredPermission: permission });
  });
}

export const adminAuthPlugin = fp(adminAuthSetup, { name: 'admin-auth-plugin' });
