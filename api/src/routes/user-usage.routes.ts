import { FastifyInstance } from 'fastify';
import { z } from 'zod';

// `usd` is the dollar summary the current web build still reads; P3.2 deletes
// it with the cost gate, after P3.7 moves the UI to minutes.
const usageQuerySchema = z.object({
  view: z.enum(['minutes', 'usd']).default('minutes'),
});

export async function userUsageRoutes(fastify: FastifyInstance) {
  const { billingService, costMonitorService } = fastify.container;

  // GET /api/users/me/usage — the signed-in user's minutes: plan, free video,
  // credits, open holds. Never a dollar amount. `?view=usd` is the legacy
  // dollar summary, kept only until the web app stops reading it.
  fastify.get('/', {
    preHandler: [fastify.authenticate, fastify.resolveTier],
  }, async (req) => {
    const { view } = usageQuerySchema.parse(req.query);
    if (view === 'usd') {
      return costMonitorService.getUserUsageSummary(req.user.userId, req.tier.name);
    }
    return billingService.summary(req.user.userId);
  });
}
