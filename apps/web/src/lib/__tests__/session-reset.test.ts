import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/mocks/server";
import { queryClient } from "@/lib/query-client";
import { resetClientSession } from "@/lib/session-reset";
import { registerChatStream, _getChatStreamCount } from "@/lib/chat-streams";
import { useChatStore } from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import { useProcessingStore } from "@/features/video-output/stores/processing-store";
import {
  subscribeToStream,
  _getRegistrySize,
} from "@/features/video-output/lib/streaming/stream-registry";

const API_URL = "http://localhost:3000/api";

describe("resetClientSession", () => {
  beforeEach(() => {
    queryClient.clear();
  });

  it("drops every cached query", () => {
    queryClient.setQueryData(["videos", "list", { folderId: undefined }], { videos: [1] });
    queryClient.setQueryData(["folders", "list"], [{ id: "f1" }]);

    resetClientSession();

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it("discards the result of a fetch still in flight", async () => {
    let resolve!: (value: unknown) => void;
    const fetching = queryClient.fetchQuery({
      queryKey: ["videos", "list", { folderId: undefined }],
      queryFn: () => new Promise((r) => { resolve = r; }),
    });

    resetClientSession();
    resolve({ videos: ["previous user's video"] });
    await fetching.catch(() => {});

    expect(queryClient.getQueryData(["videos", "list", { folderId: undefined }])).toBeUndefined();
  });

  it("aborts the open pipeline streams", () => {
    server.use(
      http.get(`${API_URL}/videos/:id/stream`, () => new HttpResponse("data: {}\n")),
    );
    subscribeToStream("vs-1", "token", () => {});
    expect(_getRegistrySize()).toBe(1);

    resetClientSession();

    expect(_getRegistrySize()).toBe(0);
  });

  it("aborts the chat turns still streaming", () => {
    const controller = new AbortController();
    registerChatStream(controller);

    resetClientSession();

    expect(controller.signal.aborted).toBe(true);
    expect(_getChatStreamCount()).toBe(0);
  });

  it("clears the processing store and the chat transcript", () => {
    useProcessingStore.getState().setStreamState("vs-1", { phase: "connecting", metadata: null, error: null });
    useChatStore.getState().setMessages([
      { id: "m1", role: "user", content: "hi", createdAt: new Date().toISOString() },
    ]);
    useChatStore.getState().ensureSessionId("library");

    resetClientSession();

    expect(useProcessingStore.getState().streams.size).toBe(0);
    expect(useChatStore.getState().messages).toEqual([]);
    expect(useChatStore.getState().sessionIds).toEqual({});
  });

  it("forgets the user's folders and favourites but keeps layout preferences", () => {
    const ui = useUIStore.getState();
    ui.setSidebarWidth(420);
    ui.setSidebarTextSize("large");
    ui.expandFolder("f1");
    ui.toggleFavorite("v1");
    ui.setSelectedFolder("f1");

    resetClientSession();

    const after = useUIStore.getState();
    expect(after.expandedFolderIds).toEqual([]);
    expect(after.favoriteVideoIds).toEqual([]);
    expect(after.selectedFolderId).toBeNull();
    expect(after.sidebarWidth).toBe(420);
    expect(after.sidebarTextSize).toBe("large");
  });
});
