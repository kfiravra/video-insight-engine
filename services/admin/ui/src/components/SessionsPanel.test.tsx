import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type { SessionSummary } from '../lib/api';

const mockBySession = vi.fn();

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      usage: {
        ...(actual.api?.usage ?? {}),
        bySession: mockBySession,
      },
    },
  };
});

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

async function renderPanel() {
  const { SessionsPanel } = await import('./SessionsPanel');
  return render(<SessionsPanel days={7} />, { wrapper: makeWrapper() });
}

const VIDEO_SESSION: SessionSummary = {
  session_id: '5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e',
  user_id: 'user-1',
  video_summary_id: 'vsid-001',
  turns: 3,
  tool_llm_calls: 2,
  call_count: 7,
  total_cost_usd: 0.0412,
  first_call: '2026-11-03T10:00:00.000Z',
  last_call: '2026-11-03T10:06:00.000Z',
  session_url: 'https://cloud.langfuse.com/project/proj-1/sessions/chat-5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e',
};

const LIBRARY_SESSION: SessionSummary = {
  ...VIDEO_SESSION,
  session_id: 'sess-lib01',
  video_summary_id: null,
  turns: 1,
  tool_llm_calls: 0,
  call_count: 1,
  total_cost_usd: 0.0031,
  session_url: null,
};

const LEGACY_BUCKET: SessionSummary = {
  ...VIDEO_SESSION,
  session_id: null,
  user_id: null,
  turns: 12,
  tool_llm_calls: 4,
  call_count: 40,
  total_cost_usd: 0.251,
  session_url: null,
};

beforeEach(() => {
  mockBySession.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('SessionsPanel', () => {
  it('should show loading skeleton while fetching', async () => {
    mockBySession.mockReturnValue(new Promise(() => {}));
    await renderPanel();
    expect(document.querySelector('[data-slot="skeleton-panel"]')).toBeTruthy();
  });

  it('should request the selected window', async () => {
    mockBySession.mockResolvedValue([]);
    await renderPanel();
    await waitFor(() => expect(mockBySession).toHaveBeenCalledWith(7, 20, 0));
  });

  it('should show an error state when the request fails', async () => {
    mockBySession.mockRejectedValue(new Error('boom'));
    await renderPanel();
    expect(await screen.findByText('Failed to load chat sessions')).toBeTruthy();
  });

  it('should show empty state when no sessions are returned', async () => {
    mockBySession.mockResolvedValue([]);
    await renderPanel();
    expect(await screen.findByText('No chat sessions found in the last 7 days.')).toBeTruthy();
  });

  it('should render one row per session with its short id, counts and cost', async () => {
    mockBySession.mockResolvedValue([VIDEO_SESSION]);
    await renderPanel();
    const id = await screen.findByText('5b1e2c…3d4e');
    expect(id.getAttribute('title')).toContain(VIDEO_SESSION.session_id);
    for (const header of ['Session', 'Surface', 'Turns', 'Tool LLM calls', 'Calls', 'Cost', 'Last activity', 'Langfuse']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeTruthy();
    }
    const cells = screen.getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells).toEqual(expect.arrayContaining(['3', '2', '7', '$0.0412']));
  });

  it('should mark a session with a summary id as the video chat', async () => {
    mockBySession.mockResolvedValue([VIDEO_SESSION]);
    await renderPanel();
    expect(await screen.findByText('video')).toBeTruthy();
    expect(screen.queryByText('library')).toBeNull();
  });

  it('should mark a session without a summary id as the library chat', async () => {
    mockBySession.mockResolvedValue([LIBRARY_SESSION]);
    await renderPanel();
    expect(await screen.findByText('library')).toBeTruthy();
    expect(screen.queryByText('video')).toBeNull();
  });

  it('should show "unattributed (legacy)" and no surface for the bucket without a session id', async () => {
    mockBySession.mockResolvedValue([LEGACY_BUCKET]);
    await renderPanel();
    expect(await screen.findByText('unattributed (legacy)')).toBeTruthy();
    expect(screen.queryByText('video')).toBeNull();
    expect(screen.queryByText('library')).toBeNull();
  });

  it('should link the session in Langfuse', async () => {
    mockBySession.mockResolvedValue([VIDEO_SESSION]);
    await renderPanel();
    const link = await screen.findByRole('link', { name: /^Open chat session .+ in Langfuse/ });
    expect(link.getAttribute('href')).toBe(VIDEO_SESSION.session_url);
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('should render no link when the server sends none', async () => {
    mockBySession.mockResolvedValue([LIBRARY_SESSION, LEGACY_BUCKET]);
    await renderPanel();
    await screen.findByText('sess-lib01');
    expect(screen.queryAllByRole('link')).toHaveLength(0);
  });
});
