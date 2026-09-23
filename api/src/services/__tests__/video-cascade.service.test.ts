import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ObjectId } from 'mongodb';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { VideoCascadeService } from '../video-cascade.service.js';
import { SummarizerPurgeError } from '../../utils/errors.js';

const logger = {
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
} as unknown as FastifyBaseLogger;

const YT = 'dQw4w9WgXcQ';

/** Fake `Db` whose collections record deleteMany calls; one can be made to fail. */
function fakeDb(failing: string | null = null) {
  const collections = new Map<string, { deleteMany: ReturnType<typeof vi.fn> }>();
  const db = {
    collection: vi.fn((name: string) => {
      if (!collections.has(name)) {
        collections.set(name, {
          deleteMany: vi.fn(async () => {
            if (name === failing) throw new Error('boom');
            return { deletedCount: 1 };
          }),
          find: vi.fn(() => ({ project: vi.fn(() => ({ toArray: vi.fn(async () => []) })) })),
        });
      }
      return collections.get(name);
    }),
  };
  return { db: db as unknown as Db, collections };
}

function build(failing: string | null = null) {
  const { db, collections } = fakeDb(failing);
  const summaryId = new ObjectId();
  const videoRepository = {
    findCacheKeysByYoutubeId: vi.fn().mockResolvedValue([{ _id: summaryId, shareSlug: 'slug-1' }]),
    findUserVideo: vi.fn(),
    findUserVideoByYoutubeId: vi.fn().mockResolvedValue(null),
    deleteUserVideo: vi.fn().mockResolvedValue(true),
  };
  const idempotencyService = {
    invalidateByYoutubeId: vi.fn().mockResolvedValue(2),
    invalidateByUserVideoId: vi.fn().mockResolvedValue(1),
  };
  const dispatchGuard = { acquire: vi.fn(), release: vi.fn().mockResolvedValue(undefined) };
  const summarizerClient = {
    purgeVideo: vi.fn().mockResolvedValue({ qdrantPoints: 4, s3Objects: 6, redisKeys: 1, warnings: [] }),
  };
  const videoDeletionRepository = { insert: vi.fn().mockResolvedValue({}) };
  const service = new VideoCascadeService(
    db,
    videoRepository as never,
    idempotencyService as never,
    dispatchGuard as never,
    summarizerClient as never,
    videoDeletionRepository as never,
    logger,
  );
  return { service, collections, summaryId, videoRepository, idempotencyService, dispatchGuard, summarizerClient, videoDeletionRepository };
}

const actor = { initiatedBy: 'admin' as const, adminId: 'kfir', reason: 'test' };

