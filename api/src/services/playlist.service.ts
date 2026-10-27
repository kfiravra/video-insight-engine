import { FastifyBaseLogger } from 'fastify';
import { ObjectId } from 'mongodb';
import { config } from '../config.js';
import { extractPlaylistId } from '../utils/youtube.js';
import {
  InvalidPlaylistUrlError,
  PlaylistNotFoundError,
  PlaylistExtractionError,
  FolderNotFoundError,
} from '../utils/errors.js';
import { VideoService } from './video.service.js';
import { FolderService } from './folder.service.js';
import { SummarizerClient, type ProviderConfig } from './summarizer-client.js';
import { CostMonitorService } from './cost-monitor.service.js';
import { DailyLimitReachedError } from '../utils/errors.js';
import type { UserTier } from '@vie/types';

// Types for summarizer response
interface SummarizerPlaylistVideo {
  video_id: string;
  title: string;
  position: number;
  duration: number | null;
  thumbnail_url: string | null;
}

interface SummarizerPlaylistResponse {
  playlist_id: string;
  title: string;
  channel: string | null;
  thumbnail_url: string | null;
  total_videos: number;
  videos: SummarizerPlaylistVideo[];
}

// Public types
export interface PlaylistVideo {
  videoId: string;
  title: string;
  position: number;
  duration: number | null;
  thumbnailUrl: string | null;
  isCached: boolean;
}

export interface PlaylistPreview {
  playlistId: string;
  title: string;
  channel: string | null;
  thumbnailUrl: string | null;
  totalVideos: number;
  videos: PlaylistVideo[];
  cachedCount: number;
}

export interface PlaylistInfo {
  playlistId: string;
  playlistTitle: string;
  position: number;
  totalVideos: number;
}

export interface FailedVideoImport {
  youtubeId: string;
  title: string;
  position: number;
  error: string;
}

export interface PlaylistImportResult {
  folder: { id: string; name: string };
  videos: Array<{
    id: string;
    videoSummaryId: string;
    youtubeId: string;
    title: string | null;
    status: string;
    position: number;
  }>;
  totalVideos: number;
  cachedCount: number;
  processingCount: number;
  failedCount: number;
  failedVideos: FailedVideoImport[];
}

const EXTRACT_TIMEOUT_MS = 30000; // 30 seconds for extraction

export class PlaylistService {
  constructor(
    private readonly videoService: VideoService,
    private readonly folderService: FolderService,
    private readonly summarizerClient: SummarizerClient,
    private readonly logger: FastifyBaseLogger,
    private readonly costMonitorService?: CostMonitorService,
  ) {}

  /**
   * Preview a playlist before importing.
   * Extracts metadata and checks cache status for each video.
   */
  async preview(url: string, maxVideos = 100): Promise<PlaylistPreview> {
    const playlistId = extractPlaylistId(url);
    if (!playlistId) {
      throw new InvalidPlaylistUrlError();
    }

    // Call summarizer to extract playlist data
    const playlistData = await this.extractFromSummarizer(playlistId, maxVideos);

    // Check cache status for each video
    const videoIds = playlistData.videos.map(v => v.video_id);
    const cachedVideos = await this.getCachedVideoIds(videoIds);

    const videos: PlaylistVideo[] = playlistData.videos.map(v => ({
      videoId: v.video_id,
      title: v.title,
      position: v.position,
      duration: v.duration,
      thumbnailUrl: v.thumbnail_url,
      isCached: cachedVideos.has(v.video_id),
    }));

    const cachedCount = videos.filter(v => v.isCached).length;

    return {
      playlistId: playlistData.playlist_id,
      title: playlistData.title,
      channel: playlistData.channel,
      thumbnailUrl: playlistData.thumbnail_url,
      totalVideos: playlistData.total_videos,
      videos,
      cachedCount,
    };
  }

