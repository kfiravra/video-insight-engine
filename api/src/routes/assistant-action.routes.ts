import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { actionEnumSchema, actionParamsSchema } from '../schemas/assistant.schema.js';
import { VideoNotFoundError } from '../utils/errors.js';
import type { AssistantAction } from '../services/assistant-client.js';
import type { VideoRepository } from '../repositories/video.repository.js';

const actionBodySchema = z.object({
  action: actionEnumSchema,
  params: actionParamsSchema.optional(),
  video_id: z.string().min(1).optional(),
});

/**
 * User-facing action proxy. The JWT-authenticated user dispatches a structured
 * action that the assistant turns into tool plans; the assistant calls back into
 * `/internal/assistant/*` scoped to the same user. `video_id` is optional because
 * library-scoped actions (organize_library, *_folder) have no single video.
 */
const VIDEO_SUMMARY_ID = /^[a-f0-9]{24}$/i;

/**
 * The assistant loads `video_id` as given (a videoSummaryId from the web, or a
 * youtubeId), so the user must hold it in their library — otherwise any
 * version row, eval runs included (D25), could be read as action context.
 */
async function userCanReadVideo(videoRepository: VideoRepository, userId: string, videoId: string): Promise<boolean> {
  return VIDEO_SUMMARY_ID.test(videoId)
    ? videoRepository.userHasAccessToSummary(userId, videoId)
    : videoRepository.userOwnsVideo(userId, videoId);
}

export async function assistantActionRoutes(fastify: FastifyInstance): Promise<void> {
  const { assistantClient, videoRepository } = fastify.container;

  // POST /api/assistant/action
  fastify.post<{
    Body: z.infer<typeof actionBodySchema>;
  }>('/action', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const parsed = actionBodySchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.status(400).send({
        error: 'VALIDATION_ERROR',
        message: parsed.error.errors[0]?.message || 'Invalid action body',
      });
    }

    const videoId = parsed.data.video_id;
    if (videoId && !(await userCanReadVideo(videoRepository, req.user.userId, videoId))) {
      throw new VideoNotFoundError();
    }

    try {
      const { status, body } = await assistantClient.action({
        action: parsed.data.action satisfies AssistantAction,
        params: parsed.data.params,
        userId: req.user.userId,
        videoId,
        requestId: req.id,
      });

      return reply.status(status).send(body);
    } catch (error) {
      fastify.log.error(error, 'assistant action failed');
      return reply.status(502).send({
        error: 'SERVICE_UNAVAILABLE',
        message: 'Assistant service is temporarily unavailable. Please try again.',
      });
    }
  });
}
