import { FastifyInstance } from 'fastify';

export async function userUsageRoutes(fastify: FastifyInstance) {
  const { billingService } = fastify.container;

  // GET /api/users/me/usage — the signed-in user's minutes: plan, free video,
  // credits, open holds. Never a dollar amount (the legacy `?view=usd`
  // summary went with the dollar gate in P3.2; unknown query keys are ignored).
  fastify.get('/', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    return billingService.summary(req.user.userId);
  });
}
