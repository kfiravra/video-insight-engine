/**
 * Processing Manager Hook
 *
 * App-level hook that observes SSE streams for all processing videos.
 * Enables auto-resume after browser refresh and sidebar sync without user
 * interaction. Both this hook and the page-level useSummaryStream hook
 * subscribe to a shared per-video stream via the stream registry, so a
 * video opened on the detail page does NOT trigger a second SSE fetch.
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";
import { useProcessingStore } from "@/features/video-output/stores/processing-store";
import { useAllVideos } from "@/hooks/use-videos";
import { queryKeys } from "@/lib/query-keys";
import {
  validatePhaseEvent,
  validateMetadataEvent,
  validateErrorEvent,
} from "@/features/video-output/lib/streaming/sse-validators";
import {
  subscribeToStream,
  abortAllStreams,
  type StreamEvent,
} from "@/features/video-output/lib/streaming/stream-registry";
import type { StreamPhase } from "@/features/video-output/hooks/use-summary-stream";

/** Lightweight state tracked by processing manager */
interface ManagerState {
  phase: StreamPhase;
  metadata: {
    title?: string;
    channel?: string;
    thumbnailUrl?: string;
    duration?: number;
  } | null;
  error?: string | null;
}

function reduceManagerEvent(
  event: StreamEvent,
  state: ManagerState,
): ManagerState {
  const eventType = typeof event.event === "string" ? event.event : null;
  if (!eventType) return state;

  switch (eventType) {
    case "phase": {
      const phase = validatePhaseEvent(event);
      return phase ? { ...state, phase: phase as StreamPhase } : state;
    }

    case "metadata": {
      const metadata = validateMetadataEvent(event);
      return {
        ...state,
        phase: "metadata",
        metadata: {
          title: metadata.title,
          channel: metadata.channel,
          thumbnailUrl: metadata.thumbnailUrl,
          duration: metadata.duration,
        },
      };
    }

    case "done":
      return { ...state, phase: "done" };

    case "error": {
      const { message } = validateErrorEvent(event);
      return { ...state, phase: "error", error: message };
    }

    default:
      return state;
  }
}

/**
 * Manages all processing video streams at the app level.
 *
 * - Watches video list for status === "pending" | "processing"
 * - Subscribes to the shared SSE stream for each processing video
 * - Updates processing store with lightweight phase/metadata state
 * - Cleans up subscriptions when videos complete or are deleted
 */
export function useProcessingManager() {
  const queryClient = useQueryClient();
  const accessToken = useAuthStore((s) => s.accessToken);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const { setStreamState, removeStreamState } = useProcessingStore();
  // Gated on the session: the login page must not fetch a library, and a
  // signed-out observer must not keep a cleared list entry alive.
  const { data: videosData } = useAllVideos({ enabled: isAuthenticated });
  const videos = videosData?.videos;

  // Track unsubscribe callbacks by videoSummaryId
  const subscriptionsRef = useRef<Map<string, () => void>>(new Map());
  const accessTokenRef = useRef(accessToken);

  useEffect(() => {
    accessTokenRef.current = accessToken;
  }, [accessToken]);

  useEffect(() => {
    if (!isAuthenticated || !videos) return;

    const processingVideos = videos.filter(
      (v) => v.status === "pending" || v.status === "processing",
    );
    const processingIds = new Set(processingVideos.map((v) => v.videoSummaryId));

    // Subscribe to streams for newly-processing videos
    for (const video of processingVideos) {
      const { videoSummaryId } = video;
      if (subscriptionsRef.current.has(videoSummaryId)) continue;

      const token = accessTokenRef.current;
      if (!token) continue;

      setStreamState(videoSummaryId, {
        phase: "connecting",
        metadata: null,
        error: null,
      });

      let state: ManagerState = { phase: "connecting" as StreamPhase, metadata: null };
      const unsubscribe = subscribeToStream(videoSummaryId, token, (event) => {
        state = reduceManagerEvent(event, state);
        setStreamState(videoSummaryId, {
          phase: state.phase,
          metadata: state.metadata,
          error: state.error ?? null,
        });
        if (event.event === "done") {
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.lists() });
          removeStreamState(videoSummaryId);
          subscriptionsRef.current.get(videoSummaryId)?.();
          subscriptionsRef.current.delete(videoSummaryId);
        }
      });
      subscriptionsRef.current.set(videoSummaryId, unsubscribe);
    }

    // Drop subscriptions for videos that are no longer processing
    const toRemove: string[] = [];
    for (const [videoSummaryId] of subscriptionsRef.current) {
      if (!processingIds.has(videoSummaryId)) toRemove.push(videoSummaryId);
    }
    for (const id of toRemove) {
      subscriptionsRef.current.get(id)?.();
      subscriptionsRef.current.delete(id);
      removeStreamState(id);
    }
  }, [isAuthenticated, videos, setStreamState, removeStreamState, queryClient]);

  // Drop all subscriptions on unmount.
  useEffect(() => {
    const subscriptions = subscriptionsRef.current;
    return () => {
      for (const unsubscribe of subscriptions.values()) unsubscribe();
      subscriptions.clear();
    };
  }, []);

  // On logout, abort the underlying streams (not just unsubscribe) so the
  // server-side connection actually closes.
  useEffect(() => {
    if (!isAuthenticated) {
      for (const unsubscribe of subscriptionsRef.current.values()) unsubscribe();
      subscriptionsRef.current.clear();
      abortAllStreams();
      useProcessingStore.getState().clearAllStreams();
    }
  }, [isAuthenticated]);
}
