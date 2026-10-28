import { createHash } from 'node:crypto';
import { Transform, pipeline } from 'node:stream';
import type { FastifyInstance, FastifyReply, FastifyRequest, RequestPayload } from 'fastify';
import fp from 'fastify-plugin';
import { READ_PERMISSIONS } from '@vie/types';
import type { Permission } from '@vie/types';

import { EMPTY_BODY_SHA256 } from '../utils/admin-actor.js';
import { isValidAdminKey, verifyActorHeader } from '../utils/admin-auth.js';
import { AdminPermissionError, UnauthorizedError } from '../utils/errors.js';

/** Who an admin request acts as. `machine` is the shared key with no named admin: reads only. */
export type AdminActor = { kind: 'user'; userId: string } | { kind: 'machine' };

declare module 'fastify' {
  interface FastifyInstance {
    /**
     * preHandler factory for `/api/admin/*` routes. Requires the shared
     * `x-admin-key`, then resolves the actor: a valid signed `x-admin-actor`
     * header makes the request that user's, provided they still hold
     * `permission`; no actor header makes it the machine key's, allowed only
     * for the read permissions. Sets `request.adminActor`.
     */
    requireAdmin: (permission: Permission) => (req: FastifyRequest) => Promise<void>;
  }
  interface FastifyRequest {
    adminActor: AdminActor | null;
    /** sha256 of the body Fastify parsed, recorded only for admin requests that carry an actor header. */
    actorBodySha256: string | null;
  }
}

const ADMIN_PATH_PREFIX = '/api/admin/';
const ACTOR_HEADER = 'x-admin-actor';

/**
 * The actor signature covers the exact request bytes, which Fastify has
 * consumed by the time a preHandler runs. This passes the stream through
 * unchanged while hashing it, and only when the caller already proved the
 * shared key, so unauthenticated traffic is never touched.
 */
async function hashAdminRequestBody(
  request: FastifyRequest,
  _reply: FastifyReply,
  payload: RequestPayload,
): Promise<RequestPayload> {
  const signed = request.headers[ACTOR_HEADER] !== undefined;
  if (!signed || !request.url.startsWith(ADMIN_PATH_PREFIX) || !isValidAdminKey(request.headers['x-admin-key'])) {
    return payload;
  }
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

async function userActor(
  fastify: FastifyInstance,
  req: FastifyRequest,
  header: string | string[],
  permission: Permission,
): Promise<AdminActor> {
  // A request whose body Fastify never parsed (GET, or no content type) carries
  // no body the app can see, so it is signed as empty.
  const verified = verifyActorHeader(header, req.actorBodySha256 ?? EMPTY_BODY_SHA256);
  if (!verified.ok) {
    req.log.warn({ reason: verified.reason }, 'admin_actor_rejected');
    throw new UnauthorizedError('Invalid admin actor');
  }
  // The header proves vie-admin vouched for this user a moment ago; whether
  // they still hold the permission is decided here, from the database.
  if (!(await fastify.container.userRepository.hasPermission(verified.userId, permission))) {
    req.log.warn({ userId: verified.userId, permission }, 'admin_permission_denied');
    throw new AdminPermissionError(`Missing permission: ${permission}`);
  }
  return { kind: 'user', userId: verified.userId };
}

async function adminAuthSetup(fastify: FastifyInstance): Promise<void> {
  fastify.decorateRequest('adminActor', null);
  fastify.decorateRequest('actorBodySha256', null);
  fastify.addHook('preParsing', hashAdminRequestBody);

  fastify.decorate('requireAdmin', (permission: Permission) => async (req: FastifyRequest): Promise<void> => {
    if (!isValidAdminKey(req.headers['x-admin-key'])) {
      throw new UnauthorizedError('Admin key required');
    }
    const header = req.headers[ACTOR_HEADER];
    req.adminActor =
      header === undefined ? machineActor(permission) : await userActor(fastify, req, header, permission);
  });
}

export const adminAuthPlugin = fp(adminAuthSetup, { name: 'admin-auth-plugin' });
