import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { server } from '@/test/mocks/server';
import { createMockVideo } from '@/test/mocks/handlers';
import { useAuthStore } from '@/stores/auth-store';
import { setAccessToken } from '@/api/client';
import { DirectionProvider } from '@/contexts/DirectionContext';

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock('sonner', () => ({ toast: { error: toastError, success: vi.fn() } }));

import { useResummarize } from '../use-resummarize';

const API_URL = 'http://localhost:3000/api';
const VIDEO = { youtubeId: 'dQw4w9WgXcQ', folderId: 'folder-1' };

function makeWrapper(language = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <DirectionProvider language={language} isRTL={language !== 'en'}>
        {children}
      </DirectionProvider>
    </QueryClientProvider>
  );
  return { wrapper, invalidate };
}

function rejectWith(status: number, body: Record<string, unknown>) {
  server.use(http.post(`${API_URL}/videos`, () => HttpResponse.json(body, { status })));
}

describe('useResummarize', () => {
  beforeEach(() => {
    toastError.mockReset();
    useAuthStore.setState({
      user: { id: 'user-1', email: 'test@example.com', name: 'Test User' },
      accessToken: 'token',
      isAuthenticated: true,
      isLoading: false,
    });
    setAccessToken('token');
  });

  it('should submit the video again with bypassCache, as a billed submit', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post(`${API_URL}/videos`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ video: createMockVideo({ id: 'v2' }), cached: false });
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize(VIDEO));

    await waitFor(() => expect(body.bypassCache).toBe(true));
    expect(body.url).toBe('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
  });

  it('should refresh the usage tile after a retry that was accepted', async () => {
    server.use(
      http.post(`${API_URL}/videos`, () => HttpResponse.json({ video: createMockVideo({ id: 'v2' }), cached: false })),
    );
    const { wrapper, invalidate } = makeWrapper();
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize(VIDEO));

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['usage', 'me'] }));
  });

  it('should toast how many minutes the video needs when the retry is refused (402)', async () => {
    rejectWith(402, { error: 'INSUFFICIENT_MINUTES', message: 'x', statusCode: 402, needed: 31, available: 12 });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize(VIDEO));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('This video needs 31 minutes and you have 12 minutes to spend.'),
    );
  });

  it('should toast the wait when the burst limit refuses the retry (429)', async () => {
    rejectWith(429, { error: 'RATE_LIMITED', message: 'Up to 20 videos per hour', statusCode: 429, retryAfterSec: 900 });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize(VIDEO));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("You're adding videos too fast. Try again in 15m."));
  });

  it('should toast in Hebrew under a Hebrew direction context', async () => {
    rejectWith(403, { error: 'ACCOUNT_BLOCKED', message: 'x', statusCode: 403 });
    const { wrapper } = makeWrapper('he');
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize(VIDEO));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('החשבון הזה לא יכול להתחיל סרטונים חדשים כרגע.'));
  });

  it('should toast the message of any other failure', async () => {
    rejectWith(500, { error: 'INTERNAL_ERROR', message: 'Something broke', statusCode: 500 });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize(VIDEO));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Something broke'));
  });

  it('should send nothing for a video with no YouTube id', () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useResummarize(), { wrapper });

    act(() => result.current.resummarize({ youtubeId: '', folderId: null }));

    expect(result.current.isPending).toBe(false);
    expect(toastError).not.toHaveBeenCalled();
  });
});
