import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../hooks/use-admin-api', () => ({
  useQueueDlq: vi.fn(),
  useQueueStats: vi.fn(),
  useReplayDlq: vi.fn(),
}));

import { useQueueDlq, useQueueStats, useReplayDlq } from '../hooks/use-admin-api';
import { DlqPanel } from './DlqPanel';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

function mockDlq(value: Partial<ReturnType<typeof useQueueDlq>>) {
  vi.mocked(useQueueDlq).mockReturnValue(value as ReturnType<typeof useQueueDlq>);
}

function mockStats(dlqMessages: number | undefined) {
  vi.mocked(useQueueStats).mockReturnValue({
    data: dlqMessages === undefined ? undefined : { main: { messages: 0, ready: 0, inFlight: 0, consumers: 1 }, dlq: { messages: dlqMessages } },
  } as unknown as ReturnType<typeof useQueueStats>);
}

function mockReplay(value: Partial<ReturnType<typeof useReplayDlq>> = {}) {
  const mutate = vi.fn();
  vi.mocked(useReplayDlq).mockReturnValue({
    mutate,
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
    data: undefined,
    ...value,
  } as unknown as ReturnType<typeof useReplayDlq>);
  return mutate;
}

const MESSAGE = {
  payload: { youtubeId: 'dQw4w9WgXcQ', videoSummaryId: '6a8ebbbfe517dd6fad90fb1d', requestId: 'req-1', plan: 'paid', attempt: 3 },
  routingKey: 'video.summarize',
  messageId: 'req-1',
  priority: 1,
  headers: { 'x-attempt': 3 },
};

describe('DlqPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStats(undefined);
  });

  it('should render an empty state when the DLQ has no messages', () => {
    mockDlq({ data: { messages: [] }, isLoading: false, isError: false });
    mockReplay();
    render(<DlqPanel />, { wrapper });
    expect(screen.getByText('DLQ is empty')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /replay/i })).toBeNull();
  });

  it('should list dead-lettered jobs with video, request id and attempt', () => {
    mockDlq({ data: { messages: [MESSAGE] }, isLoading: false, isError: false });
    mockReplay();
    render(<DlqPanel />, { wrapper });
    expect(screen.getByText('dQw4w9WgXcQ')).toBeTruthy();
    expect(screen.getByText('req-1')).toBeTruthy();
    expect(screen.getAllByTestId('dlq-row')).toHaveLength(1);
  });

  it('should show what paid for each job, and the tier of a message from before P3.4', () => {
    const legacy = { ...MESSAGE, payload: { ...MESSAGE.payload, plan: undefined, tier: 'pro' } };
    mockDlq({ data: { messages: [MESSAGE, legacy] }, isLoading: false, isError: false });
    mockReplay();
    render(<DlqPanel />, { wrapper });
    expect([screen.getByText('paid'), screen.getByText('pro')].every(Boolean)).toBe(true);
  });

  it('should require a confirmation click before replaying', () => {
    mockDlq({ data: { messages: [MESSAGE, MESSAGE] }, isLoading: false, isError: false });
    const mutate = mockReplay();
    render(<DlqPanel />, { wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'Replay all…' }));
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Replay 2' }));
    expect(mutate).toHaveBeenCalledWith(2, expect.anything());
  });

  it('should replay the real DLQ depth, not just the peeked page', () => {
    mockDlq({ data: { messages: [MESSAGE] }, isLoading: false, isError: false });
    mockStats(137);
    const mutate = mockReplay();
    render(<DlqPanel />, { wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'Replay all…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay 137' }));
    expect(mutate).toHaveBeenCalledWith(137, expect.anything());
  });

  it('should cap one replay at the vie-api batch limit and say so', () => {
    mockDlq({ data: { messages: [MESSAGE] }, isLoading: false, isError: false });
    mockStats(1200);
    const mutate = mockReplay();
    render(<DlqPanel />, { wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'Replay all…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay 500 of 1,200' }));
    expect(mutate).toHaveBeenCalledWith(500, expect.anything());
  });

  it('should disable Cancel while a replay is in flight', () => {
    mockDlq({ data: { messages: [MESSAGE] }, isLoading: false, isError: false });
    mockReplay({ isPending: true });
    render(<DlqPanel />, { wrapper });

    fireEvent.click(screen.getByRole('button', { name: 'Replay all…' }));
    expect((screen.getByRole('button', { name: 'Replaying…' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('should show the replayed count after a successful replay', () => {
    mockDlq({ data: { messages: [MESSAGE] }, isLoading: false, isError: false });
    mockReplay({ isSuccess: true, data: { replayed: 1 } });
    render(<DlqPanel />, { wrapper });
    expect(screen.getByRole('status').textContent).toContain('Replayed 1 message');
  });

  it('should surface a replay failure', () => {
    mockDlq({ data: { messages: [MESSAGE] }, isLoading: false, isError: false });
    mockReplay({ isError: true, error: new Error('QUEUE_DISABLED') });
    render(<DlqPanel />, { wrapper });
    expect(screen.getByRole('alert').textContent).toContain('QUEUE_DISABLED');
  });

  it('should render ErrorState when the DLQ query fails', () => {
    mockDlq({ data: undefined, isLoading: false, isError: true, error: new Error('boom'), refetch: vi.fn() });
    mockReplay();
    render(<DlqPanel />, { wrapper });
    expect(screen.getByTestId('error-state')).toBeTruthy();
  });
});
