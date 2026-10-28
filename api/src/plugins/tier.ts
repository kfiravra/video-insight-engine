import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import fp from 'fastify-plugin';
import type { PlanId } from '@vie/types';

declare module 'fastify' {
  interface FastifyRequest {
    /** The billing account's plan (`billingAccounts.plan.id`); `free` until resolveTier runs. */
    tier: { name: PlanId };
  }
  interface FastifyInstance {
    resolveTier: (req: FastifyRequest, reply: FastifyReply) => Promise<void>;
  }
}

const DEFAULT_TIER: { name: PlanId } = { name: 'free' };

async function tierSetup(fastify: FastifyInstance) {
  // Decorate request with null (avoids FSTDEP006 for reference types)
  fastify.decorateRequest('tier', null);

  // Set default tier for every request
  fastify.addHook('onRequest', async (req: FastifyRequest) => {
    req.tier = DEFAULT_TIER;
  });

  // Decorator for routes that need the account plan (use after authenticate).
  // The plan only steers queue priority here, never money, so a lookup
  // failure keeps `free` (lower priority) instead of failing the request.
  fastify.decorate('resolveTier', async (req: FastifyRequest, _reply: FastifyReply) => {
    const userId = req.user?.userId;
    if (!userId) return;
    try {
      const { plan } = await fastify.container.billingService.summary(userId);
      req.tier = { name: plan.id };
    } catch (err) {
      req.log.warn({ err, userId }, 'plan_lookup_failed');
    }
  });
}

export const tierPlugin = fp(tierSetup);
