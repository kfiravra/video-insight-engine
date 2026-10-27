import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';
import { ObjectId } from 'mongodb';
import { PlaylistService } from '../playlist.service.js';
import type { VideoService } from '../video.service.js';
import type { FolderService, FolderResponse } from '../folder.service.js';
import type { SummarizerClient } from '../summarizer-client.js';
import type { CostMonitorService } from '../cost-monitor.service.js';
import {
  DailyLimitReachedError,
  InvalidPlaylistUrlError,
  PlaylistNotFoundError,
  PlaylistExtractionError,
  FolderNotFoundError,
} from '../../utils/errors.js';

// Mock logger
const mockLogger = {
  info: vi.fn(),
  error: vi.fn(),
  warn: vi.fn(),
  debug: vi.fn(),
  trace: vi.fn(),
  fatal: vi.fn(),
  child: vi.fn(() => mockLogger),
  level: 'silent',
  silent: vi.fn(),
} as unknown as FastifyBaseLogger;

// Mock global fetch
const mockFetch = vi.fn();
global.fetch = mockFetch;

// Factory functions
function createPlaylistResponse(overrides: Record<string, unknown> = {}) {
  return {
    playlist_id: 'PLtest123',
    title: 'Test Playlist',
    channel: 'Test Channel',
    thumbnail_url: 'https://example.com/thumb.jpg',
    total_videos: 3,
    videos: [
      {
        video_id: 'video1',
        title: 'Video 1',
        position: 1,
        duration: 120,
        thumbnail_url: 'https://example.com/v1.jpg',
      },
      {
        video_id: 'video2',
        title: 'Video 2',
        position: 2,
        duration: 180,
        thumbnail_url: 'https://example.com/v2.jpg',
      },
      {
        video_id: 'video3',
        title: 'Video 3',
        position: 3,
        duration: 240,
        thumbnail_url: 'https://example.com/v3.jpg',
      },
    ],
    ...overrides,
  };
}

