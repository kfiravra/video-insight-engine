import { useRegistrationStatus } from "./use-registration-status";

/**
 * Whether self-service signup is open on this deployment.
 *
 * `undefined` while the status loads, so closed deployments never flash a
 * signup link. A failed request fails open: the API still enforces the flag
 * on POST /api/auth/register, so showing the link is harmless.
 */
export function useRegistrationOpen(): boolean | undefined {
  const { data, isError } = useRegistrationStatus();
  if (isError) return true;
  return data?.open;
}
