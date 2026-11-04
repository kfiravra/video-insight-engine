/**
 * Shared stream registry — one SSE fetch per videoSummaryId, multiple subscribers.
 *
 * The backend deduplicates pipeline runs via Redis Streams; this registry
 * does the symmetric work on the frontend so a page-level hook and the
 * app-level processing manager can both observe the same stream without
 * each opening their own HTTP connection.
 *
 * Late subscribers replay buffered history immediately, then receive live
 * events. Streams stay open until the producer signals done/error — they
 * are NOT torn down when the last subscriber disconnects, so a brief
 * unmount/remount (StrictMode, route transition) doesn't drop events.
 */

import { refreshToken, getAccessToken } from "@/api/client";
import { useAuthStore } from "@/stores/auth-store";
import { sseLogger } from "@/features/video-output/lib/streaming/sse-logger";
import { incrementTelemetryCounter } from "@/features/video-output/lib/telemetry";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3000/api";

export type StreamEvent = Record<string, unknown>;
export type StreamListener = (event: StreamEvent) => void;

interface StreamEntry {
  abortController: AbortController;
  listeners: Set<StreamListener>;
  history: StreamEvent[];
  done: boolean;
  /** Set to true once the fetch is in flight to avoid double-starts on race. */
  starting: boolean;
}

const streams = new Map<string, StreamEntry>();

// Hold finished streams briefly so a late mount can still replay history,
// then evict to avoid unbounded growth.
const FINISHED_RETENTION_MS = 5_000;

// Mirrors the backend Redis Streams MAXLEN. A stuck or pathological pipeline
// can emit many events; without a cap, history would pin memory until the
// user navigates away. Trimming preserves the latest events, which is what
// late-joining subscribers care about.
const MAX_HISTORY = 2000;

function broadcast(entry: StreamEntry, event: StreamEvent): void {
  entry.history.push(event);
  if (entry.history.length > MAX_HISTORY) {
    entry.history.splice(0, entry.history.length - MAX_HISTORY);
  }
  for (const listener of entry.listeners) {
    try {
      listener(event);
    } catch (err) {
      sseLogger.warn("Stream listener threw:", err instanceof Error ? err.message : String(err));
    }
  }
}

async function readSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  entry: StreamEntry,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6);
      if (data === "[DONE]") continue;
      try {
        const event = JSON.parse(data) as StreamEvent;
        broadcast(entry, event);
      } catch (err) {
        // Counter is prod-visible (window.__vieTelemetry) even though the log
        // line stays dev-only — malformed SSE frames must be observable in prod.
        incrementTelemetryCounter("sse_parse_failed");
        if (import.meta.env.DEV) {
          sseLogger.warn("Failed to parse SSE event:", err instanceof Error ? err.message : String(err));
        }
      }
    }
  }
}

/**
 * The error event for a stream the gateway refused. The `{ error, ... }`
 * envelope's code and fields ride along, so a billing refusal (402
 * INSUFFICIENT_TOKENS, 403 ACCOUNT_BLOCKED) reaches the error card as itself
 * instead of a bare "HTTP 402". The message stays the status line: the card
 * words a known code itself and never shows the server's text.
 */
async function refusalEvent(response: Response): Promise<StreamEvent> {
  const event: StreamEvent = { event: "error", message: `HTTP ${response.status}: ${response.statusText}` };
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    // Not JSON (a proxy's HTML error page, an empty body): the status line is all there is.
    return event;
  }
  if (typeof body !== "object" || body === null) return event;
  const { error: code, ...fields } = body as Record<string, unknown>;
  return typeof code === "string" ? { ...fields, ...event, code } : event;
}

async function attemptTokenRefresh(): Promise<string | null> {
  const refreshed = await refreshToken();
  return refreshed ? getAccessToken() : null;
}

async function startFetch(
  videoSummaryId: string,
  token: string,
  entry: StreamEntry,
  refreshed: boolean = false,
): Promise<void> {
  try {
    const response = await fetch(`${API_URL}/videos/${videoSummaryId}/stream`, {
      headers: {
        Accept: "text/event-stream",
        Authorization: `Bearer ${token}`,
      },
      signal: entry.abortController.signal,
    });

    if (response.status === 401 && !refreshed) {
      const newToken = await attemptTokenRefresh();
      if (newToken) {
        return startFetch(videoSummaryId, newToken, entry, true);
      }
      useAuthStore.getState().forceLogout("Session expired. Please log in again.");
      broadcast(entry, { event: "error", message: "Session expired. Please log in again." });
      return;
    }

    if (!response.ok) {
      broadcast(entry, await refusalEvent(response));
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      broadcast(entry, { event: "error", message: "No response body" });
      return;
    }

    await readSSE(reader, entry);
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") return;
    broadcast(entry, {
      event: "error",
      message: err instanceof Error ? err.message : "Stream failed",
    });
  } finally {
    entry.done = true;
    setTimeout(() => {
      if (streams.get(videoSummaryId) === entry && entry.listeners.size === 0) {
        streams.delete(videoSummaryId);
      }
    }, FINISHED_RETENTION_MS);
  }
}

/**
 * Subscribe to the SSE stream for a video. The first call kicks off a fetch;
 * subsequent calls attach to the same fetch and immediately replay history.
 *
 * Returns an unsubscribe function. The fetch keeps running even after every
 * subscriber leaves — it's only torn down on natural completion (done/error).
 */
export function subscribeToStream(
  videoSummaryId: string,
  token: string,
  listener: StreamListener,
): () => void {
  let entry = streams.get(videoSummaryId);
  if (!entry) {
    entry = {
      abortController: new AbortController(),
      listeners: new Set([listener]),
      history: [],
      done: false,
      starting: true,
    };
    streams.set(videoSummaryId, entry);
    const newEntry = entry;  // narrow for closures — TS widens `entry` back to `StreamEntry | undefined`
    void startFetch(videoSummaryId, token, newEntry)
      .catch((err) => {
        // startFetch already broadcasts errors from inside its try; this catch
        // is a safety net for sync throws before the try (e.g. malformed URL)
        // and prevents an unhandled-rejection warning in browsers / test runs.
        sseLogger.warn(
          "startFetch crashed before its handler:",
          err instanceof Error ? err.message : String(err),
        );
        broadcast(newEntry, { event: "error", message: "Stream initialization failed" });
      })
      .finally(() => {
        newEntry.starting = false;
      });
  } else {
    for (const event of entry.history) {
      try {
        listener(event);
      } catch (err) {
        sseLogger.warn("Stream listener threw during replay:", err instanceof Error ? err.message : String(err));
      }
    }
    entry.listeners.add(listener);
  }

  return () => {
    const current = streams.get(videoSummaryId);
    if (!current) return;
    current.listeners.delete(listener);
    if (current.done && current.listeners.size === 0) {
      streams.delete(videoSummaryId);
    }
  };
}

/**
 * Force-abort the active stream for a video. Used by sign-out and explicit
 * cancellations — under normal operation, subscribers just unsubscribe and
 * the stream completes on its own.
 */
export function abortStream(videoSummaryId: string): void {
  const entry = streams.get(videoSummaryId);
  if (!entry) return;
  entry.abortController.abort();
  streams.delete(videoSummaryId);
}

/** Tear down every active stream. Called on logout. */
export function abortAllStreams(): void {
  for (const [, entry] of streams) {
    entry.abortController.abort();
  }
  streams.clear();
}

/** Test-only: inspect the registry. */
export function _getRegistrySize(): number {
  return streams.size;
}
