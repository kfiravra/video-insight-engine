import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { usageApi, type UsageSummary } from "@/api/usage";

export const usageQueryKey = ["usage", "me"] as const;

export function useUserUsage(): UseQueryResult<UsageSummary> {
  return useQuery<UsageSummary>({
    queryKey: usageQueryKey,
    queryFn: () => usageApi.me(),
    // The tile sits in the user menu and the billing page reads the same data;
    // refresh on focus and every 60s.
    staleTime: 30_000,
    refetchOnWindowFocus: true,
    refetchInterval: 60_000,
  });
}
