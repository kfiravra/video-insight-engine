import { describe, it, expect, beforeEach, vi } from "vitest";

import { LIBRARY_CHAT_SURFACE, useChatStore, type ChatMessage } from "./chat-store";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "hello",
    createdAt: "2026-06-02T00:00:00.000Z",
    ...overrides,
  };
}

describe("chat-store", () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [], status: "idle", sessionIds: {} });
    localStorage.clear();
  });

  describe("setMessages", () => {
    it("should replace messages when given an array", () => {
      const msg = makeMessage();
      useChatStore.getState().setMessages([msg]);
      expect(useChatStore.getState().messages).toEqual([msg]);
    });

    it("should apply a functional updater against the previous messages", () => {
      const first = makeMessage({ id: "m1" });
      const second = makeMessage({ id: "m2", role: "user", content: "hi" });
      useChatStore.getState().setMessages([first]);

      useChatStore.getState().setMessages((prev) => [...prev, second]);

      expect(useChatStore.getState().messages.map((m) => m.id)).toEqual([
        "m1",
        "m2",
      ]);
    });
  });

  describe("setStatus", () => {
    it("should update the status", () => {
      useChatStore.getState().setStatus("streaming");
      expect(useChatStore.getState().status).toBe("streaming");
    });
  });

  describe("clearMessages", () => {
    it("should empty messages and reset status to idle", () => {
      useChatStore.setState({
        messages: [makeMessage()],
        status: "streaming",
      });

      useChatStore.getState().clearMessages();

      expect(useChatStore.getState().messages).toEqual([]);
      expect(useChatStore.getState().status).toBe("idle");
    });
  });

  describe("ensureSessionId", () => {
    it("should mint a uuid on a surface's first use and return it after that", () => {
      const first = useChatStore.getState().ensureSessionId("vid-1");

      expect(first).toMatch(UUID_V4);
      expect(useChatStore.getState().ensureSessionId("vid-1")).toBe(first);
    });

    it("should keep one session per surface", () => {
      const video = useChatStore.getState().ensureSessionId("vid-1");
      const library = useChatStore.getState().ensureSessionId(LIBRARY_CHAT_SURFACE);

      expect(library).not.toBe(video);
      expect(useChatStore.getState().sessionIds).toEqual({
        "vid-1": video,
        [LIBRARY_CHAT_SURFACE]: library,
      });
    });

    it("should fall back to getRandomValues outside a secure context", () => {
      // Plain-http pages have crypto.getRandomValues but no randomUUID.
      const getRandomValues = crypto.getRandomValues.bind(crypto);
      vi.stubGlobal("crypto", { getRandomValues });
      try {
        expect(useChatStore.getState().ensureSessionId("vid-2")).toMatch(UUID_V4);
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it("should drop every surface's session with the transcript", () => {
      const before = useChatStore.getState().ensureSessionId("vid-1");
      useChatStore.getState().ensureSessionId(LIBRARY_CHAT_SURFACE);

      useChatStore.getState().clearMessages();

      expect(useChatStore.getState().sessionIds).toEqual({});
      expect(useChatStore.getState().ensureSessionId("vid-1")).not.toBe(before);
    });
  });

  describe("persistence", () => {
    it("should persist only messages, not status, under the vie-chat key", () => {
      useChatStore.getState().setMessages([makeMessage()]);
      useChatStore.getState().setStatus("streaming");

      const raw = localStorage.getItem("vie-chat");
      expect(raw).not.toBeNull();
      const parsed = JSON.parse(raw as string);
      expect(parsed.state.messages).toHaveLength(1);
      expect(parsed.state.status).toBeUndefined();
    });

    it("should persist the session ids next to the messages", () => {
      const sessionId = useChatStore.getState().ensureSessionId("vid-1");

      const parsed = JSON.parse(localStorage.getItem("vie-chat") as string);
      expect(parsed.version).toBe(1);
      expect(parsed.state.sessionIds).toEqual({ "vid-1": sessionId });
    });

    it("should migrate a v0 transcript to v1 with no session ids", async () => {
      useChatStore.setState({ sessionIds: { stale: "x" } });
      localStorage.setItem(
        "vie-chat",
        JSON.stringify({ state: { messages: [makeMessage({ id: "old" })] }, version: 0 }),
      );

      await useChatStore.persist.rehydrate();

      expect(useChatStore.getState().messages.map((m) => m.id)).toEqual(["old"]);
      expect(useChatStore.getState().sessionIds).toEqual({});
    });
  });

  describe("rehydrate sanitize", () => {
    it("should clear isStreaming on rehydrated messages", () => {
      // Seed storage with a message stuck mid-stream (as a nav-interrupted
      // reload would leave it), then rehydrate from it.
      const stored = {
        state: {
          messages: [makeMessage({ id: "interrupted", isStreaming: true })],
        },
        version: 0,
      };
      localStorage.setItem("vie-chat", JSON.stringify(stored));

      useChatStore.persist.rehydrate();

      const msg = useChatStore
        .getState()
        .messages.find((m) => m.id === "interrupted");
      expect(msg?.isStreaming).toBe(false);
    });
  });
});
