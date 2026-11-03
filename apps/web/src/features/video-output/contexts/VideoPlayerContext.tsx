import { createContext, useContext, useRef, useState, useCallback, useEffect, useMemo, type ReactNode } from 'react';
import type { YouTubePlayerRef } from '@/components/videos/YouTubePlayer';

interface VideoPlayerContextType {
  seekTo: (seconds: number) => void;
  isPlayerOpen: boolean;
  openPlayer: () => void;
  closePlayer: () => void;
  togglePlayer: () => void;
  playerRef: React.RefObject<YouTubePlayerRef | null>;
  /** Current playback time in seconds (polled at 1s interval when player is open). */
  currentTime: number;
  /** True once the user has engaged the player — first seek or first open.
   *  Latches for the provider's lifetime and never reverts; the provider is
   *  remounted per video (VideoDetailPage keys the subtree by video id), so
   *  a new video naturally starts un-engaged. */
  hasEngaged: boolean;
  /** Registers the DOM element wrapping the inline player so `seekTo` can
   *  scroll it back into view when the user seeks from far down the page.
   *  Pass directly as a `ref` callback on the player wrapper. */
  registerPlayerAnchor: (el: HTMLElement | null) => void;
}

const VideoPlayerContext = createContext<VideoPlayerContextType | null>(null);

const NOOP_CONTEXT: VideoPlayerContextType = {
  seekTo: () => {},
  isPlayerOpen: false,
  openPlayer: () => {},
  closePlayer: () => {},
  togglePlayer: () => {},
  playerRef: { current: null },
  currentTime: 0,
  hasEngaged: false,
  registerPlayerAnchor: () => {},
};

/** True when Escape originated from a typing surface — the key belongs to the
 *  field (clear/cancel semantics), not to the player. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/** Scrolls the registered player wrapper into view when it sits fully outside
 *  the viewport — smooth by default, instant under prefers-reduced-motion. */
function scrollAnchorIntoView(el: HTMLElement): void {
  const rect = el.getBoundingClientRect();
  const outsideViewport = rect.bottom <= 0 || rect.top >= window.innerHeight;
  if (!outsideViewport) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'nearest' });
}

export function VideoPlayerProvider({ children }: { children: ReactNode }) {
  const playerRef = useRef<YouTubePlayerRef | null>(null);
  const [isPlayerOpen, setIsPlayerOpen] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [hasEngaged, setHasEngaged] = useState(false);
  const isPlayerOpenRef = useRef(false);
  isPlayerOpenRef.current = isPlayerOpen;
  const anchorRef = useRef<HTMLElement | null>(null);
  // Pending seek choreography (rAF → timeout). Cleared on unmount so a seek
  // fired right before a video switch can't scroll/seek the previous video's
  // detached anchor and player instance. Sets, not single refs: rapid seeks
  // each keep their own timer (the last to fire wins, as before).
  const seekFramesRef = useRef<Set<number>>(new Set());
  const seekTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  useEffect(() => {
    const frames = seekFramesRef.current;
    const timers = seekTimersRef.current;
    return () => {
      frames.forEach((id) => cancelAnimationFrame(id));
      timers.forEach((id) => clearTimeout(id));
      frames.clear();
      timers.clear();
    };
  }, []);

  // Poll currentTime from the YouTube player at 1s interval when player is open
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (isPlayerOpen) {
      intervalRef.current = setInterval(() => {
        const t = playerRef.current?.getCurrentTime?.();
        if (typeof t === 'number') setCurrentTime(t);
      }, 1000);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlayerOpen]);

  // Every close path pauses playback first: the player stays mounted while
  // collapsed (CSS grid-rows), so hiding it alone would leave the audio
  // running behind a closed hero.
  const pauseAndClose = useCallback(() => {
    playerRef.current?.pauseVideo();
    setIsPlayerOpen(false);
  }, []);

  // Escape closes the open player — unless the key belongs to a typing
  // surface (search field, chat input), where Escape means clear/cancel.
  useEffect(() => {
    if (!isPlayerOpen) return;
    const handleKeyDown = (e: KeyboardEvent): void => {
      // defaultPrevented → an overlay (Radix dialog, etc.) already consumed
      // this Escape; closing the player underneath it would double-handle.
      if (e.key !== 'Escape' || e.defaultPrevented || isTypingTarget(e.target)) return;
      pauseAndClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isPlayerOpen, pauseAndClose]);

  const openPlayer = useCallback(() => {
    setHasEngaged(true);
    setIsPlayerOpen(true);
  }, []);
  const closePlayer = pauseAndClose;
  const togglePlayer = useCallback(() => {
    if (isPlayerOpenRef.current) {
      pauseAndClose();
      return;
    }
    setHasEngaged(true);
    setIsPlayerOpen(true);
  }, [pauseAndClose]);

  const registerPlayerAnchor = useCallback((el: HTMLElement | null) => {
    anchorRef.current = el;
  }, []);

  const seekTo = useCallback((seconds: number) => {
    // Captured before the state update: the ref flips as soon as React
    // re-renders, which can land before the rAF callback below.
    const wasOpen = isPlayerOpenRef.current;
    setHasEngaged(true);
    setIsPlayerOpen(true);
    // A seek fired from deep in the output must bring the player back on
    // screen. On first open the scroll has to wait out the wrapper's
    // grid-rows expansion (~300ms): measured while collapsed the anchor is
    // a zero-height point whose visibility gate lies, and the hero's height
    // change cancels an in-flight smooth scroll. The same delay lets the
    // player mount before seeking.
    const frame = requestAnimationFrame(() => {
      seekFramesRef.current.delete(frame);
      const timer = setTimeout(() => {
        seekTimersRef.current.delete(timer);
        if (anchorRef.current) scrollAnchorIntoView(anchorRef.current);
        playerRef.current?.seekTo(seconds);
        playerRef.current?.playVideo();
      }, wasOpen ? 0 : 350);
      seekTimersRef.current.add(timer);
    });
    seekFramesRef.current.add(frame);
  }, []);

  const value = useMemo<VideoPlayerContextType>(() => ({
    seekTo, isPlayerOpen, openPlayer, closePlayer, togglePlayer, playerRef, currentTime, hasEngaged, registerPlayerAnchor,
  }), [seekTo, isPlayerOpen, openPlayer, closePlayer, togglePlayer, currentTime, hasEngaged, registerPlayerAnchor]);

  return (
    <VideoPlayerContext.Provider value={value}>
      {children}
    </VideoPlayerContext.Provider>
  );
}

/**
 * Hook to access the video player context.
 * Returns no-op fallback when used outside VideoPlayerProvider (safety for shared components).
 */
export function useVideoPlayer(): VideoPlayerContextType {
  const ctx = useContext(VideoPlayerContext);
  if (!ctx) return NOOP_CONTEXT;
  return ctx;
}
