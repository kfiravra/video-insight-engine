import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '@/test/mocks/server';
import { createMockVideo } from '@/test/mocks/handlers';
import { createTestQueryClient } from '@/test/test-utils';
import { useAuthStore } from '@/stores/auth-store';
import { setAccessToken } from '@/api/client';
import { useUserUsage } from '../use-usage';
import { useAddVideo } from '../use-videos';
import { usePlaylistImport } from '../use-playlists';
import { usageSummary } from '@/components/usage/__tests__/usage-fixtures';

const API_URL = 'http://localhost:3000/api';

function trackUsageReads(): { urls: string[] } {
  const tracker: { urls: string[] } = { urls: [] };
  server.use(
    http.get(`${API_URL}/users/me/usage`, ({ request }) => {
      tracker.urls.push(request.url);
      return HttpResponse.json(usageSummary({ credits: { available: 150, lots: [] } }));
    }),
  );
  return tracker;
}

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>;
}

describe('useUserUsage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: { id: 'user-1', email: 'test@example.com', name: 'Test User' },
      accessToken: 'test-access-token',
      isAuthenticated: true,
      isLoading: false,
    });
    setAccessToken('test-access-token');
  });

  it('should read the minutes summary from the default usage endpoint', async () => {
    const reads = trackUsageReads();

    const { result } = renderHook(() => useUserUsage(), { wrapper });

    await waitFor(() => expect(result.current.data?.credits.available).toBe(150));
    expect(reads.urls).toEqual([`${API_URL}/users/me/usage`]);
  });

  it('should read the usage again after a video is submitted', async () => {
    const reads = trackUsageReads();
    server.use(
      http.post(`${API_URL}/videos`, () =>
        HttpResponse.json({ video: createMockVideo({ id: 'video-1' }), cached: false }),
      ),
    );
    const { result } = renderHook(() => ({ usage: useUserUsage(), add: useAddVideo() }), { wrapper });
    await waitFor(() => expect(result.current.usage.data).toBeDefined());

    await act(() =>
      result.current.add.mutateAsync({ url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' }),
    );

    await waitFor(() => expect(reads.urls).toHaveLength(2));
  });

  it('should read the usage again after a playlist import', async () => {
    const reads = trackUsageReads();
    server.use(
      http.post(`${API_URL}/playlists/import`, () =>
        HttpResponse.json({
          folder: { id: 'f1', name: 'P' },
          videos: [],
          totalVideos: 0,
          cachedCount: 0,
          processingCount: 0,
          failedCount: 0,
          failedVideos: [],
        }),
      ),
    );
    const { result } = renderHook(() => ({ usage: useUserUsage(), imp: usePlaylistImport() }), { wrapper });
    await waitFor(() => expect(result.current.usage.data).toBeDefined());

    await act(() => result.current.imp.mutateAsync({ url: 'https://www.youtube.com/playlist?list=PL1' }));

    await waitFor(() => expect(reads.urls).toHaveLength(2));
  });
});