  /**
   * Import a playlist, creating videos for each entry.
   * Creates a folder with the playlist name if folderId not provided.
   */
  async import(
    userId: string,
    url: string,
    folderId?: string,
    maxVideos = 100,
    providers?: ProviderConfig,
    tier: UserTier = 'free'
  ): Promise<PlaylistImportResult> {
    const playlistId = extractPlaylistId(url);
    if (!playlistId) {
      throw new InvalidPlaylistUrlError();
    }

    // Extract playlist data
    const playlistData = await this.extractFromSummarizer(playlistId, maxVideos);

    // Create folder if not provided
    let folder: { id: string; name: string };
    if (folderId) {
      // Use existing folder - verify it exists
      try {
        const existingFolder = await this.folderService.getById(userId, folderId);
        folder = { id: existingFolder.id, name: existingFolder.name };
      } catch {
        throw new FolderNotFoundError();
      }
    } else {
      // Create new folder with playlist name
      const folderName = this.sanitizeFolderName(playlistData.title);
      const newFolder = await this.folderService.create({
        userId,
        name: folderName,
      });
      folder = { id: newFolder.id, name: newFolder.name };
    }

    // Process each video
    const results: PlaylistImportResult['videos'] = [];
    const failedVideos: FailedVideoImport[] = [];
    let cachedCount = 0;
    let processingCount = 0;

    // Track whether the per-user cost cap stopped the import mid-loop. Subsequent
    // videos are recorded as failed with a stable error so the UI can show a
    // "stopped at video N because you hit your daily limit" message.
    let limitReached = false;

    for (const video of playlistData.videos) {
      if (limitReached) {
        failedVideos.push({
          youtubeId: video.video_id,
          title: video.title,
          position: video.position,
          error: 'DAILY_LIMIT_REACHED',
        });
        continue;
      }

      let reservation = null;
      try {
        const videoUrl = `https://www.youtube.com/watch?v=${video.video_id}`;

        if (this.costMonitorService) {
          reservation = await this.costMonitorService.reserveUserCost(userId, tier);
        }

        const result = await this.videoService.createVideo(
          userId,
          videoUrl,
          { folderId: folder.id, bypassCache: false, providers, tier }
        );

        // Cached videos run no pipeline, so no llm_usage will land — refund.
        // Attached videos (cross-user single-flight) are the same: this caller
        // didn't trigger work. A failed refund must not fail the import — the
        // video exists, and the catch below would refund a second time.
        if ((result.cached || result.attached) && this.costMonitorService) {
          await this.costMonitorService.refundReservation(reservation).catch((refundErr) => {
            this.logger.warn(
              { err: refundErr, userId, youtubeId: video.video_id },
              'cached/attached-video refund failed during playlist import',
            );
          });
        }

        results.push({
          id: result.video.id,
          videoSummaryId: result.video.videoSummaryId,
          youtubeId: video.video_id,
          title: result.video.title ?? video.title,
          status: result.video.status,
          position: video.position,
        });

        if (result.cached) {
          cachedCount++;
        } else {
          processingCount++;
        }
      } catch (err) {
        if (this.costMonitorService && reservation) {
          await this.costMonitorService.refundReservation(reservation);
        }
        const errorMessage = err instanceof Error ? err.message : String(err);

        if (err instanceof DailyLimitReachedError) {
          limitReached = true;
          this.logger.info(
            { userId, importedSoFar: results.length },
            'Playlist import stopped at per-user cost cap',
          );
          failedVideos.push({
            youtubeId: video.video_id,
            title: video.title,
            position: video.position,
            error: 'DAILY_LIMIT_REACHED',
          });
          continue;
        }

        this.logger.warn({ youtubeId: video.video_id, error: errorMessage }, 'Failed to import video');
        failedVideos.push({
          youtubeId: video.video_id,
          title: video.title,
          position: video.position,
          error: errorMessage,
        });
      }
    }

    return {
      folder,
      videos: results,
      totalVideos: playlistData.total_videos,
      cachedCount,
      processingCount,
      failedCount: failedVideos.length,
      failedVideos,
    };
  }

  /**
   * Get all videos in a playlist for a user, sorted by position.
   */
  async getPlaylistVideos(userId: string, playlistId: string) {
    // This method needs direct repository access - we'll access via videoService
    // For now, we'll use a simplified approach
    // In production, this should be exposed via VideoService
    return [];
  }

  /**
   * Extract playlist data from summarizer service.
   */
  private async extractFromSummarizer(playlistId: string, maxVideos: number): Promise<SummarizerPlaylistResponse> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), EXTRACT_TIMEOUT_MS);

    try {
      const response = await fetch(`${config.SUMMARIZER_URL}/playlist/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          playlist_id: playlistId,
          max_videos: maxVideos,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        if (response.status === 404) {
          throw new PlaylistNotFoundError();
        }
        const error = await response.text();
        throw new PlaylistExtractionError(`Summarizer error: ${error}`);
      }

      return await response.json() as SummarizerPlaylistResponse;
    } catch (err) {
      if (err instanceof InvalidPlaylistUrlError ||
          err instanceof PlaylistNotFoundError ||
          err instanceof PlaylistExtractionError) {
        throw err;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        throw new PlaylistExtractionError('Playlist extraction timed out');
      }
      throw new PlaylistExtractionError(`Failed to extract playlist: ${err}`);
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /**
   * Check which video IDs are already cached (completed).
   * TODO: This should be exposed via VideoRepository
   */
  private async getCachedVideoIds(_videoIds: string[]): Promise<Set<string>> {
    // Simplified implementation - in production this should use repository
    return new Set();
  }

  /**
   * Sanitize playlist title for use as folder name.
   */
  private sanitizeFolderName(title: string): string {
    // Remove invalid characters, limit length
    return title
      .replace(/[/\\?%*:|"<>]/g, '')
      .trim()
      .substring(0, 100) || 'Playlist';
  }
}
