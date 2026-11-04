import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ApiError } from '@/api/client';
import { DirectionProvider } from '@/contexts/DirectionContext';
import { useBillingText } from '@/hooks/use-billing-text';
import type { FailedVideoImport } from '@/api/playlists';
import {
  debtLine,
  describeBillingNotice,
  describeChatRefusal,
  describeFailedImport,
  describeRetryFailure,
  isTokensFailure,
  playlistQuoteLine,
  RETRY_FAILED_MESSAGE,
  tokensCalloutLines,
} from '../billing-notice-text';

const NOW = new Date(2026, 9, 28, 10, 0).getTime();

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

describe('tokensCalloutLines', () => {
  it("should name the video's estimate and the balance", () => {
    expect(tokensCalloutLines({ kind: 'insufficient', needed: 840, available: 300 }, en)).toEqual({
      title: 'This video needs ≈ 840 tokens and you have 300 tokens.',
      detail: null,
    });
  });

  it('should say "0 tokens" for an empty balance that is not in debt', () => {
    expect(tokensCalloutLines({ kind: 'insufficient', needed: 750, available: 0 }, en).title).toBe(
      'This video needs ≈ 750 tokens and you have 0 tokens.',
    );
  });

  it('should lead with the shortfall and add the estimate when the balance is below zero', () => {
    expect(tokensCalloutLines({ kind: 'insufficient', needed: 840, available: -120 }, en)).toEqual({
      title: "You're 120 tokens short — add tokens to continue.",
      detail: 'This video needs ≈ 840 tokens on top of that.',
    });
  });

  it.each([
    [null, 300],
    [840, null],
  ])('should fall back to a sentence without figures for needed %s and available %s', (needed, available) => {
    expect(tokensCalloutLines({ kind: 'insufficient', needed, available }, en)).toEqual({
      title: "You don't have enough tokens for this video.",
      detail: null,
    });
  });

  it('should name the length cap for a video that is too long', () => {
    expect(tokensCalloutLines({ kind: 'too_long', maxMinutes: 180 }, en).title).toBe(
      'This account can process videos up to 180 minutes long.',
    );
  });

  it('should use the Arabic oblique dual after "up to"', () => {
    expect(tokensCalloutLines({ kind: 'too_long', maxMinutes: 2 }, ar).title).toBe(
      'يمكن لهذا الحساب معالجة فيديوهات حتى دقيقتين.',
    );
  });

  it("should fill the estimate from the language's oblique token forms and the balance from the plain ones", () => {
    const { labels } = ar;
    const title = tokensCalloutLines({ kind: 'insufficient', needed: 2, available: 2 }, ar).title;

    expect(title).toBe(
      labels.calloutNeeds
        .replace('{needed}', ar.tokensOblique(2))
        .replace('{available}', ar.tokens(2)),
    );
  });
});

describe('debtLine', () => {
  it('should name the shortfall as a positive amount', () => {
    expect(debtLine(1_250, en)).toBe("You're 1,250 tokens short — add tokens to continue.");
  });
});

describe('describeBillingNotice', () => {
  it('should join the callout lines into one sentence', () => {
    expect(describeBillingNotice({ kind: 'insufficient', needed: 840, available: -120 }, en, NOW)).toBe(
      "You're 120 tokens short — add tokens to continue. This video needs ≈ 840 tokens on top of that.",
    );
  });

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
});

