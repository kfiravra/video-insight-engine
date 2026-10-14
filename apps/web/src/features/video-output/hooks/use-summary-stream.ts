/**
 * Streaming hook for video summarization.
 *
 * Uses Server-Sent Events (SSE) for real-time streaming
 * of video summary generation via the pipeline output system.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { getUserFriendlyError } from "@/features/video-output/lib/streaming/stream-error-messages";
import { loadStreamCache, saveStreamCache, clearStreamCache } from "@/features/video-output/lib/streaming/stream-cache";
import { processEvent } from "@/features/video-output/lib/streaming/stream-event-processor";
import {
  subscribeToStream,
  abortStream,
  type StreamEvent,
} from "@/features/video-output/lib/streaming/stream-registry";
import type { StreamTab } from "@/features/video-output/lib/streaming/stream-tabs";

import type {
  VideoContext,
  TriageResult,
  EnrichmentData,
  SynthesisResult,
  VIEResponseMeta,
} from "@vie/types";

export type StreamPhase =
  | "idle"
  | "connecting"
  | "metadata"
  | "transcript"
  | "extraction"
  | "building"
  | "translation"
  | "done"
  | "cancelled"
  | "error";

/** Sub-phase flavor for stages that take multiple backend forms. The raw SSE
 *  phase vocabulary is finer-grained than the UI timeline (whisper vs cached
 *  captions vs no-captions fallback all live inside "transcript"); the
 *  processor collapses raw phases to StreamPhase and preserves the flavor
 *  here so the UI can explain the wait without a nine-step display. */
export type StreamPhaseDetail =
  | "captions-cached"
  | "audio-transcription"
  | "metadata-only"
  /** A YouTube proxy exit was blocked; the summarizer is rotating exits (no new one after 15 s). */
  | "youtube-retrying";

/** User-facing labels for each streaming phase. Plain verbs, no service jargon. */
export const STREAM_PHASE_LABELS: Record<StreamPhase, string> = {
  idle: "Getting ready…",
  connecting: "Connecting…",
  metadata: "Reading the video…",
  transcript: "Getting the transcript…",
  extraction: "Extracting the knowledge…",
  building: "Building your tabs…",
  translation: "Translating…",
  done: "Done.",
  cancelled: "Cancelled.",
  error: "Something went wrong.",
};

export interface FrameInfo {
  index: number;
  timestamp: number;
  url: string;
  s3Key?: string;
  ocrText?: string;
  textDensity?: number;
}

/** Per-batch extraction progress shape.
 *
 * ``batch`` and ``of`` are populated only by the chunked extraction path
 * (Phase 3). Single + overflow extractions emit just ``section`` and
 * ``percent``. Consumers must treat the optional fields as such — undefined
 * means "not chunked", not "unknown count". */
export interface ExtractionProgressInfo {
  section: string;
  percent: number;
  batch?: number;
  of?: number;
}

// Local types not in shared package
export interface VideoMetadata {
  title?: string;
  channel?: string;
  thumbnailUrl?: string;
  duration?: number;
  context?: VideoContext;
}

export interface StreamState {
  phase: StreamPhase;
  phaseDetail: StreamPhaseDetail | null;
  metadata: VideoMetadata | null;
  duration: number | null;
  error: string | null;
  isCached: boolean;
  processingTimeMs: number | null;
  // Partial-result flag from the summarizer terminal events: extraction
  // dropped batches or coverage was critical. Drives the retry affordance.
  degraded: boolean;
  // Warning state for partial failures
  warnings: string[];
  // Celebration trigger — increment to fire a new confetti burst
  confettiCount: number;
  // Pipeline output state (triage pipeline)
  triage: TriageResult | null;
  extractionProgress: ExtractionProgressInfo | null;
  domainData: Record<string, unknown> | null;
  enrichment: EnrichmentData | null;
  synthesis: SynthesisResult | null;
  // Assembled output (component-addressed tabs), kept sorted by position —
  // see lib/streaming/stream-tabs.ts for the tab_ready contract.
  meta: VIEResponseMeta | null;
  tabs: StreamTab[];
  /** `complete.tabIds` — the final tab order; null until `complete`. */
  finalTabIds: string[] | null;
  /** Streamed tab id → the id that superseded it (`tab_ready.replaces`). */
  replacedTabIds: Record<string, string>;
  // Progressive rendering state
  tabCount: number;
  tabLabels: { id: string; label: string; emoji: string }[];
  // Frame extraction data
  frames: FrameInfo[];
}

interface UseSummaryStreamOptions {
  videoSummaryId: string;
  enabled: boolean;
  onComplete?: (state: StreamState) => void;
  onError?: (error: string) => void;
}

export const INITIAL_STREAM_STATE: StreamState = {
  phase: "idle",
  phaseDetail: null,
  metadata: null,
  duration: null,
  error: null,
  isCached: false,
  processingTimeMs: null,
  degraded: false,
  warnings: [],
  confettiCount: 0,
  // Pipeline output state (triage pipeline)
  triage: null,
  extractionProgress: null,
  domainData: null,
  enrichment: null,
  synthesis: null,
  // Assembled output
  meta: null,
  tabs: [],
  finalTabIds: null,
  replacedTabIds: {},
  // Progressive rendering state
  tabCount: 0,
  tabLabels: [],
  // Frame extraction data
  frames: [],
};

// Save interval for localStorage cache (ms) - don't save on every update
const CACHE_SAVE_INTERVAL = 2000;

// ─── Hook ───

