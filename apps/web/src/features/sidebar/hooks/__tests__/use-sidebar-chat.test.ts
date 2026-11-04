import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CHAT_USAGE_SETTLE_MS, useSidebarChat } from "../use-sidebar-chat";
import { useChatStore } from "@/stores/chat-store";
import { ApiError } from "@/api/client";
import { DirectionProvider } from "@/contexts/DirectionContext";
import { getLabels } from "@/lib/i18n";
import {
  sendAssistantMessage,
  sendLibraryMessage,
  type AssistantChatEvent,
} from "@/lib/assistant";

// Mock the assistant transport so we assert which path the hook dispatches and
// can drive the SSE callback to exercise event handling.
vi.mock("@/lib/assistant", async () => {
  const actual = await vi.importActual<typeof import("@/lib/assistant")>(
    "@/lib/assistant",
  );
  return {
    ...actual,
    sendAssistantMessage: vi.fn(() => Promise.resolve()),
    sendLibraryMessage: vi.fn(() => Promise.resolve()),
  };
});

const mockedSendAssistantMessage = vi.mocked(sendAssistantMessage);
const mockedSendLibraryMessage = vi.mocked(sendLibraryMessage);

// The hook calls useQueryClient(), so every render needs a provider. A fresh
// client per test keeps cache state from bleeding across tests.
function makeWrapper(language?: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(
      QueryClientProvider,
      { client: queryClient },
      language
        ? createElement(
            DirectionProvider,
            { language, isRTL: language === "he" || language === "ar" },
            children,
          )
        : children,
    );
  return { queryClient, wrapper };
}

