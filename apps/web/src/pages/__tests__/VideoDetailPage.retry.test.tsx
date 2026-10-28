import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ApiError } from '@/api/client';

const mocks = vi.hoisted(() => ({
  video: { current: null as Record<string, unknown> | null },
  refetch: vi.fn(),
  retryMutate: vi.fn(),
  retryState: { error: null as unknown, isPending: false },
  streamRetry: vi.fn(),
  streamErrorCode: { current: null as string | null },
}));

vi.mock('@/hooks/use-videos', () => ({
  useVideo: () => ({
    data: mocks.video.current,
    isLoading: false,
    error: null,
    refetch: mocks.refetch,
  }),
  useRetryVideo: () => ({ mutate: mocks.retryMutate, ...mocks.retryState }),
}));

vi.mock('@/features/video-output/hooks/use-summary-stream', () => ({
  useSummaryStream: () => ({
    metadata: null,
    duration: null,
    tabs: [],
    finalTabIds: [],
    replacedTabIds: [],
    meta: null,
    synthesis: null,
    tabCount: 0,
    tabLabels: [],
    phase: 'error',
    phaseDetail: null,
    extractionProgress: null,
    triage: null,
    frames: [],
    warnings: [],
    error: 'Connection issue. Please check your internet and try again.',
    errorCode: mocks.streamErrorCode.current,
    confettiCount: 0,
    degraded: false,
    stop: vi.fn(),
    retry: mocks.streamRetry,
  }),
}));

