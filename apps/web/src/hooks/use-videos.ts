import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { videosApi, type ProviderConfig } from "@/api/videos";
import { queryKeys } from "@/lib/query-keys";
import { usageQueryKey } from "@/hooks/use-usage";

// Fetch all videos (no folder filter)
export function useAllVideos(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.videos.list(),
    queryFn: () => videosApi.list(),
    enabled: options?.enabled ?? true,
  });
}

// Fetch videos list (optionally filtered by folder)
export function useVideos(folderId?: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.videos.list(folderId),
    queryFn: () => videosApi.list({ folderId }),
    enabled: options?.enabled ?? true,
  });
}

// Fetch single video
export function useVideo(id: string) {
  return useQuery({
    queryKey: queryKeys.videos.detail(id),
    queryFn: () => videosApi.get(id),
    enabled: !!id,
  });
}

// Add video mutation
export function useAddVideo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      url,
      folderId,
      bypassCache,
      providers,
    }: {
      url: string;
      folderId?: string | null;
      bypassCache?: boolean;
      providers?: ProviderConfig;
    }) => {
      return videosApi.create(url, folderId ?? undefined, bypassCache, providers);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
      // A submit can spend the free video or hold minutes; refresh the tile.
      queryClient.invalidateQueries({ queryKey: usageQueryKey });
    },
  });
}

// Delete video mutation
export function useDeleteVideo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => videosApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
    },
  });
}

// Move video to folder mutation
export function useMoveVideo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, folderId }: { id: string; folderId: string | null }) =>
      videosApi.moveToFolder(id, folderId),
    onSuccess: () => {
      // Invalidate video queries
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
      // Also invalidate folder queries to update video counts
      queryClient.invalidateQueries({ queryKey: queryKeys.folders.lists() });
    },
  });
}

// Bulk delete videos mutation
export function useBulkDeleteVideos() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (videoIds: string[]) => {
      // Delete videos in parallel
      await Promise.all(videoIds.map((id) => videosApi.delete(id)));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.folders.lists() });
    },
  });
}

// Bulk move videos to folder mutation
export function useBulkMoveVideos() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      videoIds,
      folderId,
    }: {
      videoIds: string[];
      folderId: string | null;
    }) => {
      // Move videos in parallel
      await Promise.all(
        videoIds.map((id) => videosApi.moveToFolder(id, folderId))
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.folders.lists() });
    },
  });
}

// Retry/re-summarize a failed video
export function useRetryVideo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      youtubeId,
      folderId,
    }: {
      youtubeId: string;
      folderId?: string | null;
    }) => {
      // Create new version with bypassCache to force re-summarization
      const url = `https://www.youtube.com/watch?v=${youtubeId}`;
      return videosApi.create(url, folderId ?? undefined, true);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
    },
  });
}