export function useSummaryStream({
  videoSummaryId,
  enabled,
  onComplete,
  onError,
}: UseSummaryStreamOptions): StreamState & { retry: () => void; stop: () => void } {
  const [state, setState] = useState<StreamState>(INITIAL_STREAM_STATE);
  const accessToken = useAuthStore((s) => s.accessToken);
  // Track if we've restored from cache to avoid double-restoration
  const cacheRestoredRef = useRef(false);
  // Store callbacks in refs to avoid dependency issues
  // Updated in useEffect (not during render) for concurrent mode safety
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  const hasToken = !!accessToken;
  // Active subscription cleanup function from the registry
  const unsubscribeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    onCompleteRef.current = onComplete;
    onErrorRef.current = onError;
  }, [onComplete, onError]);

  // Save state to localStorage periodically for resumption after refresh.
  // Uses ref + interval to avoid firing on every state change during streaming.
  // Written in an effect, not during render, per the react-hooks refs rule —
  // the interval only reads it post-commit, so the value is always current.
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const subscribe = useCallback((token: string) => {
    if (!videoSummaryId || !token) return;

    setState({ ...INITIAL_STREAM_STATE, phase: "connecting" });

    unsubscribeRef.current?.();
    unsubscribeRef.current = subscribeToStream(videoSummaryId, token, (event: StreamEvent) => {
      if (event.event === "error" && typeof event.message === "string") {
        const userFriendlyError = getUserFriendlyError(event.message);
        setState((prev) => ({ ...prev, phase: "error", error: userFriendlyError }));
        onErrorRef.current?.(userFriendlyError);
        return;
      }
      processEvent(event, setState);
      if (event.event === "done") {
        setState((prev) => {
          if (prev.phase !== "error") {
            onCompleteRef.current?.(prev);
          }
          return prev;
        });
      }
    });
  }, [videoSummaryId]);

  const retry = useCallback(() => {
    if (accessToken) subscribe(accessToken);
  }, [accessToken, subscribe]);

  const stop = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    if (videoSummaryId) abortStream(videoSummaryId);
    setState((prev) => ({
      ...prev,
      phase: "cancelled",
      error: "Summarization cancelled by user",
    }));
  }, [videoSummaryId]);

  // Track the last videoSummaryId we started subscribing for
  const lastConnectedIdRef = useRef<string | null>(null);

  // A new video is being viewed: drop any streamed state from the previous
  // video so its tabs/metadata can't bleed through VideoDetailPage's
  // stream-over-cache precedence (resolvedTabs / mergedVideo.title). Without
  // this, navigating from a streamed video to a completed one (enabled=false,
  // so subscribe() never runs) leaves the prior video's content on screen.
  // `INITIAL_STREAM_STATE` is a stable module-level constant (not derived from props),
  // so it is intentionally omitted from the deps — there is no stale-closure
  // risk and exhaustive-deps does not require it.
  useEffect(() => {
    // Audited: runs once per video navigation, not per render — no cascade.
    // The alternative (key-remount at the page level) would tear down the
    // shared SSE subscription registry, which must survive remounts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState(INITIAL_STREAM_STATE);
    cacheRestoredRef.current = false;
  }, [videoSummaryId]);

  useEffect(() => {
    if (enabled && videoSummaryId && hasToken && lastConnectedIdRef.current !== videoSummaryId) {
      lastConnectedIdRef.current = videoSummaryId;

      // Restore cached state before subscribing (allows resumption after refresh)
      if (!cacheRestoredRef.current) {
        cacheRestoredRef.current = true;
        const cachedState = loadStreamCache(videoSummaryId);
        if (cachedState) {
          // Audited: one-shot cache hydration guarded by cacheRestoredRef —
          // fires at most once per subscribe cycle, before events stream in.
          // eslint-disable-next-line react-hooks/set-state-in-effect
          setState((prev) => ({ ...prev, ...cachedState }));
        }
      }

      // Pass accessToken explicitly to avoid ref timing issues on initial mount.
      if (accessToken) subscribe(accessToken);
    }

    // When streaming is disabled, reset tracking to allow reconnection
    // when the user returns to the same video
    if (!enabled) {
      lastConnectedIdRef.current = null;
      cacheRestoredRef.current = false;
    }

    // Cleanup: detach this subscriber on dependency change / unmount.
    // The shared stream itself keeps running until the producer signals done,
    // so a StrictMode unmount/remount does NOT abort the SSE fetch.
    return () => {
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
    };
  }, [enabled, videoSummaryId, hasToken, accessToken, subscribe]);

  useEffect(() => {
    if (!videoSummaryId || !enabled) return;

    const interval = setInterval(() => {
      const s = stateRef.current;
      // Don't save if stream completed or errored (will be cleared anyway)
      if (s.phase === "done" || s.phase === "error" || s.phase === "cancelled") return;
      // Only save if we have meaningful data to cache
      const hasData = s.synthesis || s.triage || s.domainData || s.metadata || s.tabs.length > 0;
      if (hasData) saveStreamCache(videoSummaryId, s);
    }, CACHE_SAVE_INTERVAL);

    return () => clearInterval(interval);
  }, [videoSummaryId, enabled]);

  // Clear cache when stream completes (success, error, or cancelled)
  // This prevents stale cache entries from accumulating
  useEffect(() => {
    if (videoSummaryId && (state.phase === "done" || state.phase === "error" || state.phase === "cancelled")) {
      clearStreamCache(videoSummaryId);
    }
  }, [videoSummaryId, state.phase]);

  return { ...state, retry, stop };
}
