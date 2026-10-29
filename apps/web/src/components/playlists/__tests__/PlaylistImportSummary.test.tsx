import { describe, it, expect, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PlaylistImportSummary } from '../PlaylistImportSummary';
import { renderIn } from '@/components/usage/__tests__/usage-fixtures';
import type { FailedVideoImport, PlaylistImportResult } from '@/api/playlists';

function failed(overrides: Partial<FailedVideoImport> = {}): FailedVideoImport {
  return { youtubeId: 'abc', title: 'A video', position: 0, error: 'Video not found', ...overrides };
}

function result(failedVideos: FailedVideoImport[], added = 3, total = 5): PlaylistImportResult {
  return {
    // The server deletes the folder it created when nothing landed in it, and sends null.
    folder: added > 0 ? { id: 'f1', name: 'Playlist' } : null,
    videos: Array.from({ length: added }, (_, i) => ({
      id: `v${i}`,
      videoSummaryId: `s${i}`,
      youtubeId: `yt${i}`,
      title: `Video ${i}`,
      status: 'processing',
      position: i,
    })),
    totalVideos: total,
    cachedCount: 0,
    processingCount: added,
    failedCount: failedVideos.length,
    failedVideos,
  };
}

describe('PlaylistImportSummary', () => {
  it('should say how many videos were added out of the playlist', () => {
    renderIn(<PlaylistImportSummary result={result([failed(), failed({ position: 1 })])} onDismiss={vi.fn()} />);

    expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent('3 of 5 videos added');
  });

  describe('when nothing landed and the import left no folder (folder is null)', () => {
    const nothingLanded = () =>
      result([failed({ error: 'INSUFFICIENT_MINUTES' }), failed({ position: 1, youtubeId: 'def', error: 'ACCOUNT_BLOCKED' })], 0, 2);

    it('should carry no folder', () => {
      expect(nothingLanded().folder).toBeNull();
    });

    it('should still say how many videos were added', () => {
      renderIn(<PlaylistImportSummary result={nothingLanded()} onDismiss={vi.fn()} />);

      expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent('0 of 2 videos added');
    });

    it('should still list why each video was left out', () => {
      renderIn(<PlaylistImportSummary result={nothingLanded()} onDismiss={vi.fn()} />);

      expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
        'A videoNot enough minutes',
        'A videoAccount can\'t start videos',
      ]);
    });

    it('should still link to the billing page when minutes were the reason', () => {
      renderIn(<PlaylistImportSummary result={nothingLanded()} onDismiss={vi.fn()} />);

      expect(screen.getByTestId('playlist-import-minutes-link')).toHaveAttribute('href', '/settings/billing');
    });
  });

  it('should list each video that was left out by title', () => {
    const items = [failed({ title: 'First' }), failed({ title: 'Second', position: 1, youtubeId: 'def' })];

    renderIn(<PlaylistImportSummary result={result(items)} onDismiss={vi.fn()} />);

    const list = screen.getByRole('list');
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('First'),
      expect.stringContaining('Second'),
    ]);
  });

  it('should fall back to the YouTube id when a title is missing', () => {
    renderIn(<PlaylistImportSummary result={result([failed({ title: '', youtubeId: 'xyz123' })])} onDismiss={vi.fn()} />);

    expect(screen.getByRole('listitem')).toHaveTextContent('xyz123');
  });

  it.each([
    ['INSUFFICIENT_MINUTES', 'Not enough minutes'],
    ['VIDEO_TOO_LONG_FOR_PLAN', 'Too long for this account'],
    ['ACCOUNT_BLOCKED', "Account can't start videos"],
    ['TOO_MANY_RUNS', 'Too many videos running'],
    ['RATE_LIMITED', 'Too many requests'],
  ])('should explain %s in words', (code, reason) => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: code })])} onDismiss={vi.fn()} />);

    expect(screen.getByRole('listitem')).toHaveTextContent(reason);
  });

  it.each(['METADATA_UNAVAILABLE', 'HOLD_CONFLICT'])('should say the video could not be read for %s', (code) => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: code })])} onDismiss={vi.fn()} />);

    expect(screen.getByRole('listitem')).toHaveTextContent("Couldn't read this video");
  });

  it('should not link to the billing page when the only failure is an unreadable video', () => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: 'METADATA_UNAVAILABLE' })])} onDismiss={vi.fn()} />);

    expect(screen.queryByTestId('playlist-import-minutes-link')).toBeNull();
  });

  describe('the up-front quote', () => {
    const withQuote = (needed: number, available: number, failures = [failed({ error: 'INSUFFICIENT_MINUTES' })]) => ({
      ...result(failures),
      billing: { neededMinutes: needed, availableMinutes: available, freeVideoAvailable: false },
    });

    it('should say what the whole playlist needs against what the account has', () => {
      renderIn(<PlaylistImportSummary result={withQuote(340, 120)} onDismiss={vi.fn()} />);

      expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent(
        'The whole playlist needs 340 minutes; you have 120 minutes.',
      );
    });

    it('should stay quiet when the account could pay for the whole playlist', () => {
      renderIn(<PlaylistImportSummary result={withQuote(100, 120)} onDismiss={vi.fn()} />);

      expect(screen.getByTestId('playlist-import-summary')).not.toHaveTextContent('The whole playlist needs');
    });

    it('should stay quiet when the failures were not about minutes', () => {
      renderIn(<PlaylistImportSummary result={withQuote(340, 120, [failed()])} onDismiss={vi.fn()} />);

      expect(screen.getByTestId('playlist-import-summary')).not.toHaveTextContent('The whole playlist needs');
    });

    it('should work with a server that sends no quote', () => {
      renderIn(<PlaylistImportSummary result={result([failed({ error: 'INSUFFICIENT_MINUTES' })])} onDismiss={vi.fn()} />);

      expect(screen.getByTestId('playlist-import-summary')).not.toHaveTextContent('The whole playlist needs');
    });

    it('should word the quote in Arabic with the oblique dual after "needs"', () => {
      renderIn(<PlaylistImportSummary result={withQuote(2, 1)} onDismiss={vi.fn()} />, 'ar');

      expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent(
        'تحتاج القائمة كاملةً إلى دقيقتين ولديك دقيقة واحدة.',
      );
    });
  });

  it('should put the Arabic dual of the playlist size in the oblique case after "of"', () => {
    renderIn(<PlaylistImportSummary result={result([failed()], 1, 2)} onDismiss={vi.fn()} />, 'ar');

    expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent('أُضيف 1 من فيديوهين');
  });

  it('should explain an unreadable video in Arabic', () => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: 'METADATA_UNAVAILABLE' })])} onDismiss={vi.fn()} />, 'ar');

    expect(screen.getByRole('listitem')).toHaveTextContent('تعذّرت قراءة هذا الفيديو');
  });

  it('should show the server message for an error that is not a billing code', () => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: 'Video not found' })])} onDismiss={vi.fn()} />);

    expect(screen.getByRole('listitem')).toHaveTextContent('Video not found');
  });

  it('should hide a raw machine code it does not know', () => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: 'SOME_FUTURE_CODE' })])} onDismiss={vi.fn()} />);

    const row = screen.getByRole('listitem');
    expect(row).not.toHaveTextContent('SOME_FUTURE_CODE');
    expect(row).toHaveTextContent("Couldn't be added");
  });

  it('should link to the billing page when a video was stopped by minutes', () => {
    renderIn(<PlaylistImportSummary result={result([failed({ error: 'INSUFFICIENT_MINUTES' })])} onDismiss={vi.fn()} />);

    expect(screen.getByTestId('playlist-import-minutes-link')).toHaveAttribute('href', '/settings/billing');
  });

  it('should not link to the billing page when no failure was about minutes', () => {
    renderIn(<PlaylistImportSummary result={result([failed()])} onDismiss={vi.fn()} />);

    expect(screen.queryByTestId('playlist-import-minutes-link')).toBeNull();
  });

  it('should call onDismiss when the user dismisses it', async () => {
    const onDismiss = vi.fn();
    renderIn(<PlaylistImportSummary result={result([failed()])} onDismiss={onDismiss} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Dismiss' }));

    expect(onDismiss).toHaveBeenCalledOnce();
  });

  describe('right-to-left languages', () => {
    it('should count the videos in Hebrew', () => {
      renderIn(<PlaylistImportSummary result={result([failed()], 1, 2)} onDismiss={vi.fn()} />, 'he');

      expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent('1 מתוך שני סרטונים נוספו');
    });

    it('should count the videos in Arabic', () => {
      renderIn(<PlaylistImportSummary result={result([failed()], 2, 5)} onDismiss={vi.fn()} />, 'ar');

      expect(screen.getByTestId('playlist-import-summary')).toHaveTextContent('أُضيف 2 من 5 فيديوهات');
    });

    it('should explain a failure in Arabic', () => {
      renderIn(<PlaylistImportSummary result={result([failed({ error: 'INSUFFICIENT_MINUTES' })])} onDismiss={vi.fn()} />, 'ar');

      expect(screen.getByRole('listitem')).toHaveTextContent('دقائق غير كافية');
    });
  });
});
