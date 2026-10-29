import { useCallback, useRef } from 'react';
import { newIdempotencyKey } from '../lib/idempotency';

interface Attempt {
  payload: string;
  key: string;
}

/**
 * One `Idempotency-Key` per distinct request. Submitting the identical payload
 * again after a failure or a timeout returns the same key, so a change that did
 * go through the first time applies once; a different payload, or `reset()` after
 * a success, starts a new key.
 */
export function useAttemptKey(): { keyFor: (payload: unknown) => string; reset: () => void } {
  const last = useRef<Attempt | null>(null);

  const keyFor = useCallback((payload: unknown): string => {
    const serialized = JSON.stringify(payload);
    if (last.current?.payload !== serialized) {
      last.current = { payload: serialized, key: newIdempotencyKey() };
    }
    return last.current.key;
  }, []);

  const reset = useCallback(() => {
    last.current = null;
  }, []);

  return { keyFor, reset };
}
