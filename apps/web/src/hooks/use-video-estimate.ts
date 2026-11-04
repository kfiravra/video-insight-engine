import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { videosApi, type VideoEstimateResponse } from "@/api/videos";
import { queryKeys } from "@/lib/query-keys";

/** How long a pasted URL must stay unchanged before it is estimated. */
export const ESTIMATE_DEBOUNCE_MS = 400;

/** `value` once it has stopped changing for `delayMs`; "" until then on mount. */
function useSettledValue(value: string, delayMs: number): string {
  const [settled, setSettled] = useState<string>("");
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}

function isEstimate(data: unknown): data is VideoEstimateResponse {
  if (typeof data !== "object" || data === null) return false;
  const { tokens, free } = data as Record<string, unknown>;
  return typeof tokens === "number" && Number.isFinite(tokens) && typeof free === "boolean";
}

/**
 * What submitting `url` would hold, once the URL has settled for
 * ESTIMATE_DEBOUNCE_MS; null while typing, loading, on any error (a 429
 * included) and when `url` is null. Pass null for anything that is not a
 * YouTube video URL. It never blocks a submit: the submit's own refusal is
 * the source of truth. Cached per URL, so pasting the same link again is free.
 */
export function useVideoEstimate(url: string | null): VideoEstimateResponse | null {
  const target = url ?? "";
  const settled = useSettledValue(target, ESTIMATE_DEBOUNCE_MS);
  const enabled = settled !== "" && settled === target;

  const { data } = useQuery({
    queryKey: queryKeys.videoEstimates.byUrl(settled),
    queryFn: ({ signal }) => videosApi.estimate(settled, signal),
    enabled,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  return enabled && isEstimate(data) ? data : null;
}