describe('describeChatRefusal', () => {
  it('should say the tokens ran out for a balance of zero', () => {
    expect(describeChatRefusal({ kind: 'insufficient', needed: 1, available: 0 }, en)).toBe(
      "You're out of tokens — add tokens to keep chatting.",
    );
  });

  it('should name the shortfall for a balance below zero', () => {
    expect(describeChatRefusal({ kind: 'insufficient', needed: 1, available: -75 }, en)).toBe(
      "You're 75 tokens short — add tokens to continue.",
    );
  });

  it('should name no per-turn price', () => {
    expect(describeChatRefusal({ kind: 'insufficient', needed: 1, available: 0 }, en)).not.toMatch(/\d/);
  });

  it('should say chats are off for a blocked account', () => {
    expect(describeChatRefusal({ kind: 'blocked' }, en)).toBe("This account can't start new chats right now.");
  });

  it.each([
    { kind: 'rate_limited', retryAfterSec: 30 } as const,
    { kind: 'too_many_runs', maxConcurrent: 2 } as const,
    { kind: 'too_long', maxMinutes: 180 } as const,
  ])('should leave $kind to the caller', (notice) => {
    expect(describeChatRefusal(notice, en)).toBeNull();
  });
});

describe('describeFailedImport', () => {
  it.each([
    ['METADATA_UNAVAILABLE', "Couldn't read this video"],
    ['HOLD_CONFLICT', "Couldn't read this video"],
    ['INSUFFICIENT_TOKENS', 'Not enough tokens'],
    ['VIDEO_TOO_LONG', 'Too long for this account'],
    ['ACCOUNT_BLOCKED', "Account can't start videos"],
    ['Video not found', 'Video not found'],
    ['INSUFFICIENT_MINUTES', "Couldn't be added"],
    ['SOME_FUTURE_CODE', "Couldn't be added"],
    ['', "Couldn't be added"],
  ])('should explain %s as "%s"', (error, expected) => {
    expect(describeFailedImport(failed(error), en)).toBe(expected);
  });

  it('should explain an unreadable video in Hebrew', () => {
    expect(describeFailedImport(failed('METADATA_UNAVAILABLE'), he)).toBe('לא ניתן היה לקרוא את הסרטון');
  });
});

describe('isTokensFailure', () => {
  it.each([
    ['INSUFFICIENT_TOKENS', true],
    ['VIDEO_TOO_LONG', false],
    ['METADATA_UNAVAILABLE', false],
    ['ACCOUNT_BLOCKED', false],
    ['Video not found', false],
  ])('should say %s is a tokens failure: %s', (error, expected) => {
    expect(isTokensFailure(failed(error))).toBe(expected);
  });
});

describe('playlistQuoteLine', () => {
  const quote = (neededTokens: number, availableTokens: number) => ({ neededTokens, availableTokens });

  it('should say what the playlist needs and what the account has', () => {
    expect(playlistQuoteLine(quote(3_400, 1_200), en)).toBe(
      'The whole playlist needs ≈ 3,400 tokens; you have 1,200 tokens.',
    );
  });

  it('should show a negative balance as it is', () => {
    expect(playlistQuoteLine(quote(840, -60), en)).toBe('The whole playlist needs ≈ 840 tokens; you have -60 tokens.');
  });

  it.each([[quote(1_000, 1_200)], [quote(1_200, 1_200)], [undefined]])('should say nothing for %j', (billing) => {
    expect(playlistQuoteLine(billing, en)).toBeNull();
  });
});

describe('describeRetryFailure', () => {
  it('should word a billing refusal in the user language', () => {
    const error = new ApiError(403, 'x', 'ACCOUNT_BLOCKED');

    expect(describeRetryFailure(error, he, NOW)).toBe('החשבון הזה לא יכול להתחיל סרטונים חדשים כרגע.');
  });

  it('should describe not enough tokens with the figures', () => {
    const error = new ApiError(402, 'x', 'INSUFFICIENT_TOKENS', { needed: 840, available: 300 });

    expect(describeRetryFailure(error, en, NOW)).toBe('This video needs ≈ 840 tokens and you have 300 tokens.');
  });

  it('should use the message of any other error', () => {
    expect(describeRetryFailure(new Error('Network down'), en, NOW)).toBe('Network down');
  });

  it('should fall back to a general sentence for a thrown non-error', () => {
    expect(describeRetryFailure('boom', en, NOW)).toBe(RETRY_FAILED_MESSAGE);
  });
});
