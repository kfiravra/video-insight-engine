import { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { objectIdSchema } from '../utils/validation.js';
import { config } from '../config.js';

// Provider config schema (same as videos)
const providerSchema = z.enum(['anthropic', 'openai', 'gemini']);
const providerConfigSchema = z.object({
  default: providerSchema,
  fast: providerSchema.optional(),
  fallback: providerSchema.nullable().optional(),
}).optional();

const previewSchema = z.object({
  url: z.string().url(),
  maxVideos: z.number().int().min(1).max(200).optional().default(100),
});

const importSchema = z.object({
  url: z.string().url(),
  folderId: objectIdSchema.optional(),
  maxVideos: z.number().int().min(1).max(200).optional().default(100),
  providers: providerConfigSchema,
});

const playlistIdParamSchema = z.object({
  playlistId: z.string().min(1).max(64).regex(/^[a-zA-Z0-9_-]+$/, 'Invalid playlist ID format'),
});

export async function playlistsRoutes(fastify: FastifyInstance) {
  const { playlistService, submitBurstLimiter } = fastify.container;

  // POST /api/playlists/preview - Preview a playlist before importing
  fastify.post<{
    Body: z.infer<typeof previewSchema>;
  }>('/preview', {
    preHandler: [fastify.authenticate],
    config: {
      rateLimit: {
        max: config.RATE_LIMITS.PLAYLIST_PREVIEW,
        timeWindow: '1 hour',
        errorResponseBuilder: () => ({
          error: 'RATE_LIMITED',
          message: 'Too many playlist preview requests. Try again later.',
          statusCode: 429,
        }),
      },
    },
  }, async (req) => {
    const { url, maxVideos } = previewSchema.parse(req.body);
    const preview = await playlistService.preview(url, maxVideos);
    return { playlist: preview };
  });

  // POST /api/playlists/import - Import a playlist
  fastify.post<{
    Body: z.infer<typeof importSchema>;
  }>('/import', {
    preHandler: [fastify.authenticate],
    config: {
      rateLimit: {
        max: config.RATE_LIMITS.PLAYLIST_IMPORT,
        timeWindow: '24 hours',
        errorResponseBuilder: () => ({
          error: 'RATE_LIMITED',
          message: 'Daily playlist import limit reached. Try again tomorrow.',
          statusCode: 429,
        }),
      },
    },
  }, async (req, reply) => {
    const { url, folderId, maxVideos, providers } = importSchema.parse(req.body);
    // Same rule as POST /api/videos: the shared demo account may import, but
    // not pick providers on the shared budget.
    if (providers) {
      await fastify.requireNotDemo(req);
    }
    // A playlist import is one submission for the shared hourly burst bucket,
    // however many videos it holds.
    await submitBurstLimiter.consume(req.user.userId);
    const result = await playlistService.import(
      req.user.userId,
      url,
      folderId,
      maxVideos,
      providers,
    );
    return reply.code(201).send(result);
  });

  // GET /api/playlists/:playlistId/videos - Get videos in a playlist
  fastify.get<{
    Params: z.infer<typeof playlistIdParamSchema>;
  }>('/:playlistId/videos', {
    preHandler: [fastify.authenticate],
  }, async (req) => {
    const { playlistId } = playlistIdParamSchema.parse(req.params);
    const videos = await playlistService.getPlaylistVideos(req.user.userId, playlistId);
    return { videos };
  });
}
