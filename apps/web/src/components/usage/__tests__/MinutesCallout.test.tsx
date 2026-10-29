import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { MinutesCallout } from '../MinutesCallout';
import type { BillingNotice } from '@/lib/billing-errors';
import { HOUR, MINUTE, inMs, renderIn } from './usage-fixtures';

type MinutesNotice = Extract<BillingNotice, { kind: 'insufficient' | 'too_long' }>;

const insufficient = (overrides: Partial<Extract<MinutesNotice, { kind: 'insufficient' }>> = {}): MinutesNotice => ({
  kind: 'insufficient',
  needed: 31,
  available: 12,
  nextFreeAt: null,
  ...overrides,
});

describe('MinutesCallout', () => {
  describe('not enough minutes (402)', () => {
    it('should say how many minutes the video needs and how many are available', () => {
      renderIn(<MinutesCallout notice={insufficient()} />);

      expect(screen.getByRole('alert')).toHaveTextContent(
        'This video needs 31 minutes and you have 12 minutes to spend.',
      );
    });

    it('should use the singular for one minute', () => {
      renderIn(<MinutesCallout notice={insufficient({ needed: 3, available: 1 })} />);

      expect(screen.getByRole('alert')).toHaveTextContent('you have 1 minute to spend');
    });

    it('should say the free video is used and when the next one opens', () => {
      const nextFreeAt = inMs(7 * HOUR + 30 * MINUTE + 30_000);

      renderIn(<MinutesCallout notice={insufficient({ nextFreeAt })} />);

      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent("You've used your free video.");
      expect(alert).toHaveTextContent('The next one opens in 7h 30m.');
    });

    it('should drop the next-free sentence when that time has already passed', () => {
      renderIn(<MinutesCallout notice={insufficient({ nextFreeAt: inMs(-MINUTE) })} />);

      expect(screen.getByRole('alert')).not.toHaveTextContent('The next one opens');
    });

    it('should fall back to a plain sentence when the amounts are not sent', () => {
      renderIn(<MinutesCallout notice={insufficient({ needed: null, available: null })} />);

      expect(screen.getByRole('alert')).toHaveTextContent("You don't have enough minutes for this video.");
    });
  });

  describe('too long for the account (422)', () => {
    it('should name the longest video the account can process', () => {
      renderIn(<MinutesCallout notice={{ kind: 'too_long', maxMinutes: 180 }} />);

      expect(screen.getByRole('alert')).toHaveTextContent(
        'This account can process videos up to 180 minutes long.',
      );
    });

    it('should fall back to a plain sentence when the limit is not sent', () => {
      renderIn(<MinutesCallout notice={{ kind: 'too_long', maxMinutes: null }} />);

      expect(screen.getByRole('alert')).toHaveTextContent(
        'This video is longer than this account can process.',
      );
    });
  });

  describe('call to action', () => {
    it('should link to the billing settings page', () => {
      renderIn(<MinutesCallout notice={insufficient()} />);

      expect(screen.getByRole('link', { name: 'See your minutes' })).toHaveAttribute(
        'href',
        '/settings/billing',
      );
    });

    it('should not offer an upgrade or a price', () => {
      renderIn(<MinutesCallout notice={insufficient()} />);

      expect(screen.getByRole('alert').textContent).not.toMatch(/\$|upgrade/i);
    });
  });

  describe('right-to-left languages', () => {
    it('should write the shortfall in Hebrew with the dual minute form', () => {
      renderIn(<MinutesCallout notice={insufficient({ needed: 5, available: 2 })} />, 'he');

      expect(screen.getByRole('alert')).toHaveTextContent('הסרטון הזה דורש 5 דקות ויש לכם שתי דקות לשימוש.');
    });

    it('should write the shortfall in Arabic with dual and few forms', () => {
      renderIn(<MinutesCallout notice={insufficient({ needed: 3, available: 2 })} />, 'ar');

      expect(screen.getByRole('alert')).toHaveTextContent('يحتاج هذا الفيديو إلى 3 دقائق ولديك دقيقتان للاستخدام.');
    });

    it('should put the Arabic dual in the oblique case after a preposition and the nominative after "you have"', () => {
      renderIn(<MinutesCallout notice={insufficient({ needed: 2, available: 2 })} />, 'ar');

      expect(screen.getByRole('alert')).toHaveTextContent('إلى دقيقتين ولديك دقيقتان للاستخدام');
    });

    it('should put the Arabic dual in the oblique case after "up to"', () => {
      renderIn(<MinutesCallout notice={{ kind: 'too_long', maxMinutes: 2 }} />, 'ar');

      expect(screen.getByRole('alert')).toHaveTextContent('حتى دقيقتين');
    });

    it('should word the video length limit in Arabic', () => {
      renderIn(<MinutesCallout notice={{ kind: 'too_long', maxMinutes: 180 }} />, 'ar');

      expect(screen.getByRole('alert')).toHaveTextContent('حتى 180 دقيقة');
    });

    it('should label the link in Hebrew and keep it pointing at billing', () => {
      renderIn(<MinutesCallout notice={insufficient()} />, 'he');

      expect(screen.getByRole('link', { name: 'לצפייה בדקות שלכם' })).toHaveAttribute(
        'href',
        '/settings/billing',
      );
    });

    it('should render inside a right-to-left container', () => {
      renderIn(<MinutesCallout notice={insufficient()} />, 'he');

      expect(screen.getByTestId('minutes-callout').closest('[dir="rtl"]')).not.toBeNull();
    });
  });
});
