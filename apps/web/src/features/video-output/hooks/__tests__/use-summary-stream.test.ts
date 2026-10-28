import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/test/mocks/server";
import { useSummaryStream, type StreamPhase } from "../use-summary-stream";
import { useAuthStore } from "@/stores/auth-store";

const API_URL = "http://localhost:3000/api";

// Helper to create SSE stream responses
function createSSEResponse(events: Array<{ event: string; data: unknown }>) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const { data } of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));
      }
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      controller.close();
    },
  });

  return new HttpResponse(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}

// Helper to create delayed SSE stream
function createDelayedSSEResponse(
  events: Array<{ event: string; data: unknown }>,
  delayMs = 50
) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      for (const { data } of events) {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));
      }
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      controller.close();
    },
  });

  return new HttpResponse(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}

describe("useSummaryStream", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Set up auth state with a valid token
    useAuthStore.setState({
      accessToken: "test-token",
      isAuthenticated: true,
      user: { id: "user-1", email: "test@test.com", name: "Test" },
    });
  });

  afterEach(() => {
    // Clean up auth state
    useAuthStore.setState({
      accessToken: null,
      isAuthenticated: false,
      user: null,
    });
  });

  describe("SSE connection", () => {
    it("should connect to SSE endpoint when enabled", async () => {
      const events = [
        { event: "phase", data: { event: "phase", phase: "metadata" } },
        {
          event: "metadata",
          data: {
            event: "metadata",
            title: "Test Video",
            channel: "Test Channel",
            duration: 600,
          },
        },
        {
          event: "done",
          data: { event: "done", processingTimeMs: 5000 },
        },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      // Should start connecting
      expect(result.current.phase).toBe("connecting");

      await waitFor(() => {
        expect(result.current.phase).toBe("done");
      });
    });

    it("should not connect when disabled", async () => {
      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: false,
        })
      );

      // Should stay idle
      expect(result.current.phase).toBe("idle");

      // Wait a bit to ensure no connection attempt
      await new Promise((resolve) => setTimeout(resolve, 100));
      expect(result.current.phase).toBe("idle");
    });

    it("should not connect without videoSummaryId", async () => {
      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "",
          enabled: true,
        })
      );

      expect(result.current.phase).toBe("idle");
    });

    it("should not connect without access token", async () => {
      useAuthStore.setState({
        accessToken: null,
        isAuthenticated: false,
      });

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      expect(result.current.phase).toBe("idle");
    });
  });

  describe("event parsing", () => {
    it("should parse metadata event", async () => {
      const events = [
        {
          event: "metadata",
          data: {
            event: "metadata",
            title: "Test Video",
            channel: "Test Channel",
            thumbnailUrl: "https://example.com/thumb.jpg",
            duration: 600,
          },
        },
        { event: "done", data: { event: "done", processingTimeMs: 1000 } },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      await waitFor(() => {
        expect(result.current.metadata?.title).toBe("Test Video");
      });

      expect(result.current.metadata?.channel).toBe("Test Channel");
      expect(result.current.metadata?.thumbnailUrl).toBe(
        "https://example.com/thumb.jpg"
      );
      expect(result.current.duration).toBe(600);
    });

    it("should parse pipeline triage and extraction events", async () => {
      const events = [
        {
          event: "triage_complete",
          data: {
            event: "triage_complete",
            contentTags: ["food"],
            modifiers: [],
            primaryTag: "food",
            confidence: 0.95,
            userGoal: "Learn to cook",
            tabs: [
              { id: "ingredients", label: "Ingredients", emoji: "🧅", dataSource: "food" },
              { id: "steps", label: "Steps", emoji: "👨‍🍳", dataSource: "food" },
            ],
          },
        },
        {
          event: "extraction_complete",
          data: {
            event: "extraction_complete",
            data: { food: { meta: {}, ingredients: [], steps: [], tips: [], substitutions: [], nutrition: [], equipment: [] } },
          },
        },
        {
          event: "synthesis_complete",
          data: {
            event: "synthesis_complete",
            tldr: "A cooking tutorial",
            keyTakeaways: ["Use fresh ingredients"],
            masterSummary: "Full summary",
            seoDescription: "SEO text",
          },
        },
        { event: "done", data: { event: "done", processingTimeMs: 3000 } },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      await waitFor(() => {
        expect(result.current.phase).toBe("done");
      });

      expect(result.current.triage?.primaryTag).toBe("food");
      expect(result.current.domainData).toBeDefined();
      expect(result.current.synthesis?.tldr).toBe("A cooking tutorial");
    });

    it("should handle error events", async () => {
      const events = [
        {
          event: "error",
          data: {
            event: "error",
            message: "Processing failed",
            code: "LLM_ERROR",
          },
        },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      await waitFor(() => {
        expect(result.current.phase).toBe("error");
      });

      // Should show user-friendly error message
      expect(result.current.error).toBeDefined();
    });

    it("should map the stream of a failed video to Retry copy and keep its code", async () => {
      const events = [
        {
          event: "error",
          data: {
            event: "error",
            message: "KeyError: 'tabs' in extract_batch",
            code: "VIDEO_FAILED",
          },
        },
      ];
      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({ videoSummaryId: "video-failed", enabled: true })
      );

      await waitFor(() => {
        expect(result.current.phase).toBe("error");
      });
      expect(result.current.errorCode).toBe("VIDEO_FAILED");
      expect(result.current.error).toBe("This video didn't finish processing. Retry starts it again as a new run.");
    });

    it("should keep no error code when the server sent none", async () => {
      const events = [{ event: "error", data: { event: "error", message: "Internal error" } }];
      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({ videoSummaryId: "video-nocode", enabled: true })
      );

      await waitFor(() => {
        expect(result.current.phase).toBe("error");
      });
      expect(result.current.errorCode).toBeNull();
    });

    it("should track phase changes", async () => {
      const phases: StreamPhase[] = [];
      const events = [
        { event: "phase", data: { event: "phase", phase: "metadata" } },
        { event: "done", data: { event: "done", processingTimeMs: 5000 } },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () =>
          createDelayedSSEResponse(events, 10)
        )
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      // Track phases during processing
      const checkPhase = setInterval(() => {
        if (
          result.current.phase !== "idle" &&
          result.current.phase !== "connecting"
        ) {
          if (phases[phases.length - 1] !== result.current.phase) {
            phases.push(result.current.phase);
          }
        }
      }, 5);

      await waitFor(
        () => {
          expect(result.current.phase).toBe("done");
        },
        { timeout: 2000 }
      );

      clearInterval(checkPhase);
      expect(phases).toContain("done");
    });
  });

  describe("cleanup on unmount", () => {
    it("should abort connection on unmount", async () => {
      // Create a slow stream that won't complete before unmount
      const events = [
        { event: "phase", data: { event: "phase", phase: "metadata" } },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () =>
          createDelayedSSEResponse(events, 1000)
        )
      );

      const { result, unmount } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      // Wait for connection to start
      await waitFor(() => {
        expect(result.current.phase).toBe("connecting");
      });

      // Unmount should not throw
      unmount();
    });
  });

  describe("stop functionality", () => {
    it("should cancel stream when stop is called", async () => {
      const events = [
        { event: "phase", data: { event: "phase", phase: "metadata" } },
        {
          event: "metadata",
          data: { event: "metadata", title: "Test", duration: 600 },
        },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () =>
          createDelayedSSEResponse(events, 500)
        )
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      // Wait for connection
      await waitFor(() => {
        expect(result.current.phase).toBe("connecting");
      });

      // Call stop
      act(() => {
        result.current.stop();
      });

      // Should be cancelled
      expect(result.current.phase).toBe("cancelled");
      expect(result.current.error).toBe("Summarization cancelled by user");
    });
  });

  describe("retry functionality", () => {
    it("should retry connection on retry call", async () => {
      let callCount = 0;

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => {
          callCount++;
          if (callCount === 1) {
            return HttpResponse.json(
              { message: "Server error" },
              { status: 500 }
            );
          }
          return createSSEResponse([
            { event: "done", data: { event: "done", processingTimeMs: 100 } },
          ]);
        })
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      // Wait for error
      await waitFor(() => {
        expect(result.current.phase).toBe("error");
      });

      // Retry
      act(() => {
        result.current.retry();
      });

      // Should reconnect and succeed
      await waitFor(() => {
        expect(result.current.phase).toBe("done");
      });

      expect(callCount).toBe(2);
    });
  });

  describe("callbacks", () => {
    it("should call onComplete when stream finishes", async () => {
      const onComplete = vi.fn();
      const events = [
        {
          event: "metadata",
          data: { event: "metadata", title: "Test", duration: 300 },
        },
        { event: "done", data: { event: "done", processingTimeMs: 1000 } },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
          onComplete,
        })
      );

      await waitFor(() => {
        expect(onComplete).toHaveBeenCalled();
      });

      expect(onComplete).toHaveBeenCalledWith(
        expect.objectContaining({
          phase: expect.any(String),
          metadata: expect.objectContaining({ title: "Test" }),
        })
      );
    });

    it("should call onError when stream errors", async () => {
      const onError = vi.fn();

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => {
          return HttpResponse.json(
            { message: "Internal error" },
            { status: 500 }
          );
        })
      );

      renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
          onError,
        })
      );

      await waitFor(() => {
        expect(onError).toHaveBeenCalled();
      });

      expect(onError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  describe("cached response", () => {
    it("should mark response as cached when cached event received", async () => {
      const events = [
        { event: "cached", data: { event: "cached" } },
        {
          event: "metadata",
          data: { event: "metadata", title: "Cached Video", duration: 300 },
        },
        { event: "done", data: { event: "done", processingTimeMs: 50 } },
      ];

      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(events))
      );

      const { result } = renderHook(() =>
        useSummaryStream({
          videoSummaryId: "video-1",
          enabled: true,
        })
      );

      await waitFor(() => {
        expect(result.current.isCached).toBe(true);
      });
    });
  });

  describe("video navigation reset", () => {
    // A populated stream (metadata + one tab) that finishes. Used to seed the
    // hook with the "previous video" before navigating away.
    const firstVideoEvents = [
      {
        event: "metadata",
        data: { event: "metadata", title: "First Video", channel: "Chan A", duration: 120 },
      },
      {
        event: "tab_ready",
        data: {
          event: "tab_ready",
          id: "steps",
          label: "Steps",
          emoji: "👣",
          component: "step_player",
          props: {},
        },
      },
      { event: "done", data: { event: "done", processingTimeMs: 1000 } },
    ];

    it("should clear streamed tabs and metadata when navigating to a different video", async () => {
      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(firstVideoEvents))
      );

      const { result, rerender } = renderHook(
        (props: { videoSummaryId: string; enabled: boolean }) => useSummaryStream(props),
        { initialProps: { videoSummaryId: "video-1", enabled: true } }
      );

      // First video streams to completion with content populated.
      await waitFor(() => {
        expect(result.current.phase).toBe("done");
      });
      expect(result.current.metadata?.title).toBe("First Video");
      expect(result.current.tabs).toHaveLength(1);

      // Navigate to a different, already-completed video (enabled=false, so the
      // subscribe path never runs). The previous video's tabs/metadata must NOT
      // bleed through — they would otherwise win in VideoDetailPage's
      // resolvedTabs / mergedVideo.title precedence.
      act(() => {
        rerender({ videoSummaryId: "video-2", enabled: false });
      });

      await waitFor(() => {
        expect(result.current.tabs).toEqual([]);
      });
      expect(result.current.metadata).toBeNull();
    });

    it("should retain finished content when status flips but the video is unchanged", async () => {
      server.use(
        http.get(`${API_URL}/videos/:id/stream`, () => createSSEResponse(firstVideoEvents))
      );

      const { result, rerender } = renderHook(
        (props: { videoSummaryId: string; enabled: boolean }) => useSummaryStream(props),
        { initialProps: { videoSummaryId: "video-1", enabled: true } }
      );

      await waitFor(() => {
        expect(result.current.phase).toBe("done");
      });
      expect(result.current.tabs).toHaveLength(1);

      // Same video, stream just finished so the page disables streaming. The
      // id is unchanged, so the reset must NOT fire — the video the user is
      // still viewing keeps its content.
      act(() => {
        rerender({ videoSummaryId: "video-1", enabled: false });
      });

      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(result.current.tabs).toHaveLength(1);
      expect(result.current.metadata?.title).toBe("First Video");
    });
  });
});