function createFolderResponse(overrides: Partial<FolderResponse> = {}): FolderResponse {
  return {
    id: new ObjectId().toHexString(),
    name: 'Test Folder',
    parentId: null,
    path: '/Test Folder',
    level: 1,
    color: null,
    icon: null,
    order: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function createVideoCreateResult(overrides: Record<string, unknown> = {}) {
  return {
    video: {
      id: new ObjectId().toHexString(),
      videoSummaryId: new ObjectId().toHexString(),
      youtubeId: 'videoId',
      title: 'Video Title',
      status: 'pending',
      ...overrides,
    },
    cached: false,
  };
}

describe('PlaylistService', () => {
  let playlistService: PlaylistService;
  let mockVideoService: {
    createVideo: ReturnType<typeof vi.fn>;
  };
  let mockFolderService: {
    getById: ReturnType<typeof vi.fn>;
    create: ReturnType<typeof vi.fn>;
  };
  let mockSummarizerClient: Record<string, unknown>;

  beforeEach(() => {
    mockVideoService = {
      createVideo: vi.fn(),
    };

    mockFolderService = {
      getById: vi.fn(),
      create: vi.fn(),
    };

    mockSummarizerClient = {};

    playlistService = new PlaylistService(
      mockVideoService as unknown as VideoService,
      mockFolderService as unknown as FolderService,
      mockSummarizerClient as unknown as SummarizerClient,
      mockLogger
    );

    // Reset fetch mock
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('preview', () => {
    it('should return playlist preview with video cache status', async () => {
      const playlistData = createPlaylistResponse();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });

      const result = await playlistService.preview(
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(result.playlistId).toBe('PLtest123');
      expect(result.title).toBe('Test Playlist');
      expect(result.channel).toBe('Test Channel');
      expect(result.totalVideos).toBe(3);
      expect(result.videos).toHaveLength(3);
      expect(result.videos[0]).toMatchObject({
        videoId: 'video1',
        title: 'Video 1',
        position: 1,
        duration: 120,
        isCached: false,
      });
    });

    it('should extract playlist ID from watch URL with list parameter', async () => {
      const playlistData = createPlaylistResponse();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });

      await playlistService.preview(
        'https://www.youtube.com/watch?v=abc123&list=PLtest123'
      );

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: expect.stringContaining('"playlist_id":"PLtest123"'),
        })
      );
    });

    it('should throw InvalidPlaylistUrlError for invalid URL', async () => {
      await expect(
        playlistService.preview('https://www.youtube.com/watch?v=abc123')
      ).rejects.toThrow(InvalidPlaylistUrlError);
    });

    it('should throw PlaylistNotFoundError when playlist does not exist', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 404,
        text: () => Promise.resolve('Not found'),
      });

      await expect(
        playlistService.preview('https://www.youtube.com/playlist?list=PLnonexistent')
      ).rejects.toThrow(PlaylistNotFoundError);
    });

    it('should throw PlaylistExtractionError on server error', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        text: () => Promise.resolve('Internal server error'),
      });

      await expect(
        playlistService.preview('https://www.youtube.com/playlist?list=PLtest123')
      ).rejects.toThrow(PlaylistExtractionError);
    });

    it('should respect maxVideos parameter', async () => {
      const playlistData = createPlaylistResponse();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });

      await playlistService.preview('https://www.youtube.com/playlist?list=PLtest123', 50);

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: expect.stringContaining('"max_videos":50'),
        })
      );
    });

    it('should use default maxVideos of 100', async () => {
      const playlistData = createPlaylistResponse();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });

      await playlistService.preview('https://www.youtube.com/playlist?list=PLtest123');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: expect.stringContaining('"max_videos":100'),
        })
      );
    });
  });

  describe('import', () => {
    it('should create new folder and import all videos', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({ total_videos: 2, videos: [
        { video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb1.jpg' },
        { video_id: 'video2', title: 'Video 2', position: 2, duration: 180, thumbnail_url: 'thumb2.jpg' },
      ]});
      const folder = createFolderResponse({ id: 'folder123', name: 'Test Playlist' });

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo
        .mockResolvedValueOnce(createVideoCreateResult({ youtubeId: 'video1', title: 'Video 1' }))
        .mockResolvedValueOnce(createVideoCreateResult({ youtubeId: 'video2', title: 'Video 2' }));

      const result = await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(mockFolderService.create).toHaveBeenCalledWith({
        userId,
        name: 'Test Playlist',
          });
      expect(mockVideoService.createVideo).toHaveBeenCalledTimes(2);
      expect(result.folder.id).toBe('folder123');
      expect(result.videos).toHaveLength(2);
      expect(result.totalVideos).toBe(2);
      expect(result.processingCount).toBe(2);
    });

    it('should use existing folder when folderId provided', async () => {
      const userId = new ObjectId().toHexString();
      const folderId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({ total_videos: 1, videos: [
        { video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb1.jpg' },
      ]});
      const folder = createFolderResponse({ id: folderId, name: 'Existing Folder' });

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.getById.mockResolvedValue(folder);
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      const result = await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123',
        folderId
      );

      expect(mockFolderService.getById).toHaveBeenCalledWith(userId, folderId);
      expect(mockFolderService.create).not.toHaveBeenCalled();
      expect(result.folder.id).toBe(folderId);
    });

    it('should throw FolderNotFoundError when provided folder does not exist', async () => {
      const userId = new ObjectId().toHexString();
      const folderId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse();

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.getById.mockRejectedValue(new Error('Not found'));

      await expect(
        playlistService.import(
          userId,
          'https://www.youtube.com/playlist?list=PLtest123',
          folderId
        )
      ).rejects.toThrow(FolderNotFoundError);
    });

    it('should track cached videos correctly', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({ total_videos: 2, videos: [
        { video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb1.jpg' },
        { video_id: 'video2', title: 'Video 2', position: 2, duration: 180, thumbnail_url: 'thumb2.jpg' },
      ]});
      const folder = createFolderResponse();

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo
        .mockResolvedValueOnce({ ...createVideoCreateResult(), cached: true })
        .mockResolvedValueOnce({ ...createVideoCreateResult(), cached: false });

      const result = await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(result.cachedCount).toBe(1);
      expect(result.processingCount).toBe(1);
    });

    it('should track failed videos', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({ total_videos: 2, videos: [
        { video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb1.jpg' },
        { video_id: 'video2', title: 'Video 2', position: 2, duration: 180, thumbnail_url: 'thumb2.jpg' },
      ]});
      const folder = createFolderResponse();

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo
        .mockResolvedValueOnce(createVideoCreateResult())
        .mockRejectedValueOnce(new Error('Failed to process video'));

      const result = await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(result.videos).toHaveLength(1);
      expect(result.failedCount).toBe(1);
      expect(result.failedVideos).toHaveLength(1);
      expect(result.failedVideos[0]).toMatchObject({
        youtubeId: 'video2',
        title: 'Video 2',
        position: 2,
        error: 'Failed to process video',
      });
    });

    it('should include video positions in results', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({ total_videos: 2, videos: [
        { video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb1.jpg' },
        { video_id: 'video2', title: 'Video 2', position: 2, duration: 180, thumbnail_url: 'thumb2.jpg' },
      ]});
      const folder = createFolderResponse();

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo
        .mockResolvedValueOnce(createVideoCreateResult({ youtubeId: 'video1' }))
        .mockResolvedValueOnce(createVideoCreateResult({ youtubeId: 'video2' }));

      const result = await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(result.videos[0].position).toBe(1);
      expect(result.videos[1].position).toBe(2);
    });

    it('should sanitize folder name by removing invalid characters', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({
        title: 'Test/Playlist: <Special> "Characters"',
        total_videos: 1,
        videos: [{ video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb.jpg' }],
      });
      const folder = createFolderResponse();

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(mockFolderService.create).toHaveBeenCalledWith({
        userId,
        name: 'TestPlaylist Special Characters',
          });
    });

    it('should use "Playlist" as fallback name when title becomes empty', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({
        title: '///???',
        total_videos: 1,
        videos: [{ video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb.jpg' }],
      });
      const folder = createFolderResponse();

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123'
      );

      expect(mockFolderService.create).toHaveBeenCalledWith({
        userId,
        name: 'Playlist',
          });
    });

    it('should pass provider config to video service', async () => {
      const userId = new ObjectId().toHexString();
      const playlistData = createPlaylistResponse({ total_videos: 1, videos: [
        { video_id: 'video1', title: 'Video 1', position: 1, duration: 120, thumbnail_url: 'thumb.jpg' },
      ]});
      const folder = createFolderResponse();
      const providers = { transcriptProvider: 'custom' as const };

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });
      mockFolderService.create.mockResolvedValue(folder);
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      await playlistService.import(
        userId,
        'https://www.youtube.com/playlist?list=PLtest123',
        undefined,
        100,
        providers
      );

      expect(mockVideoService.createVideo).toHaveBeenCalledWith(
        userId,
        'https://www.youtube.com/watch?v=video1',
        {
          folderId: folder.id,
          bypassCache: false,
          providers,
          tier: 'free',
        }
      );
    });

    it('should throw InvalidPlaylistUrlError for invalid URL', async () => {
      const userId = new ObjectId().toHexString();

      await expect(
        playlistService.import(
          userId,
          'https://www.youtube.com/watch?v=abc123'
        )
      ).rejects.toThrow(InvalidPlaylistUrlError);
    });
  });

  describe('import with a cost monitor', () => {
    const PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLtest123';
    let mockCostMonitor: {
      reserveUserCost: ReturnType<typeof vi.fn>;
      refundReservation: ReturnType<typeof vi.fn>;
    };
    let userId: string;

    function createReservation(userIdForReservation: string, amountUsd = 0.15) {
      return { userId: userIdForReservation, dateKey: '2026-10-27', amountUsd };
    }

    function useVideos(count: number) {
      const videos = Array.from({ length: count }, (_, index) => ({
        video_id: `video${index + 1}`,
        title: `Video ${index + 1}`,
        position: index + 1,
        duration: 120,
        thumbnail_url: `thumb${index + 1}.jpg`,
      }));
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(createPlaylistResponse({ total_videos: count, videos })),
      });
      mockFolderService.create.mockResolvedValue(createFolderResponse());
    }

    beforeEach(() => {
      userId = new ObjectId().toHexString();
      mockCostMonitor = {
        reserveUserCost: vi.fn(),
        refundReservation: vi.fn().mockResolvedValue(undefined),
      };
      playlistService = new PlaylistService(
        mockVideoService as unknown as VideoService,
        mockFolderService as unknown as FolderService,
        mockSummarizerClient as unknown as SummarizerClient,
        mockLogger,
        mockCostMonitor as unknown as CostMonitorService,
      );
    });

    it('should refund the reservation when the video was served from cache', async () => {
      useVideos(1);
      const reservation = createReservation(userId);
      mockCostMonitor.reserveUserCost.mockResolvedValue(reservation);
      mockVideoService.createVideo.mockResolvedValue({ ...createVideoCreateResult(), cached: true });

      await playlistService.import(userId, PLAYLIST_URL);

      expect(mockCostMonitor.refundReservation.mock.calls).toEqual([[reservation]]);
    });

    it('should refund the reservation when the video attached to an in-flight run', async () => {
      useVideos(1);
      const reservation = createReservation(userId);
      mockCostMonitor.reserveUserCost.mockResolvedValue(reservation);
      mockVideoService.createVideo.mockResolvedValue({ ...createVideoCreateResult(), attached: true });

      await playlistService.import(userId, PLAYLIST_URL);

      expect(mockCostMonitor.refundReservation.mock.calls).toEqual([[reservation]]);
    });

    it('should keep the reservation when a new pipeline run was created', async () => {
      useVideos(1);
      mockCostMonitor.reserveUserCost.mockResolvedValue(createReservation(userId));
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      const result = await playlistService.import(userId, PLAYLIST_URL);

      expect(mockCostMonitor.refundReservation).not.toHaveBeenCalled();
      expect(result.processingCount).toBe(1);
    });

    it('should still report the video as imported when the refund itself fails', async () => {
      useVideos(1);
      mockCostMonitor.reserveUserCost.mockResolvedValue(createReservation(userId));
      mockCostMonitor.refundReservation.mockRejectedValue(new Error('mongo down'));
      mockVideoService.createVideo.mockResolvedValue({ ...createVideoCreateResult(), attached: true });

      const result = await playlistService.import(userId, PLAYLIST_URL);

      expect(result.videos).toHaveLength(1);
      expect(result.failedVideos).toEqual([]);
      expect(mockCostMonitor.refundReservation).toHaveBeenCalledTimes(1);
    });

    it('should refund the reservation when createVideo throws', async () => {
      useVideos(1);
      const reservation = createReservation(userId);
      mockCostMonitor.reserveUserCost.mockResolvedValue(reservation);
      mockVideoService.createVideo.mockRejectedValue(new Error('Failed to process video'));

      const result = await playlistService.import(userId, PLAYLIST_URL);

      expect(mockCostMonitor.refundReservation.mock.calls).toEqual([[reservation]]);
      expect(result.failedVideos[0]).toMatchObject({ youtubeId: 'video1', error: 'Failed to process video' });
    });

    it('should mark the failing item and every later item DAILY_LIMIT_REACHED when the cap trips mid-import', async () => {
      useVideos(4);
      mockCostMonitor.reserveUserCost
        .mockResolvedValueOnce(createReservation(userId))
        .mockRejectedValueOnce(new DailyLimitReachedError(1, '2026-10-28T00:00:00.000Z'));
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      const result = await playlistService.import(userId, PLAYLIST_URL);

      expect(result.videos).toHaveLength(1);
      expect(result.failedVideos.map(failed => [failed.youtubeId, failed.error])).toEqual([
        ['video2', 'DAILY_LIMIT_REACHED'],
        ['video3', 'DAILY_LIMIT_REACHED'],
        ['video4', 'DAILY_LIMIT_REACHED'],
      ]);
    });

    it('should not reserve or create anything for items after the cap trips', async () => {
      useVideos(4);
      mockCostMonitor.reserveUserCost
        .mockResolvedValueOnce(createReservation(userId))
        .mockRejectedValueOnce(new DailyLimitReachedError(1, '2026-10-28T00:00:00.000Z'));
      mockVideoService.createVideo.mockResolvedValue(createVideoCreateResult());

      await playlistService.import(userId, PLAYLIST_URL);

      expect(mockCostMonitor.reserveUserCost).toHaveBeenCalledTimes(2);
      expect(mockVideoService.createVideo).toHaveBeenCalledTimes(1);
    });

    it('should refund nothing for the item that hit the cap since no reservation was taken', async () => {
      useVideos(2);
      mockCostMonitor.reserveUserCost.mockRejectedValue(new DailyLimitReachedError(1, '2026-10-28T00:00:00.000Z'));

      const result = await playlistService.import(userId, PLAYLIST_URL);

      expect(mockCostMonitor.refundReservation).not.toHaveBeenCalled();
      expect(result.failedCount).toBe(2);
    });
  });

  describe('getPlaylistVideos', () => {
    it('should return empty array (simplified implementation)', async () => {
      const userId = new ObjectId().toHexString();
      const playlistId = 'PLtest123';

      const result = await playlistService.getPlaylistVideos(userId, playlistId);

      expect(result).toEqual([]);
    });
  });

  describe('extraction timeout', () => {
    it('should throw PlaylistExtractionError on timeout', async () => {
      // Mock AbortController and timeout
      vi.useFakeTimers();

      const abortError = new Error('Aborted');
      abortError.name = 'AbortError';
      mockFetch.mockRejectedValue(abortError);

      const promise = playlistService.preview('https://www.youtube.com/playlist?list=PLtest123');

      await expect(promise).rejects.toThrow(PlaylistExtractionError);
      await expect(promise).rejects.toThrow('Playlist extraction timed out');

      vi.useRealTimers();
    });

    it('should throw PlaylistExtractionError for network errors', async () => {
      mockFetch.mockRejectedValue(new Error('Network error'));

      await expect(
        playlistService.preview('https://www.youtube.com/playlist?list=PLtest123')
      ).rejects.toThrow(PlaylistExtractionError);
    });
  });

  describe('URL edge cases', () => {
    it('should handle short youtu.be URLs with playlist', async () => {
      const playlistData = createPlaylistResponse();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });

      await playlistService.preview('https://youtu.be/abc123?list=PLtest123');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: expect.stringContaining('"playlist_id":"PLtest123"'),
        })
      );
    });

    it('should handle playlist URLs with additional parameters', async () => {
      const playlistData = createPlaylistResponse();
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(playlistData),
      });

      await playlistService.preview(
        'https://www.youtube.com/playlist?list=PLtest123&index=5'
      );

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: expect.stringContaining('"playlist_id":"PLtest123"'),
        })
      );
    });
  });
});
