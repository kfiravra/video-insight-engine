import { FastifyInstance, FastifyRequest } from 'fastify';
import fp from 'fastify-plugin';
import { config } from '../config.js';
import type { UserRepository } from '../repositories/user.repository.js';
import { DemoRestrictedError, UnauthorizedError } from '../utils/errors.js';

declare module 'fastify' {
  interface FastifyInstance {
    /**
     * preHandler (run after `authenticate` / `authenticateInternal`) that
     * refuses the shared demo account with 403 `DEMO_RESTRICTED`. Also callable
     * from inside a handler when only some request bodies must be refused.
     */
    requireNotDemo: (req: FastifyRequest) => Promise<void>;
  }
}

// A demo account that exists is re-resolved only occasionally, so an admin
// hard-delete + recreate (new _id) cannot leave the new account unguarded for
// the life of the process. A missing one is retried quickly so an account
// created after boot is picked up without a restart.
const FOUND_TTL_MS = 10 * 60_000;
const MISSING_TTL_MS = 60_000;

interface CachedDemoUser {
  email: string;
  /** Hex `_id`, or null when no account carries the demo email. */
  userId: string | null;
  expiresAt: number;
}

/**
 * Identifies the demo account by user id rather than by the JWT `email` claim:
 * the claim is optional, and the assistant's `/internal/assistant/*` callbacks
 * carry only a userId.
 */
export class DemoUserResolver {
  private cached: CachedDemoUser | null = null;

  constructor(
    private readonly users: Pick<UserRepository, 'findByEmail'>,
    private readonly demoEmail: () => string,
    private readonly now: () => number = Date.now,
  ) {}

  async isDemoUser(userId: string): Promise<boolean> {
    const demoUserId = await this.resolveDemoUserId();
    // ObjectId hex is case-insensitive to the repositories, so compare the
    // same way or a re-cased id from an internal caller would slip past.
    return demoUserId !== null && demoUserId === userId.toLowerCase();
  }

  private async resolveDemoUserId(): Promise<string | null> {
    const email = this.demoEmail();
    if (!email) return null;

    const cached = this.cached;
    if (cached && cached.email === email && cached.expiresAt > this.now()) {
      return cached.userId;
    }

    // A lookup error propagates (and is not cached): the guarded mutations
    // need the same database, so failing the request is safer than letting the
    // demo account through unchecked.
    const user = await this.users.findByEmail(email);
    const userId = user ? user._id.toString().toLowerCase() : null;
    this.cached = {
      email,
      userId,
      expiresAt: this.now() + (userId ? FOUND_TTL_MS : MISSING_TTL_MS),
    };
    return userId;
  }
}

async function demoGuardSetup(fastify: FastifyInstance) {
  const resolver = new DemoUserResolver(
    fastify.container.userRepository,
    () => config.DEMO_USER_EMAIL,
  );

  fastify.decorate('requireNotDemo', async (req: FastifyRequest) => {
    // Fail closed if a route lists this guard without authenticating first.
    const userId = req.user?.userId;
    if (!userId) throw new UnauthorizedError();
    if (await resolver.isDemoUser(userId)) throw new DemoRestrictedError();
  });
}

export const demoGuardPlugin = fp(demoGuardSetup);
