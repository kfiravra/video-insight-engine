import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { BillingNoticeAlert } from '../BillingNoticeAlert';
import { HOUR, inMs, renderIn } from './usage-fixtures';

describe('BillingNoticeAlert', () => {
  it('should show the minutes callout for not enough minutes', () => {
    renderIn(
      <BillingNoticeAlert notice={{ kind: 'insufficient', needed: 9, available: 3, nextFreeAt: null }} />,
    );

    expect(screen.getByTestId('minutes-callout')).toBeInTheDocument();
  });

  it('should show the minutes callout for a video that is too long', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'too_long', maxMinutes: 30 }} />);

    expect(screen.getByTestId('minutes-callout')).toBeInTheDocument();
  });

  it('should say the account cannot start videos when it is blocked', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'blocked' }} />);

    expect(screen.getByRole('alert')).toHaveTextContent("This account can't start new videos right now.");
  });

  it('should say how many videos are running when the concurrency cap is hit', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'too_many_runs', maxConcurrent: 2 }} />);

    expect(screen.getByRole('alert')).toHaveTextContent('You already have 2 videos running.');
  });

  it('should still make sense when the concurrency cap is not sent', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'too_many_runs', maxConcurrent: null }} />);

    expect(screen.getByRole('alert')).toHaveTextContent('You already have videos running.');
  });

  it('should say how long to wait when the burst limit sends retryAfterSec', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'rate_limited', retryAfterSec: 14 * 60 }} />);

    expect(screen.getByRole('alert')).toHaveTextContent("You're adding videos too fast. Try again in 14m.");
  });

  it('should fall back to a sentence without a time when retryAfterSec is not sent', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'rate_limited', retryAfterSec: null }} />);

    expect(screen.getByRole('alert')).toHaveTextContent("You're adding videos too fast. Try again in a moment.");
  });

  it('should say the rate-limit wait in Hebrew', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'rate_limited', retryAfterSec: 2 * 3600 }} />, 'he');

    expect(screen.getByRole('alert')).toHaveTextContent('אתם מוסיפים סרטונים מהר מדי. נסו שוב בעוד 2 שע׳.');
  });

  it('should say the rate-limit wait in Arabic', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'rate_limited', retryAfterSec: 2 * 3600 }} />, 'ar');

    expect(screen.getByRole('alert')).toHaveTextContent('تضيف الفيديوهات بسرعة كبيرة. حاول مجددًا بعد 2 س.');
  });

  it('should say at what time more chat messages open up, not a countdown', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'chat_limit', nextAt: inMs(3 * HOUR + 30_000) }} />);

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/^You've used your free chat messages for now\. More open up at .*\d/);
    expect(alert.textContent).not.toMatch(/ in \d/);
  });

  it('should translate a plain refusal into Hebrew', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'blocked' }} />, 'he');

    expect(screen.getByRole('alert')).toHaveTextContent('החשבון הזה לא יכול להתחיל סרטונים חדשים כרגע.');
  });

  it('should use the Arabic few form for the running videos', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'too_many_runs', maxConcurrent: 3 }} />, 'ar');

    expect(screen.getByRole('alert')).toHaveTextContent('لديك بالفعل 3 فيديوهات قيد المعالجة.');
  });
});
