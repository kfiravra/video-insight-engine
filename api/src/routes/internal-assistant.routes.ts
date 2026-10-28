import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { idParamSchema, objectIdSchema } from '../utils/validation.js';
import { config } from '../config.js';
import { VideoSubmissionService } from '../services/video-submission.service.js';
import type { UserTier } from '@vie/types';

// Bound how many owned videos the assistant can enumerate in a single call so a
// power user's library can't balloon the internal response unboundedly.
const OWNED_VIDEOS_LIMIT = 200;

const createFolderSchema = z.object({
  name: z.string().min(1).max(100),
  parentId: objectIdSchema.optional().nullable(),
  color: z.string().optional().nullable(),
  icon: z.string().optional().nullable(),
});

const updateFolderSchema = z.object({
  name: z.string().min(1).max(100).optional(),
  parentId: objectIdSchema.optional().nullable(),
  color: z.string().optional().nullable(),
  icon: z.string().optional().nullable(),
});

const deleteFolderQuerySchema = z.object({
  deleteContent: z.enum(['true', 'false']).optional(),
});

const moveVideoSchema = z.object({
  folderId: objectIdSchema.nullable(),
});

const generateSchema = z.object({
  url: z.string().min(1),
  folderId: objectIdSchema.optional(),
});

const VALID_TIERS = ['free', 'pro', 'team'] as const;

/** Narrow a stored tier string to a known `UserTier`, defaulting to `free`. */
function resolveTier(tier: string | undefined): UserTier {
  return VALID_TIERS.find((t) => t === tier) ?? 'free';
}

/**
 * Internal assistant callback routes. These are thin wrappers over the existing
 * folder/video services so the assistant's tool plans can mutate the caller's
 * library. Every route authenticates with `X-Internal-Secret` + `X-User-Id` and
 * scopes all work to that userId — a body-supplied userId is never trusted.
 */
export async function internalAssistantRoutes(fastify: FastifyInstance): Promise<void> {
  const {
    folderService,
    videoService,
    videoRepository,
    userRepository,
    costMonitorService,
    idempotencyService,
    submitBurstLimiter,
  } = fastify.container;
  // Same submission flow as POST /api/videos — the assistant must not be able
  // to bypass the daily cost cap, idempotency gate, or reservation unwind.
  const videoSubmissionService = new VideoSubmissionService(
    videoService,
    costMonitorService,
    idempotencyService,
    videoRepository,
    fastify.log,
    fastify.container.billingShadowService,
  );

  // GET /internal/assistant/folders
  fastify.get('/folders', {
    preHandler: [fastify.authenticateInternal],
  }, async (req) => {
    const data = await folderService.list(req.user.userId);
    return { success: true, data };
  });

  // POST /internal/assistant/folders
  fastify.post<{
    Body: z.infer<typeof createFolderSchema>;
  }>('/folders', {
    preHandler: [fastify.authenticateInternal],
  }, async (req, reply) => {
    const input = createFolderSchema.parse(req.body);
    const data = await folderService.create({
      userId: req.user.userId,
      ...input,
    });
    return reply.status(201).send({ success: true, data });
  });

  // PATCH /internal/assistant/folders/:id
  fastify.patch<{
    Params: z.infer<typeof idParamSchema>;
    Body: z.infer<typeof updateFolderSchema>;
  }>('/folders/:id', {
    preHandler: [fastify.authenticateInternal, fastify.requireNotDemo],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    const input = updateFolderSchema.parse(req.body);
    const data = await folderService.update(req.user.userId, id, input);
    return { success: true, data };
  });

  // DELETE /internal/assistant/folders/:id?deleteContent=true
  fastify.delete<{
    Params: z.infer<typeof idParamSchema>;
    Querystring: z.infer<typeof deleteFolderQuerySchema>;
  }>('/folders/:id', {
    preHandler: [fastify.authenticateInternal, fastify.requireNotDemo],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    const { deleteContent } = deleteFolderQuerySchema.parse(req.query);
    await folderService.delete(req.user.userId, id, deleteContent === 'true');
    return { success: true, data: { id } };
  });

  // GET /internal/assistant/videos
  fastify.get('/videos', {
    preHandler: [fastify.authenticateInternal],
  }, async (req) => {
    const videos = await videoRepository.getUserVideos(req.user.userId, undefined, {
      limit: OWNED_VIDEOS_LIMIT,
    });
    if (videos.length === OWNED_VIDEOS_LIMIT) {
      req.log.warn(
        { userId: req.user.userId, cap: OWNED_VIDEOS_LIMIT },
        'owned-video enumeration truncated at cap — assistant sees a partial library',
      );
    }
    const data = videos.map((v) => ({
      id: v._id.toString(),
      videoSummaryId: v.videoSummaryId.toString(),
      youtubeId: v.youtubeId,
      title: v.cache?.title ?? v.title ?? null,
      folderId: v.folderId ? v.folderId.toString() : null,
    }));
    return { success: true, data };
  });

  // PATCH /internal/assistant/videos/:id/move
  fastify.patch<{
    Params: z.infer<typeof idParamSchema>;
    Body: z.infer<typeof moveVideoSchema>;
  }>('/videos/:id/move', {
    preHandler: [fastify.authenticateInternal, fastify.requireNotDemo],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    const { folderId } = moveVideoSchema.parse(req.body);
    const data = await videoService.moveToFolder(req.user.userId, id, folderId);
    return { success: true, data };
  });

  // POST /internal/assistant/generate
  // Mirrors POST /api/videos: same per-user daily request limit (the limiter
  // key is user-scoped because authenticateInternal populates req.user before
  // the preHandler-hooked rate limit runs) and the same submission service.
  const videoDailyLimit = config.RATE_LIMITS.VIDEO_DAILY;
  fastify.post<{
    Body: z.infer<typeof generateSchema>;
  }>('/generate', {
    preHandler: [fastify.authenticateInternal],
    config: {
      rateLimit: videoDailyLimit > 0 ? {
        max: videoDailyLimit,
        timeWindow: '24 hours',
        errorResponseBuilder: () => ({
          error: 'RATE_LIMITED',
          message: `Daily video limit reached (${videoDailyLimit} videos per 24 hours). Try again tomorrow.`,
          statusCode: 429,
        }),
      } : false, // Disable rate limiting when limit is 0
    },
  }, async (req, reply) => {
    const { url, folderId } = generateSchema.parse(req.body);
    const userId = req.user.userId;

    // The same hourly bucket as POST /api/videos: the assistant cannot submit
    // around the burst limit (RATE_LIMITED, 429).
    await submitBurstLimiter.consume(userId);

    // Resolve the tier so the submission service enforces the right daily cap.
    const user = await userRepository.findById(userId);
    const tier = resolveTier(user?.tier);

    const submission = await videoSubmissionService.submit({
      userId,
      url,
      tier,
      folderId,
      requestId: req.id,
    });

    if (submission.outcome === 'duplicate') {
      return {
        success: true,
        data: { video: submission.video, cached: true, duplicate: true },
      };
    }
    if (submission.outcome === 'in_flight') {
      return reply.code(409).send({
        error: 'IDEMPOTENCY_IN_FLIGHT',
        message: 'Another request with the same key is currently processing. Retry shortly.',
      });
    }
    return { success: true, data: submission.result };
  });
}
