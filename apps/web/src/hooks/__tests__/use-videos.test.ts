import { describe, it, expect, vi, beforeEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { renderHook, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createWrapper, createTestQueryClient } from "../../test/test-utils";
import { queryKeys } from "@/lib/query-keys";
import { server } from "../../test/mocks/server";
import { createMockVideo } from "../../test/mocks/handlers";
import {
  useVideos,
  useAllVideos,
  useVideo,
  useAddVideo,
  useDeleteVideo,
  useMoveVideo,
  useBulkDeleteVideos,
  useBulkMoveVideos,
} from "../use-videos";

const API_URL = "http://localhost:3000/api";

describe("useVideos", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("useAllVideos", () => {
    it("should fetch all videos on mount", async () => {
      const { result } = renderHook(() => useAllVideos(), {
        wrapper: createWrapper(),
      });

      // Initial loading state
      expect(result.current.isLoading).toBe(true);

      // Wait for data to load
      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });

      // Verify data
      expect(result.current.data?.videos).toHaveLength(2);
      expect(result.current.data?.videos[0].id).toBe("video-1");
    });

    it("should handle loading state correctly", async () => {
      const { result } = renderHook(() => useAllVideos(), {
        wrapper: createWrapper(),
      });

      // Should be loading initially
      expect(result.current.isLoading).toBe(true);
      expect(result.current.isFetching).toBe(true);

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });

      // After load, should not be loading
      expect(result.current.isFetching).toBe(false);
    });

    it("should handle error state", async () => {
      server.use(
        http.get(`${API_URL}/videos`, () => {
          return HttpResponse.json(
            { message: "Server error" },
            { status: 500 }
          );
        })
      );

      const { result } = renderHook(() => useAllVideos(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
    });

    it("should support refetch functionality", async () => {
      const { result } = renderHook(() => useAllVideos(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      // Trigger refetch
      await act(async () => {
        await result.current.refetch();
      });

      await waitFor(() => {
        expect(result.current.isFetching).toBe(false);
      });

      expect(result.current.data?.videos).toHaveLength(2);
    });
  });

  describe("useVideos", () => {
    it("should fetch videos without folder filter", async () => {
      const { result } = renderHook(() => useVideos(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data?.videos).toHaveLength(2);
    });

    it("should filter videos by folder ID", async () => {
      server.use(
        http.get(`${API_URL}/videos`, ({ request }) => {
          const url = new URL(request.url);
          const folderId = url.searchParams.get("folderId");

          if (folderId === "folder-1") {
            return HttpResponse.json({
              videos: [createMockVideo({ id: "video-2", folderId: "folder-1" })],
              total: 1,
              hasMore: false,
            });
          }

          return HttpResponse.json({
            videos: [],
            total: 0,
            hasMore: false,
          });
        })
      );

      const { result } = renderHook(() => useVideos("folder-1"), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data?.videos).toHaveLength(1);
      expect(result.current.data?.videos[0].folderId).toBe("folder-1");
    });
  });

  describe("useVideo", () => {
    it("should fetch a single video by ID", async () => {
      const { result } = renderHook(() => useVideo("video-1"), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data?.video.id).toBe("video-1");
      expect(result.current.data?.summary).toBeDefined();
    });

    it("should not fetch when ID is empty", async () => {
      const { result } = renderHook(() => useVideo(""), {
        wrapper: createWrapper(),
      });

      // Should be disabled and not loading
      expect(result.current.isLoading).toBe(false);
      expect(result.current.data).toBeUndefined();
    });

    it("should handle 404 error", async () => {
      server.use(
        http.get(`${API_URL}/videos/:id`, () => {
          return new HttpResponse(null, { status: 404 });
        })
      );

      const { result } = renderHook(() => useVideo("nonexistent"), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
    });
  });

  describe("useAddVideo", () => {
    it("should create a new video", async () => {
      const { result } = renderHook(() => useAddVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync({
          url: "https://youtube.com/watch?v=test123",
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data?.video).toBeDefined();
      expect(result.current.data?.cached).toBe(false);
    });

    it("should create video with folder ID", async () => {
      const { result } = renderHook(() => useAddVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync({
          url: "https://youtube.com/watch?v=test123",
          folderId: "folder-1",
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data?.video.folderId).toBe("folder-1");
    });

    it("should handle mutation error", async () => {
      server.use(
        http.post(`${API_URL}/videos`, () => {
          return HttpResponse.json(
            { message: "Invalid URL" },
            { status: 400 }
          );
        })
      );

      const { result } = renderHook(() => useAddVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        try {
          await result.current.mutateAsync({
            url: "invalid-url",
          });
        } catch {
          // Expected error
        }
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
    });
  });

  describe("useDeleteVideo", () => {
    it("should drop the deleted video's detail entry from the cache", async () => {
      const queryClient = createTestQueryClient();
      // Seeded entries have no observer; the default gcTime: 0 collects them on a
      // timer that beats the assertion on a slow runner, so hold them for this test.
      queryClient.setDefaultOptions({ queries: { retry: false, gcTime: Infinity } });
      queryClient.setQueryData(queryKeys.videos.detail("video-1"), { id: "video-1" });
      queryClient.setQueryData(queryKeys.videos.detail("video-2"), { id: "video-2" });
      const wrapper = ({ children }: { children: ReactNode }) =>
        createElement(QueryClientProvider, { client: queryClient }, createElement(BrowserRouter, null, children));
      const { result } = renderHook(() => useDeleteVideo(), { wrapper });

      await act(async () => {
        await result.current.mutateAsync("video-1");
      });

      expect(queryClient.getQueryData(queryKeys.videos.detail("video-1"))).toBeUndefined();
      expect(queryClient.getQueryData(queryKeys.videos.detail("video-2"))).toEqual({ id: "video-2" });
    });

    it("should delete a video", async () => {
      const { result } = renderHook(() => useDeleteVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync("video-1");
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });

    it("should handle delete error", async () => {
      server.use(
        http.delete(`${API_URL}/videos/:id`, () => {
          return HttpResponse.json(
            { message: "Not found" },
            { status: 404 }
          );
        })
      );

      const { result } = renderHook(() => useDeleteVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        try {
          await result.current.mutateAsync("nonexistent");
        } catch {
          // Expected error
        }
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
    });
  });

  describe("useMoveVideo", () => {
    it("should move video to a folder", async () => {
      const { result } = renderHook(() => useMoveVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync({
          id: "video-1",
          folderId: "folder-1",
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });

    it("should move video to root (null folder)", async () => {
      const { result } = renderHook(() => useMoveVideo(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync({
          id: "video-1",
          folderId: null,
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });
  });

  describe("useBulkDeleteVideos", () => {
    it("should delete multiple videos", async () => {
      const { result } = renderHook(() => useBulkDeleteVideos(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync(["video-1", "video-2"]);
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });

    it("should handle partial failure gracefully", async () => {
      let deleteCount = 0;
      server.use(
        http.delete(`${API_URL}/videos/:id`, () => {
          deleteCount++;
          if (deleteCount === 2) {
            return HttpResponse.json(
              { message: "Not found" },
              { status: 404 }
            );
          }
          return new HttpResponse(null, { status: 204 });
        })
      );

      const { result } = renderHook(() => useBulkDeleteVideos(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        try {
          await result.current.mutateAsync(["video-1", "video-2"]);
        } catch {
          // Expected error for partial failure
        }
      });

      // The hook uses Promise.all, so one failure fails the whole batch
      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
    });
  });

  describe("useBulkMoveVideos", () => {
    it("should move multiple videos to a folder", async () => {
      const { result } = renderHook(() => useBulkMoveVideos(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync({
          videoIds: ["video-1", "video-2"],
          folderId: "folder-1",
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });

    it("should move multiple videos to root", async () => {
      const { result } = renderHook(() => useBulkMoveVideos(), {
        wrapper: createWrapper(),
      });

      await act(async () => {
        await result.current.mutateAsync({
          videoIds: ["video-1", "video-2"],
          folderId: null,
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });
  });
});
