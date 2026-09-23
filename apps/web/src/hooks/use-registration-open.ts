import { useQuery } from "@tanstack/react-query";
import { authApi } from "@/api/auth";
import { queryKeys } from "@/lib/query-keys";

const REGISTRATION_STATUS_STALE_MS = 5 * 60 * 1000;

/**
 * Whether self-service signup is open on this deployment.
 *
 * `undefined` while the status loads, so closed deployments never flash a
 * signup link. A failed request fails open: the API still enforces the flag
 * on POST /api/auth/register, so showing the link is harmless.
 */
export function useRegistrationOpen(): boolean | undefined {
  const { data, isError } = useQuery({
    queryKey: queryKeys.auth.registration(),
    queryFn: () => authApi.getRegistrationStatus(),
    staleTime: REGISTRATION_STATUS_STALE_MS,
  });
  if (isError) return true;
  return data?.open;
}
