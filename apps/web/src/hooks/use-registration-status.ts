import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { authApi, type RegistrationStatus } from "@/api/auth";
import { queryKeys } from "@/lib/query-keys";

const REGISTRATION_STATUS_STALE_MS = 5 * 60 * 1000;

/**
 * The deployment's public auth switches (signup open, demo offered).
 *
 * The one definition of this query: `useRegistrationOpen` and `useDemoEnabled`
 * both read it, so they share a single request and cache entry.
 */
export function useRegistrationStatus(): UseQueryResult<RegistrationStatus> {
  return useQuery({
    queryKey: queryKeys.auth.registration(),
    queryFn: () => authApi.getRegistrationStatus(),
    staleTime: REGISTRATION_STATUS_STALE_MS,
  });
}
