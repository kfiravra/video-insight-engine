import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { VideoSummaryItem } from '../lib/api';

vi.mock('../hooks/use-admin-api', () => ({
  useUsageByVideo: vi.fn(),
  useVideoDetail: vi.fn(),
}));

import { useUsageByVideo, useVideoDetail } from '../hooks/use-admin-api';
import { VideosPage } from './VideosPage';

const wrapper = ({ children, initialPath = '/videos' }: { children: React.ReactNode; initialPath?: string }) => (
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter initialEntries={[initialPath]}>{children}</MemoryRouter>
  </QueryClientProvider>
);

function makeVideo(overrides: Partial<VideoSummaryItem> = {}): VideoSummaryItem {
  return {
    video_id: 'v1',
    calls: 3,
    cost_usd: 1.5,
    tokens_in: 1000,
    tokens_out: 500,
    first_call: null,
    last_call: '2026-04-20T00:00:00Z',
    title: 'Hello World',
    channel: 'Test',
    duration: 120,
    thumbnail_url: null,
    status: 'completed',
    category: null,
    processed_at: null,
    ...overrides,
  };
}

describe('VideosPage', () => {
  it('should export VideosPage component', () => {
    expect(typeof VideosPage).toBe('function');
  });

  it('should import formatDateTime from format module (tz-aware timestamps)', async () => {
    // Verify the format module exports formatDateTime (used in the page)
    const mod = await import('../lib/format');
    expect(typeof mod.formatDateTime).toBe('function');
  });

  it('should filter table rows by title substring', () => {
    vi.mocked(useUsageByVideo).mockReturnValue({
      data: [
        makeVideo({ video_id: 'v1', title: 'React Hooks Tutorial' }),
        makeVideo({ video_id: 'v2', title: 'Python FastAPI' }),
      ],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUsageByVideo>);
    vi.mocked(useVideoDetail).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useVideoDetail>);

    render(<VideosPage />, { wrapper });

    expect(screen.getByText('React Hooks Tutorial')).toBeTruthy();
    expect(screen.getByText('Python FastAPI')).toBeTruthy();

    fireEvent.change(screen.getByTestId('videos-filter'), { target: { value: 'python' } });

    expect(screen.queryByText('React Hooks Tutorial')).toBeNull();
    expect(screen.getByText('Python FastAPI')).toBeTruthy();
  });

  it('should render error state when query fails', () => {
    vi.mocked(useUsageByVideo).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('fetch failed'),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUsageByVideo>);
    vi.mocked(useVideoDetail).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useVideoDetail>);

    render(<VideosPage />, { wrapper });
    expect(screen.getByTestId('error-state')).toBeTruthy();
  });

  it('should render sortable headers for Cost, Calls, Tokens, Video, Last Active', () => {
    vi.mocked(useUsageByVideo).mockReturnValue({
      data: [makeVideo()],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUsageByVideo>);
    vi.mocked(useVideoDetail).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useVideoDetail>);

    render(<VideosPage />, { wrapper });
    expect(screen.getByTestId('sort-cost_usd')).toBeTruthy();
    expect(screen.getByTestId('sort-calls')).toBeTruthy();
    expect(screen.getByTestId('sort-tokens')).toBeTruthy();
    expect(screen.getByTestId('sort-title')).toBeTruthy();
    expect(screen.getByTestId('sort-last_call')).toBeTruthy();
  });

  it('should preserve insertion order for rows tied on the sort key across direction toggles', () => {
    vi.mocked(useUsageByVideo).mockReturnValue({
      data: [
        makeVideo({ video_id: 'alpha', title: 'Alpha Vid', cost_usd: 5 }),
        makeVideo({ video_id: 'bravo', title: 'Bravo Vid', cost_usd: 5 }),
        makeVideo({ video_id: 'charlie', title: 'Charlie Vid', cost_usd: 3 }),
      ],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUsageByVideo>);
    vi.mocked(useVideoDetail).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useVideoDetail>);

    render(<VideosPage />, { wrapper });

    const readOrder = () => {
      const alpha = screen.getByTestId('expand-alpha');
      const bravo = screen.getByTestId('expand-bravo');
      // Node.compareDocumentPosition returns bitmask; FOLLOWING (4) means alpha precedes bravo
      const mask = alpha.compareDocumentPosition(bravo);
      return (mask & Node.DOCUMENT_POSITION_FOLLOWING) !== 0 ? 'alpha-first' : 'bravo-first';
    };

    // Initial state: sort key is cost_usd, direction is desc (default).
    // Tied rows alpha & bravo should retain insertion order (alpha first).
    const initialOrder = readOrder();
    expect(initialOrder).toBe('alpha-first');

    // Toggle cost header — direction flips to asc.
    fireEvent.click(screen.getByTestId('sort-cost_usd'));
    // Tied rows must STILL be in insertion order (alpha before bravo).
    const afterToggleOrder = readOrder();
    expect(afterToggleOrder).toBe('alpha-first');
  });

  it("should keep a chat-only video's cost pills when it has no pipeline calls", () => {
    vi.mocked(useUsageByVideo).mockReturnValue({
      data: [makeVideo({ video_id: 'v1', title: 'Chat only' })],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUsageByVideo>);
    vi.mocked(useVideoDetail).mockReturnValue({
      data: {
        video: null,
        summary: {},
        by_feature: [{ feature: 'assistant:rag_chat', calls: 4, cost_usd: 0.002 }],
        runs: [],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useVideoDetail>);

    render(<VideosPage />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));

    expect(screen.getByText('assistant:rag_chat')).toBeTruthy();
    expect(screen.getByText('No pipeline calls in the last 90 days.')).toBeTruthy();
  });

  it('should list at most 50 calls in the drawer', () => {
    const calls = Array.from({ length: 60 }, (_, i) => ({
      id: `call-${i}`,
      feature: `feature-${i}`,
      model: 'm',
      cost_usd: 0.001,
      tokens_in: 1,
      tokens_out: 1,
      duration_ms: 1,
      success: true,
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, i)).toISOString(),
    }));
    vi.mocked(useUsageByVideo).mockReturnValue({
      data: [makeVideo({ video_id: 'v1', title: 'Busy video' })],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUsageByVideo>);
    vi.mocked(useVideoDetail).mockReturnValue({
      data: { video: null, summary: {}, by_feature: [], runs: [{ calls }] },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useVideoDetail>);

    render(<VideosPage />, { wrapper });
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));

    expect(screen.getAllByText(/^feature-\d+$/)).toHaveLength(50);
    expect(screen.getByText('feature-59')).toBeTruthy();
    expect(screen.queryByText('feature-0')).toBeNull();
  });
});
