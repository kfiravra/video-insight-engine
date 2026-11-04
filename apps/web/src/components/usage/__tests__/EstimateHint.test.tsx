import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '@/test/mocks/server';
import { setAccessToken } from '@/api/client';
import { EstimateHint } from '../EstimateHint';
import { renderIn } from './usage-fixtures';

const API_URL = 'http://localhost:3000/api';
const URL_A = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const URL_B = 'https://www.youtube.com/watch?v=9bZkp7q19f0';

/** Render with one query client that survives `rerender`, so the URL can change under the same cache. */
function renderHint(url: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<EstimateHint url={url} />, { wrapper });
}

function estimates(tokensFor: (url: string) => number): { urls: string[] } {
  const seen: { urls: string[] } = { urls: [] };
  server.use(
    http.get(`${API_URL}/videos/estimate`, ({ request }) => {
      const url = new URL(request.url).searchParams.get('url') ?? '';
      seen.urls.push(url);
      return HttpResponse.json({ youtubeId: 'x', durationSec: 600, tokens: tokensFor(url), free: false });
    }),
  );
  return seen;
}

describe('EstimateHint', () => {
  beforeEach(() => {
    setAccessToken('test-access-token');
  });

  it('should show "≈ N tokens" for a URL once it settles', async () => {
    estimates(() => 1_020);

    renderIn(<EstimateHint url={URL_A} />);

    expect(await screen.findByTestId('estimate-hint')).toHaveTextContent('≈ 1,020 tokens');
  });

  it('should name the line for screen readers without a price', async () => {
    estimates(() => 840);

    renderIn(<EstimateHint url={URL_A} />);

    expect((await screen.findByTestId('estimate-hint')).textContent).toBe('Estimate: ≈ 840 tokens');
  });

  it('should send the URL as a query parameter', async () => {
    const seen = estimates(() => 840);

    renderIn(<EstimateHint url={URL_A} />);

    await waitFor(() => expect(seen.urls).toEqual([URL_A]));
  });

  it('should ask nothing and show nothing without a URL', async () => {
    const seen = estimates(() => 840);

    renderIn(<EstimateHint url={null} />);
    await new Promise((resolve) => setTimeout(resolve, 600));

    expect([seen.urls, screen.queryByTestId('estimate-hint')]).toEqual([[], null]);
  });

  it('should wait for the URL to settle, asking only for the last one', async () => {
    const seen = estimates((url) => (url === URL_B ? 930 : 840));
    const view = renderHint(URL_A);

    view.rerender(<EstimateHint url={URL_B} />);

    expect(await screen.findByTestId('estimate-hint')).toHaveTextContent('≈ 930 tokens');
    expect(seen.urls).toEqual([URL_B]);
  });

  it('should hide the hint as soon as the URL is taken away', async () => {
    estimates(() => 840);
    const view = renderHint(URL_A);
    await screen.findByTestId('estimate-hint');

    view.rerender(<EstimateHint url={null} />);

    expect(screen.queryByTestId('estimate-hint')).toBeNull();
  });

  it('should not ask again for a URL it already estimated', async () => {
    const seen = estimates(() => 840);
    const view = renderHint(URL_A);
    await screen.findByTestId('estimate-hint');
    view.rerender(<EstimateHint url={null} />);

    view.rerender(<EstimateHint url={URL_A} />);

    expect(await screen.findByTestId('estimate-hint')).toHaveTextContent('≈ 840 tokens');
    expect(seen.urls).toEqual([URL_A]);
  });

  it.each([
    ['a validation error', 400, { error: 'INVALID_YOUTUBE_URL', message: 'x', statusCode: 400 }],
    ['the hourly limit', 429, { error: 'RATE_LIMITED', message: 'x', statusCode: 429, retryAfterSec: 60 }],
    ['unreadable metadata', 503, { error: 'METADATA_UNAVAILABLE', message: 'x', statusCode: 503 }],
  ])('should stay silent on %s', async (_name, status, body) => {
    const seen: string[] = [];
    server.use(
      http.get(`${API_URL}/videos/estimate`, ({ request }) => {
        seen.push(request.url);
        return HttpResponse.json(body, { status });
      }),
    );

    renderIn(<EstimateHint url={URL_A} />);
    await waitFor(() => expect(seen).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect([screen.queryByTestId('estimate-hint'), screen.queryByRole('alert')]).toEqual([null, null]);
  });

  it('should ignore an answer that is not an estimate', async () => {
    server.use(http.get(`${API_URL}/videos/estimate`, () => HttpResponse.json({ video: { id: 'v1' } })));

    renderIn(<EstimateHint url={URL_A} />);
    await new Promise((resolve) => setTimeout(resolve, 700));

    expect(screen.queryByTestId('estimate-hint')).toBeNull();
  });
});
