import { useEffect, useState } from 'react';

/** Whole seconds from `startedAtMs` to `nowMs`, never negative — a server
 *  clock a little ahead of the client must not read as a negative wait. */
function elapsedSince(startedAtMs: number, nowMs: number): number {
  return Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
}

/**
 * Seconds since `startedAtMs`, ticking once a second. Fed the job's server
 * start time, the clock shows the run's real age — a refresh mid-run does not
 * restart it at 0:00, and it reads the same before and after the first tab
 * lands. A changed origin is picked up on the next tick.
 */
export function useElapsedSeconds(startedAtMs: number): number {
  const [elapsed, setElapsed] = useState(() => elapsedSince(startedAtMs, Date.now()));
  useEffect(() => {
    const timer = setInterval(() => setElapsed(elapsedSince(startedAtMs, Date.now())), 1000);
    return () => clearInterval(timer);
  }, [startedAtMs]);
  return elapsed;
}

/** `m:ss` — minutes unbounded, seconds zero-padded. */
export function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