describe('VideoCascadeService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('global scope', () => {
    it('should abort before touching Mongo when the summarizer purge fails', async () => {
      const ctx = build();
      ctx.summarizerClient.purgeVideo.mockRejectedValue(new SummarizerPurgeError('down'));

      await expect(
        ctx.service.deleteVideo({ scope: 'global', youtubeId: YT, actor }),
      ).rejects.toBeInstanceOf(SummarizerPurgeError);

      expect(ctx.collections.size).toBe(0);
      expect(ctx.videoDeletionRepository.insert).not.toHaveBeenCalled();
    });

    it('should delete every reference, release the guard and write an audit row', async () => {
      const ctx = build();

      const result = await ctx.service.deleteVideo({ scope: 'global', youtubeId: YT, actor });

      expect(ctx.summarizerClient.purgeVideo).toHaveBeenCalledWith({
        youtubeId: YT,
        videoSummaryIds: [ctx.summaryId.toString()],
      });
      expect([...ctx.collections.keys()]).toEqual([
        'userVideos', 'shareLikes', 'shareViews', 'agentNotes', 'videoSummaryCache',
      ]);
      expect(ctx.dispatchGuard.release).toHaveBeenCalledWith(ctx.summaryId.toString(), null);
      expect(result.counts).toMatchObject({
        videoSummaryCache: 1, userVideos: 1, shareLikes: 1, shareViews: 1, agentNotes: 1,
        idempotencyKeys: 2, dispatchGuards: 1, qdrantPoints: 4, s3Objects: 6, redisKeys: 1,
      });
      expect(result.warnings).toEqual([]);
      expect(ctx.videoDeletionRepository.insert).toHaveBeenCalledWith(
        expect.objectContaining({ youtubeId: YT, initiatedBy: 'admin', adminId: 'kfir', reason: 'test' }),
      );
    });

    it('should record a warning and keep going when one Mongo step fails', async () => {
      const ctx = build('shareLikes');
      ctx.summarizerClient.purgeVideo.mockResolvedValue({
        qdrantPoints: 0, s3Objects: 0, redisKeys: 0, warnings: ['s3: partial'],
      });

      const result = await ctx.service.deleteVideo({ scope: 'global', youtubeId: YT, actor });

      expect(result.warnings).toEqual(['summarizer: s3: partial', 'shareLikes: boom']);
      expect(result.counts.videoSummaryCache).toBe(1);
      expect(logger.error).toHaveBeenCalledWith(
        expect.objectContaining({ youtubeId: YT }),
        'video_global_delete_completed_with_warnings',
      );
    });
  });

  describe('user scope', () => {
    it('should remove only the row, its idempotency key and the user notes', async () => {
      const ctx = build();
      const userId = new ObjectId().toString();
      const rowId = new ObjectId().toString();
      ctx.videoRepository.findUserVideo.mockResolvedValue({
        _id: new ObjectId(rowId), userId: new ObjectId(userId), videoSummaryId: ctx.summaryId, youtubeId: YT,
      });

      const result = await ctx.service.deleteVideo({ scope: 'user', userId, userVideoId: rowId });

      expect(ctx.summarizerClient.purgeVideo).not.toHaveBeenCalled();
      expect([...ctx.collections.keys()]).toEqual(['agentNotes']);
      expect(ctx.collections.get('agentNotes')?.deleteMany.mock.calls[0][0].videoId.$in).toContain(rowId);
      expect(ctx.idempotencyService.invalidateByUserVideoId).toHaveBeenCalledWith(rowId);
      expect(result.counts).toMatchObject({ userVideos: 1, idempotencyKeys: 1, agentNotes: 1, videoSummaryCache: 0 });
      expect(ctx.videoDeletionRepository.insert).not.toHaveBeenCalled();
    });

    it('should keep the notes when the user still holds the video in another folder', async () => {
      const ctx = build();
      const userId = new ObjectId().toString();
      const rowId = new ObjectId().toString();
      ctx.videoRepository.findUserVideo.mockResolvedValue({
        _id: new ObjectId(rowId), userId: new ObjectId(userId), videoSummaryId: ctx.summaryId, youtubeId: YT,
      });
      ctx.videoRepository.findUserVideoByYoutubeId.mockResolvedValue({ _id: new ObjectId() });

      const result = await ctx.service.deleteVideo({ scope: 'user', userId, userVideoId: rowId });

      expect([...ctx.collections.keys()]).toEqual([]);
      expect(result.counts.agentNotes).toBe(0);
    });

    it('should throw VideoNotFoundError when the row was already deleted by a concurrent request', async () => {
      const ctx = build();
      ctx.videoRepository.findUserVideo.mockResolvedValue({
        _id: new ObjectId(), userId: new ObjectId(), videoSummaryId: ctx.summaryId, youtubeId: YT,
      });
      ctx.videoRepository.deleteUserVideo.mockResolvedValue(false);

      await expect(
        ctx.service.deleteVideo({ scope: 'user', userId: new ObjectId().toString(), userVideoId: new ObjectId().toString() }),
      ).rejects.toMatchObject({ code: 'VIDEO_NOT_FOUND' });
      expect(ctx.idempotencyService.invalidateByUserVideoId).not.toHaveBeenCalled();
    });

    it('should throw VideoNotFoundError when the row belongs to someone else', async () => {
      const ctx = build();
      ctx.videoRepository.findUserVideo.mockResolvedValue(null);

      await expect(
        ctx.service.deleteVideo({ scope: 'user', userId: new ObjectId().toString(), userVideoId: new ObjectId().toString() }),
      ).rejects.toMatchObject({ code: 'VIDEO_NOT_FOUND' });
      expect(ctx.videoRepository.deleteUserVideo).not.toHaveBeenCalled();
    });
  });
});
