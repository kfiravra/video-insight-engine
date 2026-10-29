import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useAttemptKey } from './use-attempt-key';

describe('useAttemptKey', () => {
  it('should return the same key for an identical payload', () => {
    const { result } = renderHook(() => useAttemptKey());

    const first = result.current.keyFor({ minutes: 5, reason: 'a' });

    expect(result.current.keyFor({ minutes: 5, reason: 'a' })).toBe(first);
  });

  it('should return a new key when the payload changes', () => {
    const { result } = renderHook(() => useAttemptKey());

    const first = result.current.keyFor({ minutes: 5, reason: 'a' });

    expect(result.current.keyFor({ minutes: 6, reason: 'a' })).not.toBe(first);
  });

  it('should return a new key after a reset', () => {
    const { result } = renderHook(() => useAttemptKey());
    const first = result.current.keyFor({ minutes: 5 });

    result.current.reset();

    expect(result.current.keyFor({ minutes: 5 })).not.toBe(first);
  });

  it('should return a key the server accepts', () => {
    const { result } = renderHook(() => useAttemptKey());

    expect(result.current.keyFor({})).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });
});
