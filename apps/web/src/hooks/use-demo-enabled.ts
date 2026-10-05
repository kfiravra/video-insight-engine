import { useRegistrationStatus } from "./use-registration-status";

/**
 * Whether this deployment offers the shared demo account.
 *
 * Shares the registration-status query, so it costs no extra request. Unlike
 * `useRegistrationOpen` it fails closed: `false` while loading and on error,
 * so the demo entry never flashes on deployments that do not offer it.
 */
export function useDemoEnabled(): boolean {
  const { data } = useRegistrationStatus();
  return data?.demoEnabled === true;
}