vi.mock('@/components/layout/Layout', () => ({
  Layout: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

// The real router has dozens of tabs; the page only hands it the two retry props.
vi.mock('@/features/video-output/components/OutputRouter', () => ({
  OutputRouter: ({
    onRetryStream,
    retryingStream,
    streamErrorCode,
  }: {
    onRetryStream?: () => void;
    retryingStream?: boolean;
    streamErrorCode?: string | null;
  }) => (
    <button type="button" onClick={onRetryStream} disabled={retryingStream} data-error-code={streamErrorCode ?? ''}>
      Retry stream
    </button>
  ),
}));

import { VideoDetailPage } from '../VideoDetailPage';

const VIDEO = {
  id: 'video-1',
  videoSummaryId: 'summary-1',
  youtubeId: 'dQw4w9WgXcQ',
  folderId: 'folder-1',
  title: 'A video',
  status: 'processing',
  tabs: [],
  meta: null,
};

function setVideo(overrides: Record<string, unknown> = {}) {
  mocks.video.current = { ...VIDEO, ...overrides };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/video/video-1']}>
      <Routes>
        <Route path="/video/:id" element={<VideoDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** What a re-read of the video record answers when Retry checks its state. */
function refetchAnswers(status: string | undefined) {
  mocks.refetch.mockResolvedValue({ data: status === undefined ? undefined : { ...VIDEO, status } });
}

describe('VideoDetailPage retry', () => {
  beforeEach(() => {
    mocks.refetch.mockReset().mockResolvedValue({ data: undefined });
    mocks.retryMutate.mockReset();
    mocks.streamRetry.mockReset();
    mocks.retryState.error = null;
    mocks.retryState.isPending = false;
    mocks.streamErrorCode.current = null;
    setVideo();
  });

  describe('Retry on the stream-error card', () => {
    it('should go through the billed retry, not the stream, when the video has failed', async () => {
      refetchAnswers('failed');
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.retryMutate).toHaveBeenCalledOnce());
      expect(mocks.retryMutate.mock.calls[0][0]).toEqual({ youtubeId: 'dQw4w9WgXcQ', folderId: 'folder-1' });
      expect(mocks.streamRetry).not.toHaveBeenCalled();
    });

    it('should read the state of the video before deciding', async () => {
      refetchAnswers('failed');
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.refetch).toHaveBeenCalledOnce());
    });

    it('should reattach to the stream, with no new submit, when the run is still going', async () => {
      refetchAnswers('processing');
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.streamRetry).toHaveBeenCalledOnce());
      expect(mocks.retryMutate).not.toHaveBeenCalled();
    });

    it('should reattach when the run has not started yet', async () => {
      refetchAnswers('pending');
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.streamRetry).toHaveBeenCalledOnce());
    });

    it('should do neither when the run has completed, since the output swaps in by itself', async () => {
      refetchAnswers('completed');
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.refetch).toHaveBeenCalled());
      expect(mocks.retryMutate).not.toHaveBeenCalled();
      expect(mocks.streamRetry).not.toHaveBeenCalled();
    });

    it('should reattach when the state cannot be read, and never submit blindly', async () => {
      refetchAnswers(undefined);
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.streamRetry).toHaveBeenCalledOnce());
      expect(mocks.retryMutate).not.toHaveBeenCalled();
    });

    it('should hand the stream error code to the output so the card can offer the billed retry', () => {
      mocks.streamErrorCode.current = 'VIDEO_FAILED';
      renderPage();

      expect(screen.getByRole('button', { name: 'Retry stream' }).getAttribute('data-error-code')).toBe('VIDEO_FAILED');
    });

    it('should send VIDEO_FAILED through the billed retry when Retry is pressed', async () => {
      mocks.streamErrorCode.current = 'VIDEO_FAILED';
      refetchAnswers('failed');
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: 'Retry stream' }));

      await waitFor(() => expect(mocks.retryMutate).toHaveBeenCalledOnce());
      expect(mocks.streamRetry).not.toHaveBeenCalled();
    });

    it('should disable the button while a retry is being submitted', () => {
      mocks.retryState.isPending = true;
      renderPage();

      expect((screen.getByRole('button', { name: 'Retry stream' }) as HTMLButtonElement).disabled).toBe(true);
    });
  });

  describe('a refused retry', () => {
    it('should show the minutes callout above the output when the retry needs more minutes (402)', () => {
      mocks.retryState.error = new ApiError(402, 'x', 'INSUFFICIENT_MINUTES', { needed: 31, available: 12 });
      renderPage();

      expect(screen.getByTestId('minutes-callout')).toHaveTextContent(
        'This video needs 31 minutes and you have 12 minutes to spend.',
      );
    });

    it('should link the callout to the billing page', () => {
      mocks.retryState.error = new ApiError(402, 'x', 'INSUFFICIENT_MINUTES', { needed: 31, available: 12 });
      renderPage();

      expect(screen.getByRole('link', { name: 'See your minutes' })).toHaveAttribute('href', '/settings/billing');
    });

    it('should show the wait when the burst limit refuses it (429)', () => {
      mocks.retryState.error = new ApiError(429, 'x', 'RATE_LIMITED', { retryAfterSec: 600 });
      renderPage();

      expect(screen.getByTestId('billing-notice')).toHaveTextContent('Try again in 10m.');
    });

    it('should show nothing when no retry has failed', () => {
      renderPage();

      expect(screen.queryByTestId('minutes-callout')).toBeNull();
      expect(screen.queryByTestId('billing-notice')).toBeNull();
      expect(screen.queryByTestId('retry-error')).toBeNull();
    });
  });

  describe('the full-page failure state', () => {
    beforeEach(() => {
      setVideo({ status: 'failed' });
    });

    it('should retry through the billed submit', () => {
      renderPage();

      fireEvent.click(screen.getByRole('button', { name: /Retry/ }));

      expect(mocks.retryMutate).toHaveBeenCalledOnce();
      expect(mocks.streamRetry).not.toHaveBeenCalled();
    });

    it('should show why when the retry is refused (402)', () => {
      mocks.retryState.error = new ApiError(402, 'x', 'INSUFFICIENT_MINUTES', { needed: 5, available: 0 });
      renderPage();

      expect(screen.getByTestId('minutes-callout')).toBeInTheDocument();
    });

    it('should show why when the account is blocked (403)', () => {
      mocks.retryState.error = new ApiError(403, 'x', 'ACCOUNT_BLOCKED');
      renderPage();

      expect(screen.getByTestId('billing-notice')).toHaveTextContent("can't start new videos");
    });

    it('should show the message of any other failure instead of swallowing it', () => {
      mocks.retryState.error = new ApiError(500, 'Server error', 'INTERNAL_ERROR');
      renderPage();

      expect(screen.getByTestId('retry-error')).toHaveTextContent('Server error');
    });
  });
});