describe("useSidebarChat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset the persisted store between tests so transcripts never leak.
    useChatStore.setState({
      messages: [],
      status: "idle",
      pendingConfirmation: null,
    });
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("sendMessage without videoSummaryId (library mode)", () => {
    it("should call sendLibraryMessage when no video is open", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("What did I learn this week?");
      });

      expect(mockedSendLibraryMessage).toHaveBeenCalledTimes(1);
      expect(mockedSendLibraryMessage).toHaveBeenCalledWith(
        "What did I learn this week?",
        expect.any(Array),
        expect.any(Function),
        expect.any(AbortSignal),
        undefined, // no confirmToken on a regular turn
      );
    });

    it("should not call the single-video endpoint in library mode", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("hello");
      });

      expect(mockedSendAssistantMessage).not.toHaveBeenCalled();
    });

    it("should not push the placeholder message in library mode", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("hello");
      });

      const placeholder = result.current.messages.find((m) =>
        m.content.includes("Open a video to start chatting"),
      );
      expect(placeholder).toBeUndefined();
    });

    it("should create a streaming assistant message in library mode", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("hello");
      });

      const assistantMsg = result.current.messages.find(
        (m) => m.role === "assistant",
      );
      expect(assistantMsg?.isStreaming).toBe(true);
    });
  });

  describe("sendMessage with videoSummaryId (single-video mode)", () => {
    it("should call sendAssistantMessage when a video is open", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(
        () => useSidebarChat({ videoSummaryId: "vid-123" }),
        { wrapper },
      );

      act(() => {
        result.current.sendMessage("Explain this part");
      });

      expect(mockedSendAssistantMessage).toHaveBeenCalledTimes(1);
      expect(mockedSendAssistantMessage).toHaveBeenCalledWith(
        "vid-123",
        "Explain this part",
        expect.any(Array),
        expect.any(Function),
        expect.any(AbortSignal),
        undefined, // no confirmToken on a regular turn
      );
    });

    it("should not call the library endpoint in single-video mode", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(
        () => useSidebarChat({ videoSummaryId: "vid-123" }),
        { wrapper },
      );

      act(() => {
        result.current.sendMessage("Explain this part");
      });

      expect(mockedSendLibraryMessage).not.toHaveBeenCalled();
    });

    it("should surface tool steps and refresh the tree on a mutating action while a video is open", () => {
      // Regression: a video being open must NOT make the assistant a toolless
      // chatbot — single-video chat now runs library tools too.
      const { queryClient, wrapper } = makeWrapper();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
      const { result } = renderHook(
        () => useSidebarChat({ videoSummaryId: "vid-123" }),
        { wrapper },
      );

      act(() => {
        result.current.sendMessage("organize my collection");
      });

      // The single-video transport receives handleEvent as its 4th arg.
      const handleEvent = mockedSendAssistantMessage.mock.calls[0][3] as (
        event: AssistantChatEvent,
      ) => void;

      act(() => {
        handleEvent({
          type: "tool",
          content: "Organized library: 4 folders, 8 videos moved",
          metadata: { status: "done", action: "organize_library" },
        });
        handleEvent({ type: "done" });
      });

      const assistantMsg = result.current.messages.find(
        (m) => m.role === "assistant",
      );
      expect(assistantMsg?.steps).toEqual([
        "Organized library: 4 folders, 8 videos moved",
      ]);
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["folders", "list"],
      });
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["videos", "list"],
      });
    });
  });

  describe("usage refresh after every turn", () => {
    function runTurn(events: AssistantChatEvent[]) {
      const { queryClient, wrapper } = makeWrapper();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
      const { result } = renderHook(() => useSidebarChat(), { wrapper });
      act(() => {
        result.current.sendMessage("summarize that video");
      });
      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (event: AssistantChatEvent) => void;
      act(() => {
        for (const event of events) handleEvent(event);
      });
      return invalidateSpy;
    }

    function usageRefreshes(invalidateSpy: ReturnType<typeof vi.spyOn>): number {
      return invalidateSpy.mock.calls.filter(([filters]) => {
        const key = (filters as { queryKey?: readonly string[] } | undefined)?.queryKey;
        return key?.[0] === "usage";
      }).length;
    }

    it("should re-read the usage tile after a plain chat reply, which is charged after it lands", () => {
      const invalidateSpy = runTurn([{ type: "text", content: "Here is the answer." }, { type: "done" }]);

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["usage", "me"] });
    });

    it("should re-read the usage tile when the agent starts a video", () => {
      const invalidateSpy = runTurn([
        { type: "tool", content: "Started", metadata: { status: "done", action: "generate_video" } },
        { type: "done" },
      ]);

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["usage", "me"] });
    });

    it("should refresh the usage once per turn, not once per tool call", () => {
      const invalidateSpy = runTurn([
        { type: "tool", content: "A", metadata: { status: "done", action: "generate_video" } },
        { type: "tool", content: "B", metadata: { status: "done", action: "create_folder" } },
        { type: "done" },
      ]);

      expect(usageRefreshes(invalidateSpy)).toBe(1);
    });

    it("should not refresh the usage before the turn is done", () => {
      const invalidateSpy = runTurn([{ type: "text", content: "Still writing" }]);

      expect(usageRefreshes(invalidateSpy)).toBe(0);
    });

    it("should re-read the usage once more after the background charge has had time to land", () => {
      vi.useFakeTimers();
      try {
        const invalidateSpy = runTurn([{ type: "done" }]);
        expect(usageRefreshes(invalidateSpy)).toBe(1);

        act(() => {
          vi.advanceTimersByTime(CHAT_USAGE_SETTLE_MS);
        });

        expect(usageRefreshes(invalidateSpy)).toBe(2);
      } finally {
        vi.useRealTimers();
      }
    });

    it("should drop the delayed re-read once the chat unmounts", () => {
      vi.useFakeTimers();
      try {
        const { queryClient, wrapper } = makeWrapper();
        const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
        const { result, unmount } = renderHook(() => useSidebarChat(), { wrapper });
        act(() => {
          result.current.sendMessage("hi");
        });
        const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (event: AssistantChatEvent) => void;
        act(() => handleEvent({ type: "done" }));

        unmount();
        vi.advanceTimersByTime(CHAT_USAGE_SETTLE_MS);

        expect(usageRefreshes(invalidateSpy)).toBe(1);
      } finally {
        vi.useRealTimers();
      }
    });

    it("should clear cached estimates when the agent starts a video", () => {
      const invalidateSpy = runTurn([
        { type: "tool", content: "Started", metadata: { status: "done", action: "generate_video" } },
        { type: "done" },
      ]);

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["video-estimate"] });
    });
  });

  describe("sendMessage streams every message through the agent", () => {
    it("should stream an action-like phrase as a normal chat turn", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("organize my library");
      });

      // No client-side intent path anymore — it just chats and the backend
      // agent decides whether to call tools.
      expect(mockedSendLibraryMessage).toHaveBeenCalledTimes(1);
    });
  });

  describe("tool events", () => {
    it("should append a step line for a 'tool' done event", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("organize my library");
      });

      // The hook hands handleEvent as the 3rd arg to sendLibraryMessage.
      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;

      act(() => {
        handleEvent({
          type: "tool",
          content: 'Created folder "Series"',
          metadata: { status: "done", action: "create_folder" },
        });
      });

      const assistantMsg = result.current.messages.find(
        (m) => m.role === "assistant",
      );
      expect(assistantMsg?.steps).toEqual(['Created folder "Series"']);
    });

    it("should ignore a 'tool' start event (no step until done)", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("organize my library");
      });

      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;

      act(() => {
        handleEvent({
          type: "tool",
          content: "Organizing library…",
          metadata: { status: "start", action: "organize_library" },
        });
      });

      const assistantMsg = result.current.messages.find(
        (m) => m.role === "assistant",
      );
      expect(assistantMsg?.steps).toBeUndefined();
    });
  });

  describe("source events carry numeric seconds for seek/deep-link", () => {
    // The wire field is snake_case (Python RAGSource.timestamp_seconds); the
    // shared AssistantSource type doesn't declare it yet, hence the cast.
    type WireSource = AssistantChatEvent["sources"] extends
      | (infer S)[]
      | undefined
      ? S & { timestamp_seconds?: number | null }
      : never;

    function driveSourceEvent(sources: WireSource[]) {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });
      act(() => {
        result.current.sendMessage("where is this covered?");
      });
      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;
      act(() => {
        handleEvent({ type: "source", sources });
      });
      return result;
    }

    it("should map timestamp_seconds to timestampSeconds (floored)", () => {
      const result = driveSourceEvent([
        {
          text: "Attention is all you need.",
          score: 0.9,
          chunk_index: 0,
          video_id: "vid1",
          title: "Transformers Explained",
          timestamp: "12:34",
          timestamp_seconds: 754.6,
        },
      ]);

      const assistantMsg = result.current.messages.find(
        (m) => m.role === "assistant",
      );
      expect(assistantMsg?.sources).toEqual([
        {
          title: "Transformers Explained",
          youtubeId: "vid1",
          timestamp: "12:34",
          timestampSeconds: 754,
        },
      ]);
    });

    it("should leave timestampSeconds undefined for v1-legacy null seconds", () => {
      const result = driveSourceEvent([
        {
          text: "Old chunk without a timeline.",
          score: 0.8,
          chunk_index: 0,
          video_id: "vid2",
          title: "Legacy Video",
          timestamp_seconds: null,
        },
      ]);

      const assistantMsg = result.current.messages.find(
        (m) => m.role === "assistant",
      );
      expect(assistantMsg?.sources?.[0].timestampSeconds).toBeUndefined();
    });
  });

  describe("persistence across remount", () => {
    it("should keep messages after the hook unmounts and re-renders", () => {
      const { wrapper } = makeWrapper();
      const first = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        first.result.current.sendMessage("remember me");
      });
      expect(first.result.current.messages.length).toBeGreaterThan(0);

      // Simulate the Sidebar remounting on navigation.
      first.unmount();

      const second = renderHook(() => useSidebarChat(), { wrapper });
      const userMsg = second.result.current.messages.find(
        (m) => m.role === "user",
      );
      expect(userMsg?.content).toBe("remember me");
    });
  });

  describe("live UI refresh on mutating agent actions", () => {
    it("should invalidate folder + video lists after a mutating tool then done", () => {
      const { queryClient, wrapper } = makeWrapper();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("move my videos");
      });

      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;

      act(() => {
        handleEvent({
          type: "tool",
          content: "Moved 3 videos",
          metadata: { status: "done", action: "move_video" },
        });
        handleEvent({ type: "done" });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["folders", "list"],
      });
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["videos", "list"],
      });
    });

    it("should NOT invalidate the library trees when no mutating tool ran", () => {
      const { queryClient, wrapper } = makeWrapper();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("what did I learn?");
      });

      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;

      act(() => {
        handleEvent({ type: "text", content: "You learned a lot." });
        handleEvent({ type: "done" });
      });

      expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["folders", "list"] });
      expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ["videos", "list"] });
    });
  });

  describe("pending confirmation flow", () => {
    /** Drive a turn to the point where the server parks a gated action and
     * emits `pending_confirmation`; returns the hook handle. */
    function primePendingConfirmation(
      result: { current: ReturnType<typeof useSidebarChat> },
      token = "tok-abc123456789",
    ) {
      act(() => {
        result.current.sendMessage("delete the Old folder and its videos");
      });
      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;
      act(() => {
        handleEvent({
          type: "tool",
          content: "Waiting for your confirmation…",
          metadata: {
            status: "pending_confirmation",
            action: "delete_folder",
            confirmation: {
              token,
              action: "delete_folder",
              summary: "Delete this folder AND every video inside it",
            },
          },
        });
        handleEvent({ type: "done" });
      });
    }

    it("should set pendingConfirmation from a pending_confirmation tool event", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      primePendingConfirmation(result);

      expect(result.current.pendingConfirmation).toEqual({
        token: "tok-abc123456789",
        action: "delete_folder",
        summary: "Delete this folder AND every video inside it",
      });
    });

    it("should ignore a malformed confirmation payload (no token)", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("delete stuff");
      });
      const handleEvent = mockedSendLibraryMessage.mock.calls[0][2] as (
        event: AssistantChatEvent,
      ) => void;
      act(() => {
        handleEvent({
          type: "tool",
          metadata: {
            status: "pending_confirmation",
            confirmation: { action: "delete_folder" },
          },
        });
      });

      expect(result.current.pendingConfirmation).toBeNull();
    });

    it("should resend with the token and clear the pending state on confirm", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });
      primePendingConfirmation(result);

      act(() => {
        result.current.confirmPendingAction();
      });

      expect(result.current.pendingConfirmation).toBeNull();
      expect(mockedSendLibraryMessage).toHaveBeenCalledTimes(2);
      const lastCall = mockedSendLibraryMessage.mock.calls.at(-1)!;
      expect(lastCall[4]).toBe("tok-abc123456789");
      // The confirmation is echoed into the transcript as a user turn.
      const userMessages = result.current.messages.filter(
        (m) => m.role === "user",
      );
      expect(userMessages.at(-1)?.content).toBe("Yes, do it");
    });

    it("should clear the pending state without resending on cancel", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });
      primePendingConfirmation(result);

      act(() => {
        result.current.cancelPendingAction();
      });

      expect(result.current.pendingConfirmation).toBeNull();
      // Only the original turn hit the transport — the token is never sent.
      expect(mockedSendLibraryMessage).toHaveBeenCalledTimes(1);
      const lastMsg = result.current.messages.at(-1);
      expect(lastMsg?.role).toBe("assistant");
      expect(lastMsg?.content).toBe("Okay, cancelled.");
    });

    it("should abandon a pending confirmation when a new message is sent", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });
      primePendingConfirmation(result);

      act(() => {
        result.current.sendMessage("actually, tell me a joke instead");
      });

      expect(result.current.pendingConfirmation).toBeNull();
      // The fresh turn never carries the token.
      const lastCall = mockedSendLibraryMessage.mock.calls.at(-1)!;
      expect(lastCall[4]).toBeUndefined();
    });

    it("should do nothing on confirm when no confirmation is pending", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.confirmPendingAction();
      });

      expect(mockedSendLibraryMessage).not.toHaveBeenCalled();
      expect(mockedSendAssistantMessage).not.toHaveBeenCalled();
      expect(result.current.messages).toEqual([]);
    });
  });

  describe("request failures", () => {
    async function sendAndGetAssistantReply(
      options: Parameters<typeof useSidebarChat>[0],
      language?: string,
    ): Promise<string | undefined> {
      const { wrapper } = makeWrapper(language);
      const { result } = renderHook(() => useSidebarChat(options), { wrapper });

      await act(async () => {
        result.current.sendMessage("hello");
      });

      return result.current.messages.find((m) => m.role === "assistant")?.content;
    }

    it("should tell the user to slow down when the single-video chat is rate limited", async () => {
      mockedSendAssistantMessage.mockRejectedValueOnce(
        new ApiError(429, "Too many messages", "RATE_LIMITED"),
      );

      const reply = await sendAndGetAssistantReply({ videoSummaryId: "vid-123" });

      expect(reply).toBe("Too many messages. Please wait a moment and try again.");
    });

    it("should tell the user to slow down when the library chat is rate limited", async () => {
      mockedSendLibraryMessage.mockRejectedValueOnce(
        new ApiError(429, "Too many messages", "RATE_LIMITED"),
      );

      const reply = await sendAndGetAssistantReply({});

      expect(reply).toBe("Too many messages. Please wait a moment and try again.");
    });

    describe("billing refusals before the turn starts (402 / 403)", () => {
      function refuseWith(error: ApiError): void {
        mockedSendAssistantMessage.mockRejectedValueOnce(error);
        mockedSendLibraryMessage.mockRejectedValueOnce(error);
      }

      const outOfTokens = (available: number) =>
        new ApiError(402, "x", "INSUFFICIENT_TOKENS", { error: "INSUFFICIENT_TOKENS", needed: 1, available });

      it.each([
        ["single-video", { videoSummaryId: "vid-123" }],
        ["library", {}],
      ])("should say the tokens ran out when the %s chat is refused with a zero balance", async (_name, options) => {
        refuseWith(outOfTokens(0));

        const reply = await sendAndGetAssistantReply(options);

        expect(reply).toBe("You're out of tokens — add tokens to keep chatting.");
      });

      it("should name the shortfall when the balance is below zero", async () => {
        refuseWith(outOfTokens(-75));

        const reply = await sendAndGetAssistantReply({});

        expect(reply).toBe("You're 75 tokens short — add tokens to continue.");
      });

      it("should say chats are off for a blocked account (403)", async () => {
        refuseWith(new ApiError(403, "x", "ACCOUNT_BLOCKED"));

        const reply = await sendAndGetAssistantReply({});

        expect(reply).toBe("This account can't start new chats right now.");
      });

      it("should mark the refused reply so the sidebar links it to the billing page", async () => {
        refuseWith(outOfTokens(0));

        await sendAndGetAssistantReply({});

        expect(useChatStore.getState().messages.find((m) => m.role === "assistant")?.billingLink).toBe(true);
      });

      it("should re-read the usage tile, whose balance the refusal shows is stale", async () => {
        refuseWith(outOfTokens(0));
        const { queryClient, wrapper } = makeWrapper();
        const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
        const { result } = renderHook(() => useSidebarChat(), { wrapper });

        await act(async () => {
          result.current.sendMessage("hello");
        });

        expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["usage", "me"] });
      });

      it("should read the wording from the billing table of the surrounding language", async () => {
        refuseWith(outOfTokens(0));

        const reply = await sendAndGetAssistantReply({}, "ar");

        expect(reply).toBe(getLabels("ar").billing.chatInsufficient);
      });

      it("should put the chat into the error state", async () => {
        refuseWith(outOfTokens(0));

        await sendAndGetAssistantReply({});

        expect(useChatStore.getState().status).toBe("error");
      });

      it("should not mark a rate-limited reply as a billing refusal", async () => {
        mockedSendLibraryMessage.mockRejectedValueOnce(new ApiError(429, "Too many messages", "RATE_LIMITED"));

        await sendAndGetAssistantReply({});

        expect(useChatStore.getState().messages.find((m) => m.role === "assistant")?.billingLink).toBeUndefined();
      });

      it("should treat the retired CHAT_LIMIT_REACHED code as a plain rate limit", async () => {
        mockedSendLibraryMessage.mockRejectedValueOnce(new ApiError(429, "Chat limit reached", "CHAT_LIMIT_REACHED"));

        const reply = await sendAndGetAssistantReply({});

        expect(reply).toBe("Too many messages. Please wait a moment and try again.");
      });
    });

    it("should keep the connection-failure copy for any other failure", async () => {
      mockedSendAssistantMessage.mockRejectedValueOnce(
        new Error("Assistant chat failed: 503"),
      );

      const reply = await sendAndGetAssistantReply({ videoSummaryId: "vid-123" });

      expect(reply).toBe("Failed to connect to assistant. Please try again.");
    });

    it("should put the chat into the error state on a rate-limited request", async () => {
      mockedSendAssistantMessage.mockRejectedValueOnce(
        new ApiError(429, "Too many messages", "RATE_LIMITED"),
      );

      await sendAndGetAssistantReply({ videoSummaryId: "vid-123" });

      expect(useChatStore.getState().status).toBe("error");
    });
  });

  describe("clearMessages", () => {
    it("should empty the transcript", () => {
      const { wrapper } = makeWrapper();
      const { result } = renderHook(() => useSidebarChat(), { wrapper });

      act(() => {
        result.current.sendMessage("hello");
      });
      expect(result.current.messages.length).toBeGreaterThan(0);

      act(() => {
        result.current.clearMessages();
      });
      expect(result.current.messages).toEqual([]);
    });
  });
});
