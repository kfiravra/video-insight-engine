import { describe, it, expect, vi } from "vitest";
import type { ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createTestQueryClient } from "../../test/test-utils";
import { server } from "../../test/mocks/server";
import { usePlaylistImport } from "../use-playlists";

const API_URL = "http://localhost:3000/api";

describe("usePlaylistImport", () => {
  it("should clear the cached estimates and re-read the balance once the import lands", async () => {
    server.use(
      http.post(`${API_URL}/playlists/import`, () =>
        HttpResponse.json({ playlist: { id: "PL1" }, folder: null, videos: [], totalVideos: 0, cachedCount: 0, processingCount: 0, failedCount: 0 }),
      ),
    );
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => usePlaylistImport(), { wrapper });

    await act(() => result.current.mutateAsync({ url: "https://www.youtube.com/playlist?list=PL1" }));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["video-estimate"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["usage", "me"] });
  });
});
