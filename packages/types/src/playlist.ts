// ═══════════════════════════════════════════════════
// Playlist Types
// ═══════════════════════════════════════════════════

export type PlaylistMode = 'video' | 'playlist';

/**
 * Playlist context stored in userVideos for ordering within a playlist.
 * Stored in userVideos (not cache) because same video can be in multiple playlists.
 */
export interface PlaylistInfo {
  playlistId: string;
  playlistTitle: string;
  position: number;       // 0-indexed order in playlist
  totalVideos: number;    // Total videos at import time
}

/** Video info within a playlist preview (before import). */
export interface PlaylistVideo {
  videoId: string;
  title: string;
  position: number;
  duration: number | null;
  thumbnailUrl: string | null;
  isCached: boolean;
}

/** Playlist preview response - returned before importing. */
export interface PlaylistPreview {
  playlistId: string;
  title: string;
  channel: string | null;
  thumbnailUrl: string | null;
  totalVideos: number;
  videos: PlaylistVideo[];
  cachedCount: number;
}

/** Video entry in playlist import result. */
export interface PlaylistImportVideo {
  id: string;
  videoSummaryId: string;
  youtubeId: string;
  title: string | null;
  status: string;
  position: number;
}

/** Result of importing a playlist. */
export interface PlaylistImportResult {
  /** null when the import created a folder and left it empty (the server deletes it). */
  folder: { id: string; name: string } | null;
  videos: PlaylistImportVideo[];
  totalVideos: number;
  cachedCount: number;
  processingCount: number;
  failedCount: number;
}
