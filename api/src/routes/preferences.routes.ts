import { FastifyInstance } from 'fastify';
import { z } from 'zod';

export async function userUsageRoutes(fastify: FastifyInstance) {
  const { costMonitorService } = fastify.container;

  // GET /api/users/me/usage — today's spend + remaining + reset time for the signed-in user
  fastify.get('/', {
    preHandler: [fastify.authenticate, fastify.resolveTier],
  }, async (req) => {
    const summary = await costMonitorService.getUserUsageSummary(
      req.user.userId,
      req.tier.name,
    );
    return summary;
  });
}

const updatePreferencesSchema = z.object({
  theme: z.enum(['system', 'light', 'dark']).optional(),
  displayName: z.string().min(1).max(50).optional(),
  username: z.string()
    .regex(/^[a-zA-Z0-9_]+$/, 'Username must be alphanumeric with underscores')
    .min(3)
    .max(30)
    .optional(),
});

/**
 * Name fields are shown to every visitor of the shared demo account and
 * `username` is a unique claim, so the demo account may not change them.
 * Theme is a harmless convenience and stays allowed.
 */
function editsNameFields(body: z.infer<typeof updatePreferencesSchema>): boolean {
  return body.displayName !== undefined || body.username !== undefined;
}

export async function preferencesRoutes(fastify: FastifyInstance) {
  const { userRepository } = fastify.container;

  // PATCH /api/users/me/preferences — update user preferences
  fastify.patch('/', {
    preHandler: [fastify.authenticate],
    config: {
      rateLimit: {
        max: 30,
        timeWindow: '1 minute',
      },
    },
  }, async (req, reply) => {
    const body = updatePreferencesSchema.parse(req.body);

    if (Object.keys(body).length === 0) {
      return reply.status(400).send({
        error: 'VALIDATION_ERROR',
        message: 'At least one field must be provided',
      });
    }

    if (editsNameFields(body)) await fastify.requireNotDemo(req);

    // Build update object
    const updates: Record<string, unknown> = {};

    if (body.theme !== undefined) {
      updates['preferences.theme'] = body.theme;
    }

    if (body.displayName !== undefined) {
      updates.displayName = body.displayName;
    }

    if (body.username !== undefined) {
      // Check uniqueness (sparse index handles null)
      const existing = await userRepository.findByUsername(body.username);
      if (existing && existing._id.toString() !== req.user.userId) {
        return reply.status(409).send({
          error: 'USERNAME_TAKEN',
          message: 'Username is already taken',
        });
      }
      updates.username = body.username;
    }

    await userRepository.update(req.user.userId, updates);
    const updated = await userRepository.findById(req.user.userId);

    return reply.send({
      id: updated!._id.toString(),
      email: updated!.email,
      name: updated!.name,
      username: updated!.username ?? null,
      displayName: updated!.displayName ?? null,
      theme: updated!.preferences?.theme ?? 'system',
    });
  });
}
