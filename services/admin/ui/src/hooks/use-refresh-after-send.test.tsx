import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ApiError } from '../lib/api-error';
import { useRefreshAfterSend } from './use-refresh-after-send';

const USER_ID = 'a'.repeat(24);

function setup() {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useRefreshAfterSend(USER_ID), { wrapper });
  return { refreshAfter: result.current, invalidate };
}

describe('useRefreshAfterSend', () => {
  it('should return what the call returned', async () => {
    const { refreshAfter } = setup();

    expect(await refreshAfter(async () => 'done')).toBe('done');
  });

  it('should re-read the account and the audit log after a success', async () => {
    const { refreshAfter, invalidate } = setup();

    await refreshAfter(async () => 'done');

    expect(invalidate.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: ['billing-account', USER_ID] },
      { queryKey: ['audit'] },
    ]);
  });

  it.each([
    ['a timeout', new ApiError(504, 'timeout', 'the change may have been applied')],
    ['a server error', new ApiError(500, 'boom')],
    ['a dropped connection', new TypeError('Failed to fetch')],
  ])('should re-read after %s, which can follow an applied change', async (_name, error) => {
    const { refreshAfter, invalidate } = setup();

    await expect(refreshAfter(() => Promise.reject(error))).rejects.toBe(error);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['audit'] });
  });

  it.each([400, 403, 404, 409, 422, 429])('should not re-read after a %i refusal, which changed nothing', async (status) => {
    const { refreshAfter, invalidate } = setup();
    const refusal = new ApiError(status, 'refused');

    await expect(refreshAfter(() => Promise.reject(refusal))).rejects.toBe(refusal);

    expect(invalidate).not.toHaveBeenCalled();
  });
});
