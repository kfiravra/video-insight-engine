import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { wasRefused } from '../lib/api-error';

/**
 * Wraps the call that changes a user's account so the account and the audit log
 * are re-read afterwards: on success, and on a failure that may have come after
 * the change was applied (a timeout, a 5xx, a dropped connection). Without it an
 * uncertain outcome leaves a stale panel, and the operator retries a change that
 * already happened. A 4xx refusal changed nothing and is not re-read.
 */
export function useRefreshAfterSend(userId: string): <T>(send: () => Promise<T>) => Promise<T> {
  const queryClient = useQueryClient();

  return useCallback(
    async <T>(send: () => Promise<T>): Promise<T> => {
      const refresh = () => {
        void queryClient.invalidateQueries({ queryKey: ['billing-account', userId] });
        void queryClient.invalidateQueries({ queryKey: ['audit'] });
      };
      try {
        const result = await send();
        refresh();
        return result;
      } catch (error) {
        if (!wasRefused(error)) refresh();
        throw error;
      }
    },
    [queryClient, userId],
  );
}
