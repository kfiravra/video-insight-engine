import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { VIDEO_CATEGORY_VALUES } from '@vie/types';
import { objectIdSchema } from '../utils/validation.js';

const overrideParamsSchema = z.object({
  id: objectIdSchema,
});

const overrideBodySchema = z.object({
  category: z.enum(VIDEO_CATEGORY_VALUES as unknown as [string, ...string[]]),
});

export async function overrideRoutes(fastify: FastifyInstance) {
  const { videoService } = fastify.container;

  // PATCH /api/videos/:id/override-category — change detected category
  fastify.patch<{
    Params: { id: string };
    Body: { category: string };
  }>('/:id/override-category', {
    preHandler: [fastify.authenticate, fastify.requireNotDemo],
  }, async (req) => {
    const { id } = overrideParamsSchema.parse(req.params);
    const { category } = overrideBodySchema.parse(req.body);
    return videoService.overrideCategory(req.user.userId, id, category);
  });
}
