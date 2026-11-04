import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
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

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

async function renderPanel() {
  const { PipelineRunsPanel } = await import('./PipelineRunsPanel');
  return render(<PipelineRunsPanel days={7} />, { wrapper: makeWrapper() });
}

/** The tone of the (`index`-th) status pill that reads `label`. */
function pillTone(label: string, index = 0): string | null {
  return (
    screen.getAllByText(label)[index].closest('[data-slot="tone-pill"]')?.getAttribute('data-tone') ?? null
  );
}

const LEGACY_FIELDS = {
  attempt: null,
  pipeline_version: null,
  bypass: null,
  path: null,
  status: null,
  retry_of: null,
  langfuse_url: null,
  session_url: null,
} satisfies Partial<RunSummary>;

const RUN_1: RunSummary = {
  run_id: 'run-aaa111',
  request_id: 'req-aaa111',
  video_id: 'ytid-abc',
  video_summary_id: 'vsid-001',
  user_id: 'user-1',
  first_call: '2026-06-01T10:00:00.000Z',
  last_call: '2026-06-01T10:02:00.000Z',
  total_cost_usd: 0.0125,
  call_count: 5,
  regen_ordinal: 1,
  attempt: 1,
  pipeline_version: 'v11',
  bypass: false,
  path: 'worker',
  status: 'completed',
  retry_of: null,
  langfuse_url: 'https://cloud.langfuse.com/project/proj-1/traces/trace-aaa',
  session_url: 'https://cloud.langfuse.com/project/proj-1/sessions/pipeline-vsid-001',
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

/** A retry of RUN_1's request: same request id, its own run id. */
const RETRY_RUN: RunSummary = {
  ...RUN_1,
  run_id: '3f2a9b1c-5d6e-4f70-8a9b-0c1d2e3fe7d0',
  first_call: '2026-06-01T10:05:00.000Z',
  attempt: 2,
  bypass: true,
  path: 'sse',
  status: 'failed',
  retry_of: '9c8b7a6d-1e2f-4a3b-8c4d-5e6f7a8b0c1d',
  langfuse_url: 'https://cloud.langfuse.com/project/proj-1/traces/trace-retry',
  calls: [
    {
      id: 'call-retry',
      feature: 'extract',
      model: 'claude-sonnet-4-6',
      cost_usd: 0.004,
      tokens_in: 900,
      tokens_out: 100,
      duration_ms: 900,
      success: false,
      timestamp: '2026-06-01T10:05:30.000Z',
    },
  ],
};

const RUN_2: RunSummary = {
  ...RUN_1,
  run_id: 'run-bbb222',
  request_id: 'req-bbb222',
  video_summary_id: 'vsid-002',
  first_call: '2026-06-02T09:00:00.000Z',
  last_call: '2026-06-02T09:03:00.000Z',
  total_cost_usd: 0.0095,
  call_count: 4,
  regen_ordinal: 2,
  calls: [],
};

/** Written before run ids: grouped by its request id, no runs[] entry fields. */
const REQUEST_KEYED_RUN: RunSummary = {
  ...RUN_1,
  ...LEGACY_FIELDS,
  run_id: null,
  request_id: 'req-old111',
  regen_ordinal: null,
  calls: [],
};

const RUN_WITH_AUDIO: RunSummary = {
  ...RUN_1,
  run_id: 'run-ccc333',
  request_id: 'req-ccc333',
  video_id: 'ytid-xyz',
  video_summary_id: 'vsid-003',
  total_cost_usd: 0.36,
  call_count: 2,
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
  ...LEGACY_FIELDS,
  run_id: null,
  request_id: null,
  video_id: null,
  video_summary_id: null,
  user_id: null,
  first_call: '2026-06-01T08:00:00.000Z',
  last_call: '2026-06-01T08:01:00.000Z',
  total_cost_usd: 0.001,
  call_count: 1,
  regen_ordinal: null,
  calls: [],
};

beforeEach(() => {
  mockByRun.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('PipelineRunsPanel', () => {
  it('should show loading skeleton while fetching', async () => {
    mockByRun.mockReturnValue(new Promise(() => {})); // never resolves
    await renderPanel();
    expect(document.querySelector('[data-slot="skeleton-panel"]')).toBeTruthy();
  });

  it('should request the selected window', async () => {
    mockByRun.mockResolvedValue([]);
    await renderPanel();
    await waitFor(() => expect(mockByRun).toHaveBeenCalledWith(7, 20, 0));
  });

  it('should show an error state when the request fails', async () => {
    mockByRun.mockRejectedValue(new Error('boom'));
    await renderPanel();
    expect(await screen.findByText('Failed to load pipeline runs')).toBeTruthy();
  });

  it('should label each row by its run id when the run has one', async () => {
    mockByRun.mockResolvedValue([RUN_1, RUN_2]);
    await renderPanel();
    expect(await screen.findByText('run-aaa111')).toBeTruthy();
    expect(screen.getByText('run-bbb222')).toBeTruthy();
    expect(screen.queryByText('req-aaa111')).toBeNull();
  });

  it('should shorten a long run id and keep the full id in the title', async () => {
    mockByRun.mockResolvedValue([RETRY_RUN]);
    await renderPanel();
    const id = await screen.findByText('3f2a9b…e7d0');
    expect(id.getAttribute('title')).toContain(RETRY_RUN.run_id);
  });

  it('should fall back to the request id when a legacy row has no run id', async () => {
    mockByRun.mockResolvedValue([REQUEST_KEYED_RUN]);
    await renderPanel();
    expect(await screen.findByText('req-old111')).toBeTruthy();
  });

  it('should show "unattributed (legacy)" when a row has neither id', async () => {
    mockByRun.mockResolvedValue([UNATTRIBUTED_RUN]);
    await renderPanel();
    expect(await screen.findByText('unattributed (legacy)')).toBeTruthy();
  });

  it('should render the attempt, version, bypass and path of a run', async () => {
    mockByRun.mockResolvedValue([RETRY_RUN]);
    await renderPanel();
    await screen.findByText('3f2a9b…e7d0');
    for (const header of ['Attempt', 'Version', 'Bypass', 'Path', 'Status', 'Cost', 'Calls', 'Langfuse']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeTruthy();
    }
    expect(screen.getByText('#2')).toBeTruthy();
    expect(screen.getByText('v11')).toBeTruthy();
    expect(screen.getByText('yes')).toBeTruthy();
    expect(screen.getByText('sse')).toBeTruthy();
  });

  it('should render dashes for the attempt fields of a legacy row', async () => {
    mockByRun.mockResolvedValue([REQUEST_KEYED_RUN]);
    await renderPanel();
    await screen.findByText('req-old111');
    // attempt, version, bypass, path, status
    expect(screen.getAllByText('—')).toHaveLength(5);
  });

  it.each([
    ['completed', 'completed', 'success'],
    ['failed', 'failed', 'danger'],
    ['running', 'running', 'warning'],
    ['dispatched', 'dispatched', 'warning'],
    ['cache_hit', 'cache hit', 'neutral'],
    ['requeued', 'requeued', 'neutral'],
  ])('should tone the %s status badge', async (status, label, tone) => {
    mockByRun.mockResolvedValue([{ ...RUN_1, status }]);
    await renderPanel();
    await screen.findByText('run-aaa111');
    expect(pillTone(label)).toBe(tone);
  });

  it('should name the run a retry continues', async () => {
    mockByRun.mockResolvedValue([RETRY_RUN]);
    await renderPanel();
    const retry = await screen.findByText(/retry of/i);
    expect(retry.textContent).toBe('retry of 9c8b7a…0c1d');
    expect(retry.getAttribute('title')).toContain(RETRY_RUN.retry_of);
  });

  it('should not show a retry line for a first attempt', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    await renderPanel();
    await screen.findByText('run-aaa111');
    expect(screen.queryByText(/retry of/i)).toBeNull();
  });

  it('should link the run trace and the row session in Langfuse', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    await renderPanel();
    const trace = await screen.findByRole('link', { name: /^Open trace of run .+ in Langfuse/ });
    expect(trace.getAttribute('href')).toBe(RUN_1.langfuse_url);
    expect(trace.getAttribute('target')).toBe('_blank');
    const session = screen.getByRole('link', { name: /^Open session of run .+ in Langfuse/ });
    expect(session.getAttribute('href')).toBe(RUN_1.session_url);
  });

  it('should render no Langfuse links when the server sends none', async () => {
    mockByRun.mockResolvedValue([REQUEST_KEYED_RUN, UNATTRIBUTED_RUN]);
    await renderPanel();
    await screen.findByText('req-old111');
    expect(screen.queryAllByRole('link', { name: /in Langfuse/ })).toHaveLength(0);
  });

  it('should show a regen badge for regen_ordinal > 1', async () => {
    mockByRun.mockResolvedValue([RUN_2]);
    await renderPanel();
    expect(await screen.findByText('regen #2')).toBeTruthy();
  });

  it('should NOT show a regen badge for regen_ordinal === 1', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    await renderPanel();
    await screen.findByText('run-aaa111');
    expect(screen.queryByText(/regen/i)).toBeNull();
  });

  it('should show a degraded badge when the run doc is flagged degraded', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: true }]);
    await renderPanel();
    expect(await screen.findByText(/degraded/i)).toBeTruthy();
  });

  it('should name a failed media download on the degraded badge', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: true, degraded_reasons: ['media_download_failed'] }]);
    await renderPanel();
    expect(await screen.findByText(/degraded · media/i)).toBeTruthy();
  });

  it('should NOT show a degraded badge for clean or unresolved runs', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: false }, UNATTRIBUTED_RUN]);
    await renderPanel();
    await screen.findByText('unattributed (legacy)');
    expect(screen.queryByText(/degraded/i)).toBeNull();
  });

  it('should expand call details when a row is clicked', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    await renderPanel();
    await screen.findByText('run-aaa111');

    const expandBtn = screen.getByRole('button', { name: /expand/i });
    fireEvent.click(expandBtn);

    expect(screen.getByText('plan')).toBeTruthy();
    expect(expandBtn.getAttribute('aria-expanded')).toBe('true');
  });

  it('should link each run to its video, since the Usage page mixes videos', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    await renderPanel();

    const link = await screen.findByRole('link', { name: 'ytid-abc' });
    expect(link.getAttribute('href')).toBe('/videos/ytid-abc');
  });

  it('should list a failed call first and mark it failed', async () => {
    const run: RunSummary = {
      ...RUN_1,
      status: 'failed',
      calls: [RUN_1.calls[0], { ...RETRY_RUN.calls[0], id: 'call-broken', feature: 'extract' }],
    };
    mockByRun.mockResolvedValue([run]);
    await renderPanel();
    await screen.findByText('run-aaa111');

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    const features = screen.getAllByText(/^(plan|extract)$/).map((el) => el.textContent);
    expect(features).toEqual(['extract', 'plan']);
    expect(pillTone('failed', 1)).toBe('danger');
  });

  it('should collapse call details when expanded row is clicked again', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    await renderPanel();
    await screen.findByText('run-aaa111');

    const expandBtn = screen.getByRole('button', { name: /expand/i });
    fireEvent.click(expandBtn); // open
    fireEvent.click(expandBtn); // close
    expect(screen.queryByText('plan')).toBeNull();
  });

  it('should show the full run, request and retried run ids in the expanded row', async () => {
    mockByRun.mockResolvedValue([RETRY_RUN]);
    const { container } = await renderPanel();
    await screen.findByText('3f2a9b…e7d0');

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    const details = container.querySelector('[data-slot="run-details"]');
    expect(details).not.toBeNull();
    expect(details?.textContent).toContain(`Run${RETRY_RUN.run_id}`);
    expect(details?.textContent).toContain(`Request${RETRY_RUN.request_id}`);
    expect(details?.textContent).toContain(`Retry of${RETRY_RUN.retry_of}`);
  });

  it('should spell out why a run is degraded in the expanded row', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: true, degraded_reasons: ['media_download_failed'] }]);
    const { container } = await renderPanel();
    await screen.findByText(/degraded · media/i);

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    const details = container.querySelector('[data-slot="run-details"]');
    expect(details?.textContent).toContain('media: a video download failed');
  });

  it('should explain a degraded row without reasons as a partial extraction', async () => {
    mockByRun.mockResolvedValue([{ ...RUN_1, degraded: true, degraded_reasons: null }]);
    const { container } = await renderPanel();
    await screen.findByText('degraded');

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    const details = container.querySelector('[data-slot="run-details"]');
    expect(details?.textContent).toContain('partial: an extraction field group failed');
  });

  it('should show no details block for the unattributed bucket', async () => {
    mockByRun.mockResolvedValue([UNATTRIBUTED_RUN]);
    const { container } = await renderPanel();
    await screen.findByText('unattributed (legacy)');

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    expect(container.querySelector('[data-slot="run-details"]')).toBeNull();
  });

  it('should show the run age under its id below lg, where the Started column hides', async () => {
    mockByRun.mockResolvedValue([RUN_1]);
    const { container } = await renderPanel();
    await screen.findByText('run-aaa111');

    const age = container.querySelector(`time[datetime="${RUN_1.first_call}"]`);
    expect(age?.textContent).toMatch(/ago$/);
    expect(age?.className).toContain('lg:hidden');
  });

  it('should expand one attempt only when two attempts share a request id', async () => {
    mockByRun.mockResolvedValue([RETRY_RUN, RUN_1]);
    await renderPanel();
    await screen.findByText('run-aaa111');

    const [retryToggle] = screen.getAllByRole('button', { name: /expand/i });
    fireEvent.click(retryToggle);

    expect(screen.getByText('extract')).toBeTruthy();
    expect(screen.queryByText('plan')).toBeNull();
  });

  it('should render audio-unit calls as "N min audio" instead of tokens', async () => {
    mockByRun.mockResolvedValue([RUN_WITH_AUDIO]);
    await renderPanel();
    await screen.findByText('run-ccc333');

    fireEvent.click(screen.getByRole('button', { name: /expand/i }));

    // 3600s / 60 = 60.0 min audio — not "0" tokens.
    expect(screen.getByText(/60\.0 min audio/i)).toBeTruthy();
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it('should show empty state when no runs are returned', async () => {
    mockByRun.mockResolvedValue([]);
    await renderPanel();
    expect(await screen.findByText(/no pipeline runs/i)).toBeTruthy();
  });
});
