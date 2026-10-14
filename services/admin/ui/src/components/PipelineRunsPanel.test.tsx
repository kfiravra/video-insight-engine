import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { RunSummary } from '../lib/api';

// Mock api module
const mockByRun = vi.fn();

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      usage: {
        ...(actual.api?.usage ?? {}),
        byRun: mockByRun,
      },
    },
  };
});

vi.mock('../lib/langfuse', () => ({
  buildLangfuseTraceUrl: () => null,
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const RUN_1: RunSummary = {
  request_id: 'req-aaa111',
  video_id: 'ytid-abc',
  video_summary_id: 'vsid-001',
  user_id: 'user-1',
  first_call: '2026-06-01T10:00:00.000Z',
  last_call: '2026-06-01T10:02:00.000Z',
  total_cost_usd: 0.0125,
  call_count: 5,
  regen_ordinal: 1,
  langfuse_url: 'https://cloud.langfuse.com/project/proj-1/traces/trace-aaa',
  calls: [
    {
      id: 'call-1',
      feature: 'plan',
      model: 'claude-3-5-sonnet',
      cost_usd: 0.005,
      tokens_in: 1000,
      tokens_out: 200,
      duration_ms: 1200,
      success: true,
      timestamp: '2026-06-01T10:00:30.000Z',
    },
  ],
};

const RUN_2: RunSummary = {
  request_id: 'req-bbb222',
  video_id: 'ytid-abc',
  video_summary_id: 'vsid-002',
  user_id: 'user-1',
  first_call: '2026-06-02T09:00:00.000Z',
  last_call: '2026-06-02T09:03:00.000Z',
  total_cost_usd: 0.0095,
  call_count: 4,
  regen_ordinal: 2,
  langfuse_url: null,
  calls: [],
};

const RUN_WITH_AUDIO: RunSummary = {
  request_id: 'req-ccc333',
  video_id: 'ytid-xyz',
  video_summary_id: 'vsid-003',
  user_id: 'user-2',
  first_call: '2026-06-03T11:00:00.000Z',
  last_call: '2026-06-03T11:05:00.000Z',
  total_cost_usd: 0.36,
  call_count: 2,
  regen_ordinal: 1,
  langfuse_url: null,
  calls: [
    {
      id: 'call-audio',
      feature: 'summarize:transcript:whisper',
      model: 'whisper-1',
      cost_usd: 0.36,
      tokens_in: 0,
      tokens_out: 0,
      duration_ms: 30000,
      success: true,
      timestamp: '2026-06-03T11:00:30.000Z',
      unit: 'audio_seconds',
      audio_seconds: 3600,
    },
  ],
};

const UNATTRIBUTED_RUN: RunSummary = {
  request_id: null,
  video_id: null,
  video_summary_id: null,
  user_id: null,
  first_call: '2026-06-01T08:00:00.000Z',
  last_call: '2026-06-01T08:01:00.000Z',
  total_cost_usd: 0.001,
  call_count: 1,
  regen_ordinal: null,
  langfuse_url: null,
  calls: [],
};

beforeEach(() => {
  mockByRun.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('PipelineRunsPanel', () => {
  it('should export PipelineRunsPanel component', async () => {
    const mod = await import('./PipelineRunsPanel');
    expect(typeof mod.PipelineRunsPanel).toBe('function');
  });

  it('should show loading skeleton while fetching', async () => {
    mockByRun.mockReturnValue(new Promise(() => {})); // never resolves
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    // SkeletonPanel renders with data-slot="skeleton-panel"
    expect(document.querySelector('[data-slot="skeleton-panel"]')).toBeTruthy();
  });

  it('should render run rows when data is available', async () => {
    mockByRun.mockResolvedValue([RUN_1, RUN_2]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/req-aaa111/i)).toBeTruthy();
    });
    expect(screen.getByText(/req-bbb222/i)).toBeTruthy();
  });

  it('should render the server-resolved Langfuse link when langfuse_url is set', async () => {
    // buildLangfuseTraceUrl is mocked to null, so a link can only come from the
    // backend-provided run.langfuse_url.
    mockByRun.mockResolvedValue([RUN_1]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    const link = await screen.findByRole('link', { name: /open in langfuse/i });
    expect(link.getAttribute('href')).toBe(RUN_1.langfuse_url);
  });

  it('should not render a Langfuse link when langfuse_url is null and no fallback', async () => {
    mockByRun.mockResolvedValue([RUN_2]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => screen.getByText(/req-bbb222/i));
    expect(screen.queryByRole('link', { name: /open in langfuse/i })).toBeNull();
  });

  it('should show "unattributed (legacy)" for null request_id', async () => {
    mockByRun.mockResolvedValue([UNATTRIBUTED_RUN]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/unattributed/i)).toBeTruthy();
    });
  });

  it('should show a regen badge for regen_ordinal > 1', async () => {
    mockByRun.mockResolvedValue([RUN_2]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/regen/i)).toBeTruthy();
    });
  });

  it('should NOT show a regen badge for regen_ordinal === 1', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.queryByText(/regen/i)).toBeNull();
    });
  });

  it('should show a degraded badge when the run doc is flagged degraded', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: true }]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/degraded/i)).toBeTruthy();
    });
  });

  it('should name a failed media download on the degraded badge', async () => {
    mockByRun.mockResolvedValue([
      { ...RUN_1, degraded: true, degraded_reasons: ['media_download_failed'] },
    ]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/degraded · media/i)).toBeTruthy();
    });
  });

  it('should NOT show a degraded badge for clean or unresolved runs', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: false }, UNATTRIBUTED_RUN]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText('unattributed (legacy)')).toBeTruthy();
    });
    expect(screen.queryByText(/degraded/i)).toBeNull();
  });

  it('should expand call details when a row is clicked', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => screen.getByText(/req-aaa111/i));

    // find expand button
    const expandBtn = screen.getByRole('button', { name: /expand/i });
    fireEvent.click(expandBtn);

    // The call details panel should now be visible (feature "plan")
    expect(screen.getByText('plan')).toBeTruthy();
  });

  it('should collapse call details when expanded row is clicked again', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => screen.getByText(/req-aaa111/i));

    const expandBtn = screen.getByRole('button', { name: /expand/i });
    fireEvent.click(expandBtn); // open
    fireEvent.click(expandBtn); // close
    expect(screen.queryByText('plan')).toBeNull();
  });

  it('should render audio-unit calls as "N min audio" instead of tokens', async () => {
    mockByRun.mockResolvedValue([RUN_WITH_AUDIO]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => screen.getByText(/req-ccc333/i));

    // Expand to reveal the transcription call row.
    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    // 3600s / 60 = 60.0 min audio — not "0" tokens.
    expect(screen.getByText(/60\.0 min audio/i)).toBeTruthy();
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it('should show empty state when no runs are returned', async () => {
    mockByRun.mockResolvedValue([]);
    const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
    const wrapper = makeWrapper();
    render(<PipelineRunsPanel days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/no pipeline runs/i)).toBeTruthy();
    });
  });
});
