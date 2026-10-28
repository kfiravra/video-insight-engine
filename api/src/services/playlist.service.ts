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
import { LedgerItemImporter } from './playlist-items.js';
import type { UserTier } from '@vie/types';
import type { LedgerEnforcement } from './billing/ledger-enforcement.js';
import type { FailedVideoImport, ImportedVideo, ItemImporter, ItemOutcome } from './playlist-items.js';

export type { FailedVideoImport } from './playlist-items.js';

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

export interface PlaylistImportResult {
  folder: { id: string; name: string };
  videos: ImportedVideo[];
  totalVideos: number;
  cachedCount: number;
  processingCount: number;
  failedCount: number;
  failedVideos: FailedVideoImport[];
  /** The up-front quote ("needs 340 min, you have 120"). */
  billing: { neededMinutes: number; availableMinutes: number; freeVideoAvailable: boolean };
}

const EXTRACT_TIMEOUT_MS = 30000; // 30 seconds for extraction

export class PlaylistService {
  constructor(
    private readonly videoService: VideoService,
    private readonly folderService: FolderService,
    private readonly summarizerClient: SummarizerClient,
    // The minutes ledger decides every item (P3.1; the only gate since P3.2).
    private readonly billingEnforcement: LedgerEnforcement,
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
    // Quote the whole playlist before anything is created; nothing affordable → refused here.
    const plan = await this.billingEnforcement.planPlaylist(
      userId,
      playlistData.videos.map((v) => ({ youtubeId: v.video_id, durationSec: v.duration })),
    );
    const folder = await this.resolveFolder(userId, folderId, playlistData.title);
    const importer = new LedgerItemImporter(
      this.videoService, this.billingEnforcement, { userId, folderId: folder.id, providers, tier }, plan.durationsSec,
    );
    const result = await this.importItems(importer, playlistData.videos);
    return {
      folder,
      ...result,
      totalVideos: playlistData.total_videos,
      billing: {
        neededMinutes: plan.quote.neededMinutes,
        availableMinutes: plan.quote.availableMinutes,
        freeVideoAvailable: plan.quote.freeVideoAvailable,
      },
    };
  }

  private async resolveFolder(userId: string, folderId: string | undefined, playlistTitle: string): Promise<{ id: string; name: string }> {
    if (folderId) {
      // Use existing folder - verify it exists
      try {
        const existingFolder = await this.folderService.getById(userId, folderId);
        return { id: existingFolder.id, name: existingFolder.name };
      } catch {
        throw new FolderNotFoundError();
      }
    }
    // Create new folder with playlist name
    const newFolder = await this.folderService.create({ userId, name: this.sanitizeFolderName(playlistTitle) });
    return { id: newFolder.id, name: newFolder.name };
  }

  /**
   * Items in playlist order. A refusal that later items cannot do better on
   * (out of minutes, too many runs, blocked) stops the import there and
   * records every later item with the same error, so the UI can say "stopped
   * at video N" — the import admits a prefix. A later item already cached or
   * running is still served (Q6).
   */
  private async importItems(
    importer: ItemImporter,
    items: SummarizerPlaylistVideo[],
  ): Promise<Omit<PlaylistImportResult, 'folder' | 'totalVideos' | 'billing'>> {
    const videos: ImportedVideo[] = [];
    const failedVideos: FailedVideoImport[] = [];
    let cachedCount = 0;
    let stoppedWith: string | null = null;
    for (const [index, item] of items.entries()) {
      const outcome: ItemOutcome = stoppedWith ? await importer.skip(item, stoppedWith) : await importer.import(item, index);
      if (outcome.kind === 'failed') {
        failedVideos.push(outcome.failure);
        if (outcome.stopsImport) stoppedWith = outcome.failure.error;
        continue;
      }
      videos.push(outcome.video);
      if (outcome.cached) cachedCount++;
      if (outcome.stopsImport) stoppedWith = outcome.stopsImport;
    }
    return { videos, cachedCount, processingCount: videos.length - cachedCount, failedCount: failedVideos.length, failedVideos };
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
