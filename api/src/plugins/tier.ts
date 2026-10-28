import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import fp from 'fastify-plugin';
import type { UserTier, TierLimits } from '@vie/types';
import { TIER_LIMITS } from '@vie/types';

declare module 'fastify' {
  interface FastifyRequest {
    tier: { name: UserTier; limits: TierLimits };
  }
  interface FastifyInstance {
    resolveTier: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

const DEFAULT_TIER: { name: UserTier; limits: TierLimits } = {
  name: 'free',
  limits: TIER_LIMITS.free,
};

async function tierSetup(fastify: FastifyInstance) {
  // Decorate request with null (avoids FSTDEP006 for reference types)
  fastify.decorateRequest('tier', null);

  // Set default tier for every request
  fastify.addHook('onRequest', async (req: FastifyRequest) => {
    req.tier = DEFAULT_TIER;
  });

  // Decorator for routes that need actual tier resolution (use after authenticate)
  // TODO: Add short-lived cache (e.g. 60s) if DB lookups become a bottleneck
  fastify.decorate('resolveTier', async (req: FastifyRequest, _reply: FastifyReply) => {
    if (!req.user?.userId) return;
    const stored = await fastify.container.userRepository.getTier(req.user.userId);
    const name: UserTier = stored === 'pro' || stored === 'team' ? stored : 'free';
    req.tier = { name, limits: TIER_LIMITS[name] };
  });
}

export const tierPlugin = fp(tierSetup);
