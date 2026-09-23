import { useEffect, useRef, useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { queryKeys } from "@/lib/query-keys";
import { buildWebSocketUrl } from "@/lib/websocket-url";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:3000";

// Issue #17: Debug logging option for WebSocket events
const DEBUG_WS = import.meta.env.DEV && import.meta.env.VITE_DEBUG_WS === "true";

function debugLog(...args: unknown[]) {
  if (DEBUG_WS) console.debug("[WebSocket]", ...args);
}

/** Connection states for WebSocket */
export type ConnectionState = "connecting" | "connected" | "disconnected";

/** Maximum reconnection delay in milliseconds */
const MAX_RECONNECT_DELAY = 30000;

/** Initial reconnection delay in milliseconds */
const INITIAL_RECONNECT_DELAY = 1000;

interface VideoStatusEvent {
  type: "video.status";
  payload: {
    videoSummaryId: string;
    userVideoId?: string;
    status: "pending" | "processing" | "completed" | "failed";
    progress?: number;
    message?: string;
    error?: string | null;
  };
}

interface VideoMetadataEvent {
  type: "video.metadata";
  payload: {
    videoSummaryId: string;
    title: string;
    channel?: string;
    thumbnailUrl?: string;
    duration?: number;
  };
}

type WebSocketEvent = VideoStatusEvent | VideoMetadataEvent;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const [connectionState, setConnectionState] = useState<ConnectionState>("disconnected");
  const queryClient = useQueryClient();
  const accessToken = useAuthStore((s) => s.accessToken);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  // Store latest values in refs to avoid circular dependencies
  const accessTokenRef = useRef(accessToken);
  const isAuthenticatedRef = useRef(isAuthenticated);
  accessTokenRef.current = accessToken;
  isAuthenticatedRef.current = isAuthenticated;

  /**
   * Calculate reconnection delay with exponential backoff.
   * Delay doubles each attempt: 1s, 2s, 4s, 8s, 16s, max 30s
   */
  const getReconnectDelay = useCallback(() => {
    const delay = INITIAL_RECONNECT_DELAY * Math.pow(2, reconnectAttemptsRef.current);
    return Math.min(delay, MAX_RECONNECT_DELAY);
  }, []);

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as WebSocketEvent;
        debugLog("Message received", data.type);

        if (data.type === "video.status") {
          // Invalidate video queries to refetch fresh data
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });

          // If we have a userVideoId, also invalidate that specific video
          if (data.payload.userVideoId) {
            queryClient.invalidateQueries({
              queryKey: queryKeys.videos.detail(data.payload.userVideoId),
            });
          }
        }

        if (data.type === "video.metadata") {
          // Metadata received - invalidate video list to update sidebar titles
          debugLog("Metadata received for", data.payload.videoSummaryId, data.payload.title);
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
        }
      } catch {
        // Ignore non-JSON messages like "connected"
      }
    },
    [queryClient]
  );

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    // Use refs to get latest values without recreating this callback
    const token = accessTokenRef.current;
    const authenticated = isAuthenticatedRef.current;

    if (!token || !authenticated) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setConnectionState("connecting");
    // Token travels in the Sec-WebSocket-Protocol header (not the query string,
    // which leaks into access logs). Server validates it and selects "vie-auth".
    const ws = new WebSocket(buildWebSocketUrl(WS_URL), ["vie-auth", token]);

    ws.onopen = () => {
      debugLog("Connected");
      setConnectionState("connected");
      reconnectAttemptsRef.current = 0; // Reset on successful connection
    };

    ws.onmessage = handleMessage;

    ws.onclose = (event) => {
      debugLog("Disconnected", { code: event.code, reason: event.reason });
      wsRef.current = null;
      setConnectionState("disconnected");

      // Code 4001 = auth failure - force logout and redirect to login
      if (event.code === 4001) {
        useAuthStore.getState().forceLogout("Session expired. Please log in again.");
        return;
      }

      // Reconnect with exponential backoff if still authenticated
      if (isAuthenticatedRef.current) {
        const delay = getReconnectDelay();
        reconnectAttemptsRef.current += 1;
        debugLog("Reconnecting in", delay, "ms (attempt", reconnectAttemptsRef.current, ")");

        reconnectTimeoutRef.current = window.setTimeout(() => {
          connect();
        }, delay);
      }
    };

    ws.onerror = () => {
      debugLog("Error occurred");
      // Error will trigger onclose, state handled there
    };

    wsRef.current = ws;
  }, [handleMessage, getReconnectDelay]);

  // Effect to manage connection lifecycle
  // Using refs for connect/disconnect to avoid infinite loops
  useEffect(() => {
    if (isAuthenticated && accessToken) {
      connect();
    } else {
      disconnect();
    }

    return () => {
      disconnect();
    };
    // Issue #10: Intentionally limited deps to prevent infinite loops
    // - connect/disconnect callbacks use refs internally for latest values
    // - Including them would cause reconnection loops on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, accessToken]);

  return { connect, disconnect, connectionState };
}
