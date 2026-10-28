import { useEffect, useState } from "react";

/** Current time in ms, refreshed every minute so "next in 7h 30m" stays accurate while open. */
export function useMinuteTick(): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);
  return now;
}
