import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { formatElapsed, useElapsedSeconds } from '../use-elapsed-seconds';

describe('useElapsedSeconds', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-02T10:01:35.000Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should start from the age of the given origin, not from zero', () => {
    const startedAtMs = Date.now() - 95_000;
    const { result } = renderHook(() => useElapsedSeconds(startedAtMs));
    expect(result.current).toBe(95);
  });

  it('should tick once a second', () => {
    const startedAtMs = Date.now() - 95_000;
    const { result } = renderHook(() => useElapsedSeconds(startedAtMs));
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current).toBe(97);
  });

  it('should clamp a server clock running ahead of the client to zero', () => {
    const startedAtMs = Date.now() + 30_000;
    const { result } = renderHook(() => useElapsedSeconds(startedAtMs));
    expect(result.current).toBe(0);
  });

  it('should count from mount when no origin is given', () => {
    const { result } = renderHook(() => useElapsedSeconds());
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current).toBe(3);
  });

  it('should stop ticking on unmount', () => {
    const { unmount } = renderHook(() => useElapsedSeconds(Date.now()));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('formatElapsed', () => {
  it('should format minutes and zero-padded seconds', () => {
    expect(formatElapsed(0)).toBe('0:00');
    expect(formatElapsed(5)).toBe('0:05');
    expect(formatElapsed(95)).toBe('1:35');
    expect(formatElapsed(3600)).toBe('60:00');
  });
});
