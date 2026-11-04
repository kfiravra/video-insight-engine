import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { RunSummary, VideoDetailResponse } from '../lib/api';

const mockForVideo = vi.fn();

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      usage: {
        ...(actual.api?.usage ?? {}),
        forVideo: mockForVideo,
      },
    },
  };
});

async function renderPage(videoId = 'FTha4zARGN4') {
  const { VideoDetailPage } = await import('./VideoDetailPage');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/videos/${videoId}`]}>
        <Routes>
          <Route path="/videos/:videoId" element={<VideoDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const NEWEST_RUN: RunSummary = {
  run_id: 'run-new222',
  request_id: 'req-new222',
  video_id: 'FTha4zARGN4',
  video_summary_id: 'vsid-001',
  user_id: 'user-1',
  first_call: '2026-11-03T12:00:00.000Z',
  last_call: '2026-11-03T12:03:00.000Z',
  total_cost_usd: 0.21,
  call_count: 14,
  regen_ordinal: 2,
  attempt: 1,
  pipeline_version: 'v11',
  bypass: true,
  path: 'worker',
  status: 'completed',
  retry_of: null,
  langfuse_url: 'https://lf.example/project/proj-1/traces/trace-new',
  session_url: 'https://lf.example/project/proj-1/sessions/pipeline-vsid-001',
  degraded: false,
  degraded_reasons: null,
  calls: [
    {
      id: 'call-synth',
      feature: 'synthesis',
      model: 'claude-sonnet-4-6',
      cost_usd: 0.08,
      tokens_in: 9000,
      tokens_out: 1200,
      duration_ms: 8000,
      success: true,
      timestamp: '2026-11-03T12:02:00.000Z',
    },
  ],
};

const OLDER_RUN: RunSummary = {
  ...NEWEST_RUN,
  run_id: 'run-old111',
  request_id: 'req-old111',
  first_call: '2026-11-01T09:00:00.000Z',
  last_call: '2026-11-01T09:04:00.000Z',
  regen_ordinal: 1,
  bypass: false,
  status: 'failed',
  langfuse_url: 'https://lf.example/project/proj-1/traces/trace-old',
  calls: [],
};

function makeDetail(runs: RunSummary[]): VideoDetailResponse {
  return {
    video: {
      title: 'Building a REST API with FastAPI',
      channel: 'Tech Channel',
      duration: 752,
      thumbnail_url: null,
      status: 'completed',
      category: 'coding',
      processed_at: '2026-11-03T12:05:00.000Z',
    },
    summary: {
      total_calls: 14,
      total_cost_usd: 0.25,
      total_tokens_in: 90000,
      total_tokens_out: 12000,
      avg_duration_ms: 2100,
      first_call: '2026-11-01T09:00:00.000Z',
      last_call: '2026-11-03T12:03:00.000Z',
    },
    by_feature: [],
    runs,
  };
}

beforeEach(() => {
  mockForVideo.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('VideoDetailPage', () => {
  it('should load the detail of the video in the URL', async () => {
    mockForVideo.mockResolvedValue(makeDetail([]));
    await renderPage('abc123');
    await waitFor(() => expect(mockForVideo).toHaveBeenCalledWith('abc123'));
  });

  it('should list the video runs in the order the server sends them, newest first', async () => {
    mockForVideo.mockResolvedValue(makeDetail([NEWEST_RUN, OLDER_RUN]));
    await renderPage();
    expect(await screen.findByText('Pipeline Runs (2)')).toBeTruthy();
    const newest = screen.getByText('run-new222');
    const older = screen.getByText('run-old111');
    expect(newest.compareDocumentPosition(older) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('failed')).toBeTruthy();
    expect(screen.getByText('regen #2')).toBeTruthy();
  });

  it('should link each run trace and the row session in Langfuse', async () => {
    mockForVideo.mockResolvedValue(makeDetail([NEWEST_RUN, OLDER_RUN]));
    await renderPage();
    await screen.findByText('run-new222');
    const traces = screen.getAllByRole('link', { name: /^Open trace of run .+ in Langfuse/ });
    expect(traces.map((a) => a.getAttribute('href'))).toEqual([NEWEST_RUN.langfuse_url, OLDER_RUN.langfuse_url]);
    expect(screen.getAllByRole('link', { name: /^Open session of run .+ in Langfuse/ })).toHaveLength(2);
  });

  it('should show a run calls when its row is expanded', async () => {
    mockForVideo.mockResolvedValue(makeDetail([NEWEST_RUN, OLDER_RUN]));
    await renderPage();
    await screen.findByText('run-new222');
    expect(screen.queryByText('synthesis')).toBeNull();

    const [newestToggle] = screen.getAllByRole('button', { name: /expand/i });
    fireEvent.click(newestToggle);

    expect(screen.getByText('synthesis')).toBeTruthy();
    expect(screen.getByText('$0.0800')).toBeTruthy();
  });

  it('should show an empty state when the video has no runs', async () => {
    mockForVideo.mockResolvedValue(makeDetail([]));
    await renderPage();
    expect(await screen.findByText('Pipeline Runs (0)')).toBeTruthy();
    expect(screen.getByText('No pipeline runs recorded for this video in the last 90 days.')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('should not render the old flat calls table', async () => {
    mockForVideo.mockResolvedValue(makeDetail([NEWEST_RUN]));
    await renderPage();
    await screen.findByText('run-new222');
    expect(screen.queryByText(/individual calls/i)).toBeNull();
  });

  it('should show an error state when the detail fails to load', async () => {
    mockForVideo.mockRejectedValue(new Error('boom'));
    await renderPage();
    expect(await screen.findByText('Failed to load video detail')).toBeTruthy();
  });
});
