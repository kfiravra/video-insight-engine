import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { VideoPlayerProvider, useVideoPlayer } from '../VideoPlayerContext';
import { setPrefersReducedMotionForTest } from '@/test/setup';
import type { ReactNode } from 'react';

function Wrapper({ children }: { children: ReactNode }) {
  return <VideoPlayerProvider>{children}</VideoPlayerProvider>;
}

/** Builds a fake player anchor element with a controllable viewport position.
 *  jsdom implements neither layout nor scrollIntoView, so both are stubbed. */
function createAnchor(top: number, height: number): HTMLElement {
  const el = document.createElement('div');
  el.getBoundingClientRect = () =>
    ({ top, bottom: top + height, left: 0, right: 0, width: 0, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
  el.scrollIntoView = vi.fn();
  return el;
}

describe('VideoPlayerContext', () => {
  describe('useVideoPlayer outside provider', () => {
    it('returns the NOOP context with the full shape', () => {
      const { result } = renderHook(() => useVideoPlayer());
      expect(result.current.isPlayerOpen).toBe(false);
      expect(result.current.hasEngaged).toBe(false);
      expect(result.current.currentTime).toBe(0);
      expect(result.current.playerRef.current).toBeNull();
    });

    it('NOOP actions do not throw', () => {
      const { result } = renderHook(() => useVideoPlayer());
      expect(() => {
        result.current.seekTo(10);
        result.current.openPlayer();
        result.current.closePlayer();
        result.current.togglePlayer();
        result.current.registerPlayerAnchor(document.createElement('div'));
      }).not.toThrow();
    });
  });

  describe('hasEngaged latch', () => {
    it('should start false', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      expect(result.current.hasEngaged).toBe(false);
    });

    it('should latch true on the first seekTo', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.seekTo(10));
      expect(result.current.hasEngaged).toBe(true);
      expect(result.current.isPlayerOpen).toBe(true);
    });

    it('should latch true on openPlayer', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());
      expect(result.current.hasEngaged).toBe(true);
    });

    it('should latch true when togglePlayer opens the player', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.togglePlayer());
      expect(result.current.isPlayerOpen).toBe(true);
      expect(result.current.hasEngaged).toBe(true);
    });

    it('should never revert once latched', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());
      act(() => result.current.closePlayer());
      expect(result.current.hasEngaged).toBe(true);
      act(() => result.current.togglePlayer());
      act(() => result.current.togglePlayer());
      expect(result.current.hasEngaged).toBe(true);
    });

    it('should stay false on closePlayer alone', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.closePlayer());
      expect(result.current.hasEngaged).toBe(false);
    });
  });

  describe('seekTo scroll-into-view', () => {
    // The scroll is deferred behind rAF + a 350ms first-open timeout so it
    // measures the EXPANDED wrapper, not the collapsed zero-height anchor.
    function flushSeekTimers(): void {
      act(() => {
        vi.runAllTimers();
      });
    }

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'requestAnimationFrame'] });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should scroll the anchor into view when it sits below the viewport', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const anchor = createAnchor(2000, 400); // window.innerHeight is 768 in jsdom
      act(() => result.current.registerPlayerAnchor(anchor));

      act(() => result.current.seekTo(10));
      flushSeekTimers();

      // Test env defaults prefers-reduced-motion to true → instant scroll.
      expect(anchor.scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'nearest' });
    });

    it('should scroll when the anchor sits above the viewport', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const anchor = createAnchor(-500, 300);
      act(() => result.current.registerPlayerAnchor(anchor));

      act(() => result.current.seekTo(10));
      flushSeekTimers();

      expect(anchor.scrollIntoView).toHaveBeenCalledTimes(1);
    });

    it('should defer the first-open scroll until the wrapper has expanded', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const anchor = createAnchor(-500, 300);
      act(() => result.current.registerPlayerAnchor(anchor));

      act(() => result.current.seekTo(10));
      // rAF fires, but the 350ms expansion window has not elapsed yet:
      // scrolling now would measure the collapsed zero-height anchor.
      act(() => {
        vi.advanceTimersByTime(16);
      });
      expect(anchor.scrollIntoView).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(350);
      });
      expect(anchor.scrollIntoView).toHaveBeenCalledTimes(1);
    });

    it('should scroll without the expansion delay when the player is already open', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const anchor = createAnchor(-500, 300);
      act(() => result.current.registerPlayerAnchor(anchor));
      act(() => result.current.openPlayer());

      act(() => result.current.seekTo(10));
      // Two frames: one for the faked rAF, one for its 0ms timeout — still
      // far inside the 350ms window the first-open path would wait out.
      act(() => {
        vi.advanceTimersByTime(32);
      });

      expect(anchor.scrollIntoView).toHaveBeenCalledTimes(1);
    });

    it('should use smooth scrolling when motion is not reduced', () => {
      setPrefersReducedMotionForTest(false);
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const anchor = createAnchor(2000, 400);
      act(() => result.current.registerPlayerAnchor(anchor));

      act(() => result.current.seekTo(10));
      flushSeekTimers();

      expect(anchor.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'nearest' });
    });

    it('should not scroll when the anchor is already visible', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const anchor = createAnchor(100, 400);
      act(() => result.current.registerPlayerAnchor(anchor));

      act(() => result.current.seekTo(10));
      flushSeekTimers();

      expect(anchor.scrollIntoView).not.toHaveBeenCalled();
    });

    it('should not throw when no anchor is registered', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      expect(() => {
        act(() => result.current.seekTo(10));
        flushSeekTimers();
      }).not.toThrow();
    });
  });

  describe('pending seek cleanup', () => {
    it('should not seek or scroll after the provider unmounts mid-choreography', () => {
      vi.useFakeTimers({
        toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'],
      });
      try {
        const { result, unmount } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
        const anchor = createAnchor(-500, 100); // fully above the viewport → would scroll
        act(() => result.current.registerPlayerAnchor(anchor));
        const player = { seekTo: vi.fn(), playVideo: vi.fn() };
        result.current.playerRef.current = player as never;

        act(() => result.current.seekTo(42));
        unmount();
        act(() => {
          vi.runAllTimers();
        });

        expect(player.seekTo).not.toHaveBeenCalled();
        expect(anchor.scrollIntoView).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe('closing pauses playback', () => {
    function mountPlayer(result: { current: ReturnType<typeof useVideoPlayer> }) {
      const player = { seekTo: vi.fn(), playVideo: vi.fn(), pauseVideo: vi.fn(), getCurrentTime: vi.fn(() => 0) };
      result.current.playerRef.current = player;
      return player;
    }

    it('should pause the player on closePlayer', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const player = mountPlayer(result);
      act(() => result.current.openPlayer());
      expect(player.pauseVideo).not.toHaveBeenCalled();

      act(() => result.current.closePlayer());

      expect(player.pauseVideo).toHaveBeenCalledTimes(1);
      expect(result.current.isPlayerOpen).toBe(false);
    });

    it('should pause the player when togglePlayer closes it, not when it opens it', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const player = mountPlayer(result);

      act(() => result.current.togglePlayer());
      expect(result.current.isPlayerOpen).toBe(true);
      expect(player.pauseVideo).not.toHaveBeenCalled();

      act(() => result.current.togglePlayer());
      expect(result.current.isPlayerOpen).toBe(false);
      expect(player.pauseVideo).toHaveBeenCalledTimes(1);
    });

    it('should pause the player when Escape closes it', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      const player = mountPlayer(result);
      act(() => result.current.openPlayer());

      act(() => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });

      expect(player.pauseVideo).toHaveBeenCalledTimes(1);
      expect(result.current.isPlayerOpen).toBe(false);
    });

    it('should close cleanly when no player has mounted yet', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());
      expect(() => act(() => result.current.closePlayer())).not.toThrow();
      expect(result.current.isPlayerOpen).toBe(false);
    });
  });

  describe('Escape closes the player', () => {
    it('should close the open player on Escape', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());

      act(() => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });

      expect(result.current.isPlayerOpen).toBe(false);
    });

    it('should ignore Escape originating from a text input', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());

      const input = document.createElement('input');
      document.body.appendChild(input);
      act(() => {
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });
      input.remove();

      expect(result.current.isPlayerOpen).toBe(true);
    });

    it('should ignore non-Escape keys', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());

      act(() => {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      });

      expect(result.current.isPlayerOpen).toBe(true);
    });

    it('should ignore Escape already consumed by an overlay (defaultPrevented)', () => {
      const { result } = renderHook(() => useVideoPlayer(), { wrapper: Wrapper });
      act(() => result.current.openPlayer());

      act(() => {
        const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
        event.preventDefault();
        document.dispatchEvent(event);
      });

      expect(result.current.isPlayerOpen).toBe(true);
    });
  });
});
