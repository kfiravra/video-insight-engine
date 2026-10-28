import { useMutation, useQueryClient } from "@tanstack/react-query";
import { playlistsApi, type PlaylistPreview, type PlaylistImportResult } from "@/api/playlists";
import type { ProviderConfig } from "@/api/videos";
import { queryKeys } from "@/lib/query-keys";
import { usageQueryKey } from "@/hooks/use-usage";

/**
 * Hook to preview a playlist before importing.
 * Returns metadata and cache status for each video.
 */
export function usePlaylistPreview() {
  return useMutation({
    mutationFn: async ({
      url,
      maxVideos,
    }: {
      url: string;
      maxVideos?: number;
    }): Promise<PlaylistPreview> => {
      const result = await playlistsApi.preview(url, maxVideos);
      return result.playlist;
    },
  });
}

/**
 * Hook to import a playlist.
 * Creates a folder and adds all videos.
 */
export function usePlaylistImport() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      url,
      folderId,
      maxVideos,
      providers,
    }: {
      url: string;
      folderId?: string;
      maxVideos?: number;
      providers?: ProviderConfig;
    }): Promise<PlaylistImportResult> => {
      return playlistsApi.import(url, folderId, maxVideos, providers);
    },
    onSuccess: () => {
      // Invalidate videos and folders to show new content
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.folders.lists() });
      queryClient.invalidateQueries({ queryKey: usageQueryKey });
    },
  });
}
