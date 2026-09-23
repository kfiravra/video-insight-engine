import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../hooks/use-admin-api', () => ({
  usePurgeVideo: vi.fn(),
}));

import { usePurgeVideo } from '../hooks/use-admin-api';
import { PurgeVideoControls } from './PurgeVideoControls';

function mockPurge(value: Partial<ReturnType<typeof usePurgeVideo>> = {}) {
  const mutate = vi.fn();
  vi.mocked(usePurgeVideo).mockReturnValue({
    mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
    data: undefined,
    ...value,
  } as unknown as ReturnType<typeof usePurgeVideo>);
  return mutate;
}

const RESULT = {
  scope: 'global' as const,
  youtubeId: 'dQw4w9WgXcQ',
  summaryIds: ['6a8ebbbfe517dd6fad90fb1d'],
  counts: { videoSummaryCache: 1, userVideos: 3, qdrantPoints: 58, s3Objects: 0 },
  warnings: [] as string[],
};

describe('PurgeVideoControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should ask for confirmation before deleting', () => {
    const mutate = mockPurge();
    render(<PurgeVideoControls videoId="dQw4w9WgXcQ" />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete everywhere…' }));

    expect(screen.getByRole('button', { name: 'Confirm delete' })).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();
  });

  it('should run the purge when confirmed', () => {
    const mutate = mockPurge();
    render(<PurgeVideoControls videoId="dQw4w9WgXcQ" />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete everywhere…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));

    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it('should list the removed counts after a successful purge', () => {
    mockPurge({ isSuccess: true, data: RESULT });
    render(<PurgeVideoControls videoId="dQw4w9WgXcQ" />);

    expect(screen.getByTestId('purge-result').textContent).toContain('videoSummaryCache 1 · userVideos 3 · qdrantPoints 58');
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Deleted everywhere' }).disabled).toBe(true);
  });

  it('should surface summarizer warnings', () => {
    mockPurge({ isSuccess: true, data: { ...RESULT, warnings: ['summarizer: s3: partial'] } });
    render(<PurgeVideoControls videoId="dQw4w9WgXcQ" />);

    expect(screen.getByTestId('purge-result').textContent).toContain('warnings: summarizer: s3: partial');
  });

  it('should show the error state when the purge fails', () => {
    mockPurge({ isError: true, error: new Error('API error: 502') });
    render(<PurgeVideoControls videoId="dQw4w9WgXcQ" />);

    expect(screen.getByText('Delete failed')).toBeTruthy();
  });
});
