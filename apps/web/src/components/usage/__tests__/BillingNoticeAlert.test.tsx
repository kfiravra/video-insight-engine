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

  it('should show the server message for the burst rate limit', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'rate_limited', message: 'Up to 20 videos per hour.' }} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Up to 20 videos per hour.');
  });

  it('should fall back to a local sentence when the burst limit has no message', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'rate_limited', message: null }} />);

    expect(screen.getByRole('alert')).toHaveTextContent("You're adding videos too fast.");
  });

  it('should say when more chat messages open up', () => {
    renderIn(<BillingNoticeAlert notice={{ kind: 'chat_limit', nextAt: inMs(3 * HOUR + 30_000) }} />);

    expect(screen.getByRole('alert')).toHaveTextContent(
      "You've used your free chat messages for now. More open up in 3h.",
    );
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
