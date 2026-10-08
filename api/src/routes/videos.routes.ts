import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { idParamSchema, objectIdSchema, youtubeIdSchema } from '../utils/validation.js';
import { config } from '../config.js';
import { VideoSubmissionService } from '../services/video-submission.service.js';

// Stripe's spec is "1–255 chars, opaque to us". Reject longer to keep junk out
// of the DB; reject control chars/whitespace because they're never produced by
// real clients and would muddy logs.
const IDEMPOTENCY_KEY_PATTERN = /^[\x21-\x7E]{1,255}$/;

// Provider config schema for dev tools (optional)
const providerSchema = z.enum(['anthropic', 'openai', 'gemini']);
const providerConfigSchema = z.object({
  default: providerSchema,
  fast: providerSchema.optional(),
  fallback: providerSchema.nullable().optional(),
}).optional();

const createVideoSchema = z.object({
  url: z.string().url(),
  folderId: objectIdSchema.optional(),
  bypassCache: z.boolean().optional().default(false),
  // Benchmark-only (admin/eval accounts; anyone else gets 403): skip the
  // summarizer's S3 transcript + scene-frame caches so the run measures cold
  // media. Implies bypassCache.
  cold: z.boolean().optional().default(false),
  providers: providerConfigSchema,
});

const moveVideoSchema = z.object({
  folderId: objectIdSchema.nullable(),
});

const videosQuerySchema = z.object({
  folderId: objectIdSchema.optional(),
  limit: z.coerce.number().int().min(1).max(100).optional().default(50),
  offset: z.coerce.number().int().min(0).optional().default(0),
});

const versionsParamSchema = z.object({
  youtubeId: youtubeIdSchema,
});

const versionsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(50).optional().default(10),
});

export async function videosRoutes(fastify: FastifyInstance) {
  const { videoService, videoCascadeService, costMonitorService, idempotencyService, videoRepository } = fastify.container;
  // Constructed here (not in the container) so route tests that override
  // container services via `buildTestApp` keep full control over its deps.
  const videoSubmissionService = new VideoSubmissionService(
    videoService,
    costMonitorService,
    idempotencyService,
    videoRepository,
    fastify.log,
  );

  // GET /api/videos
  fastify.get<{
    Querystring: z.infer<typeof videosQuerySchema>;
  }>('/', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    const { folderId, limit, offset } = videosQuerySchema.parse(req.query);
    const { videos, total } = await videoService.getVideos(req.user.userId, folderId, { limit, offset });
    // `pagination` is an additive sibling — existing consumers keep reading `videos`.
    return { videos, pagination: { limit, offset, total } };
  });

  // GET /api/videos/:id
  fastify.get<{
    Params: z.infer<typeof idParamSchema>;
  }>('/:id', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    return videoService.getVideo(req.user.userId, id);
  });

  // POST /api/videos
  // Video daily limit is configurable via VIDEO_DAILY_LIMIT env var (0 = unlimited)
  const videoDailyLimit = config.RATE_LIMITS.VIDEO_DAILY;
  fastify.post<{
    Body: z.infer<typeof createVideoSchema>;
  }>('/', {
    preHandler: [fastify.authenticate, fastify.resolveTier],
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
    const input = createVideoSchema.parse(req.body);

    // Validate the optional client-supplied header before doing any work.
    // We bound the size + alphabet so a misbehaving client can't pollute
    // the index. Header parsing/validation is HTTP-boundary work, so it
    // stays here rather than in the submission service.
    const rawHeader = req.headers['idempotency-key'];
    const clientKey = Array.isArray(rawHeader) ? rawHeader[0] : rawHeader;
    if (clientKey && !IDEMPOTENCY_KEY_PATTERN.test(clientKey)) {
      return reply.code(400).send({
        error: 'INVALID_IDEMPOTENCY_KEY',
        message: 'Idempotency-Key must be 1-255 printable ASCII characters',
      });
    }

    // Full submission flow (idempotency gate → cost reservation → createVideo
    // → unwind on failure) lives in the service so the assistant's internal
    // generate route enforces the exact same gates. DailyLimitReachedError and
    // createVideo errors propagate to the app-level error handler.
    const submission = await videoSubmissionService.submit({
      userId: req.user.userId,
      url: input.url,
      tier: req.tier.name,
      folderId: input.folderId,
      bypassCache: input.bypassCache,
      cold: input.cold,
      providers: input.providers,
      clientKey,
      requestId: req.id,
    });

    if (submission.outcome === 'duplicate') {
      return reply.code(200).send({ video: submission.video, cached: true, duplicate: true });
    }
    if (submission.outcome === 'in_flight') {
      return reply.code(409).send({
        error: 'IDEMPOTENCY_IN_FLIGHT',
        message: 'Another request with the same key is currently processing. Retry shortly.',
      });
    }
    return reply.code(201).send(submission.result);
  });

  // DELETE /api/videos/:id
  fastify.delete<{
    Params: z.infer<typeof idParamSchema>;
  }>('/:id', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { id } = idParamSchema.parse(req.params);
    // User scope: only this user's row, idempotency key and notes go; the
    // shared summary stays for everyone else.
    await videoCascadeService.deleteVideo({ scope: 'user', userId: req.user.userId, userVideoId: id });
    return reply.code(204).send();
  });

  // PATCH /api/videos/:id/move - Move video to a folder
  fastify.patch<{
    Params: z.infer<typeof idParamSchema>;
    Body: z.infer<typeof moveVideoSchema>;
  }>('/:id/move', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    const { id } = idParamSchema.parse(req.params);
    const { folderId } = moveVideoSchema.parse(req.body);
    return videoService.moveToFolder(req.user.userId, id, folderId);
  });

  // GET /api/videos/versions/:youtubeId - Get all versions of a video for A/B comparison
  // Requires user to own the video (have it in their library)
  fastify.get<{
    Params: z.infer<typeof versionsParamSchema>;
    Querystring: z.infer<typeof versionsQuerySchema>;
  }>('/versions/:youtubeId', {
    preHandler: [fastify.authenticate],
  }, async (req, reply) => {
    const { youtubeId } = versionsParamSchema.parse(req.params);
    const { limit } = versionsQuerySchema.parse(req.query);

    // Authorization: verify user owns a video with this youtubeId
    const userOwnsVideo = await videoService.userOwnsVideo(req.user.userId, youtubeId);
    if (!userOwnsVideo) {
      return reply.code(404).send({
        error: 'VIDEO_NOT_FOUND',
        message: 'Video not found',
      });
    }

    const versions = await videoService.getVersions(req.user.userId, youtubeId, { limit });
    return { versions };
  });
}
