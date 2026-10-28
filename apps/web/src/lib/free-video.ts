import type { UsageSummary } from "@vie/types";

// `free.perWindow` is the effective number of free videos per rolling window for
// this account (the plan's one, an admin's cap, or 0 when caps switch the free
// video off). The type declares it, but it is still checked at runtime: a response
// from a server that predates the field must read as the standard single free video,
// not as "off".
type FreeSummary = Pick<UsageSummary["free"], "available" | "nextFreeAt" | "maxVideoMinutes"> & {
  perWindow?: number;
};

const STANDARD_FREE_VIDEOS = 1;

export function freeVideosPerWindow(free: FreeSummary): number {
  const count = free.perWindow;
  return typeof count === "number" && Number.isFinite(count) && count >= 0 ? count : STANDARD_FREE_VIDEOS;
}
