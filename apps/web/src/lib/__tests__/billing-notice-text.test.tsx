import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ApiError } from '@/api/client';
import { DirectionProvider } from '@/contexts/DirectionContext';
import { useBillingText } from '@/hooks/use-billing-text';
import type { FailedVideoImport } from '@/api/playlists';
import {
  describeBillingNotice,
  describeFailedImport,
  describeRetryFailure,
  isMinutesFailure,
  minutesCalloutLines,
  playlistQuoteLine,
  RETRY_FAILED_MESSAGE,
} from '../billing-notice-text';

// Local times, so "same day" holds in whatever time zone the tests run in.
const NOW = new Date(2026, 9, 28, 10, 0).getTime();
const SAME_DAY = new Date(2026, 9, 28, 13, 15).toISOString();
const NEXT_WEEK = new Date(2026, 10, 4, 13, 15).toISOString();

function textFor(language: string) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <DirectionProvider language={language} isRTL={language !== 'en'}>
      {children}
    </DirectionProvider>
  );
  return renderHook(() => useBillingText(), { wrapper }).result.current;
}

const en = textFor('en');
const he = textFor('he');
const ar = textFor('ar');

const failed = (error: string): FailedVideoImport => ({ youtubeId: 'a', title: 'A', position: 0, error });

describe('describeBillingNotice', () => {
  describe('rate limited', () => {
    it('should say how long to wait from retryAfterSec', () => {
      expect(describeBillingNotice({ kind: 'rate_limited', retryAfterSec: 14 * 60 }, en, NOW)).toBe(
        "You're adding videos too fast. Try again in 14m.",
      );
    });

    it('should say try again in a moment without retryAfterSec', () => {
      expect(describeBillingNotice({ kind: 'rate_limited', retryAfterSec: null }, en, NOW)).toBe(
        "You're adding videos too fast. Try again in a moment.",
      );
    });

    it.each([0, -3])('should not print a wait of %i seconds', (retryAfterSec) => {
      expect(describeBillingNotice({ kind: 'rate_limited', retryAfterSec }, en, NOW)).toBe(
        "You're adding videos too fast. Try again in a moment.",
      );
    });

    it('should localise the wait', () => {
      expect(describeBillingNotice({ kind: 'rate_limited', retryAfterSec: 3600 }, he, NOW)).toBe(
        'אתם מוסיפים סרטונים מהר מדי. נסו שוב בעוד 1 שע׳.',
      );
    });
  });

  describe('chat limit', () => {
    it('should name the clock time for a moment later today', () => {
      expect(describeBillingNotice({ kind: 'chat_limit', nextAt: SAME_DAY }, en, NOW)).toBe(
        "You've used your free chat messages for now. More open up at 1:15 PM.",
      );
    });

    it('should name the date too when it is not today', () => {
      expect(describeBillingNotice({ kind: 'chat_limit', nextAt: NEXT_WEEK }, en, NOW)).toBe(
        "You've used your free chat messages for now. More open up at Nov 4, 2026, 1:15 PM.",
      );
    });

    it.each([null, new Date(NOW - 60_000).toISOString()])('should say only that the messages are used up for nextAt %s', (nextAt) => {
      expect(describeBillingNotice({ kind: 'chat_limit', nextAt }, en, NOW)).toBe(
        "You've used your free chat messages for now.",
      );
    });

    it('should be the same text whenever it is read, since it is saved', () => {
      const notice = { kind: 'chat_limit' as const, nextAt: SAME_DAY };

      expect(describeBillingNotice(notice, en, NOW)).toBe(describeBillingNotice(notice, en, NOW + 30 * 60_000));
    });
  });
});

describe('minutesCalloutLines in Arabic', () => {
  it('should use the oblique dual after "needs" and the nominative after "you have"', () => {
    const lines = minutesCalloutLines({ kind: 'insufficient', needed: 2, available: 2, nextFreeAt: null }, ar, NOW);

    expect(lines.title).toBe('يحتاج هذا الفيديو إلى دقيقتين ولديك دقيقتان للاستخدام.');
  });

  it('should use the oblique dual after "up to"', () => {
    expect(minutesCalloutLines({ kind: 'too_long', maxMinutes: 2 }, ar, NOW).title).toBe(
      'يمكن لهذا الحساب معالجة فيديوهات حتى دقيقتين.',
    );
  });
});

describe('describeFailedImport', () => {
  it.each([
    ['METADATA_UNAVAILABLE', "Couldn't read this video"],
    ['HOLD_CONFLICT', "Couldn't read this video"],
    ['INSUFFICIENT_MINUTES', 'Not enough minutes'],
    ['ACCOUNT_BLOCKED', "Account can't start videos"],
    ['Video not found', 'Video not found'],
    ['SOME_FUTURE_CODE', "Couldn't be added"],
    ['', "Couldn't be added"],
  ])('should explain %s as "%s"', (error, expected) => {
    expect(describeFailedImport(failed(error), en)).toBe(expected);
  });

  it('should explain an unreadable video in Hebrew', () => {
    expect(describeFailedImport(failed('METADATA_UNAVAILABLE'), he)).toBe('לא ניתן היה לקרוא את הסרטון');
  });
});

describe('isMinutesFailure', () => {
  it.each([
    ['INSUFFICIENT_MINUTES', true],
    ['VIDEO_TOO_LONG_FOR_PLAN', true],
    ['METADATA_UNAVAILABLE', false],
    ['ACCOUNT_BLOCKED', false],
    ['Video not found', false],
  ])('should say %s is a minutes failure: %s', (error, expected) => {
    expect(isMinutesFailure(failed(error))).toBe(expected);
  });
});

describe('playlistQuoteLine', () => {
  const quote = (neededMinutes: number, availableMinutes: number) => ({ neededMinutes, availableMinutes, freeVideoAvailable: false });

  it('should say what the playlist needs and what the account has', () => {
    expect(playlistQuoteLine(quote(340, 120), en)).toBe('The whole playlist needs 340 minutes; you have 120 minutes.');
  });

  it.each([[quote(100, 120)], [quote(120, 120)], [undefined]])('should say nothing for %j', (billing) => {
    expect(playlistQuoteLine(billing, en)).toBeNull();
  });
});

describe('describeRetryFailure', () => {
  it('should word a billing refusal in the user language', () => {
    const error = new ApiError(403, 'x', 'ACCOUNT_BLOCKED');

    expect(describeRetryFailure(error, he, NOW)).toBe('החשבון הזה לא יכול להתחיל סרטונים חדשים כרגע.');
  });

  it('should describe not enough minutes with the figures', () => {
    const error = new ApiError(402, 'x', 'INSUFFICIENT_MINUTES', { needed: 31, available: 12 });

    expect(describeRetryFailure(error, en, NOW)).toBe('This video needs 31 minutes and you have 12 minutes to spend.');
  });

  it('should use the message of any other error', () => {
    expect(describeRetryFailure(new Error('Network down'), en, NOW)).toBe('Network down');
  });

  it('should fall back to a general sentence for a thrown non-error', () => {
    expect(describeRetryFailure('boom', en, NOW)).toBe(RETRY_FAILED_MESSAGE);
  });
});
