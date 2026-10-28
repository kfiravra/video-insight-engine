import { request } from "./client";
import type { ProviderConfig } from "./videos";

// Types matching backend responses
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

export interface FailedVideoImport {
  youtubeId: string;
  title: string;
  position: number;
  /** The error message, or a stable code such as INSUFFICIENT_MINUTES. */
  error: string;
  /** The billing error code when the server sends it apart from `error`. */
  code?: string;
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

export interface PlaylistVideoDetail {
  id: string;
  videoSummaryId: string;
  youtubeId: string;
  title: string;
  channel: string | null;
  duration: number | null;
  thumbnailUrl: string | null;
  status: string;
  folderId: string | null;
  playlistInfo: {
    playlistId: string;
    playlistTitle: string;
    position: number;
    totalVideos: number;
  };
  createdAt: string;
}

export const playlistsApi = {
  /**
   * Preview a playlist before importing.
   * Returns metadata and cache status for each video.
   */
  async preview(
    url: string,
    maxVideos?: number
  ): Promise<{ playlist: PlaylistPreview }> {
    return request("/playlists/preview", {
      method: "POST",
      body: JSON.stringify({
        url,
        maxVideos,
      }),
    });
  },

  /**
   * Import a playlist, creating a folder and adding all videos.
   */
  async import(
    url: string,
    folderId?: string,
    maxVideos?: number,
    providers?: ProviderConfig
  ): Promise<PlaylistImportResult> {
    return request("/playlists/import", {
      method: "POST",
      body: JSON.stringify({
        url,
        folderId,
        maxVideos,
        providers,
      }),
    });
  },

  /**
   * Get videos in a playlist, sorted by position.
   */
  async getPlaylistVideos(
    playlistId: string
  ): Promise<{ videos: PlaylistVideoDetail[] }> {
    return request(`/playlists/${encodeURIComponent(playlistId)}/videos`);
  },
};
