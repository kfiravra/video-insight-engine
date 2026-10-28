import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { ApiError } from '@/api/client';
import { RetryErrorNotice } from '../RetryErrorNotice';
import { renderIn } from './usage-fixtures';

describe('RetryErrorNotice', () => {
  it.each([null, undefined])('should render nothing without an error (%s)', (error) => {
    const { container } = renderIn(<RetryErrorNotice error={error} />);

    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('should show the minutes callout, with its link to billing, when the retry needs more minutes (402)', () => {
    const error = new ApiError(402, 'x', 'INSUFFICIENT_MINUTES', { needed: 31, available: 12 });

    renderIn(<RetryErrorNotice error={error} />);

    expect(screen.getByTestId('minutes-callout')).toHaveTextContent('This video needs 31 minutes and you have 12 minutes to spend.');
    expect(screen.getByRole('link', { name: 'See your minutes' })).toHaveAttribute('href', '/settings/billing');
  });

  it('should show the length limit when the video is too long for the plan (422)', () => {
    renderIn(<RetryErrorNotice error={new ApiError(422, 'x', 'VIDEO_TOO_LONG_FOR_PLAN', { maxMinutes: 30 })} />);

    expect(screen.getByTestId('minutes-callout')).toHaveTextContent('up to 30 minutes long');
  });

  it('should say the account is blocked (403) without the minutes callout', () => {
    renderIn(<RetryErrorNotice error={new ApiError(403, 'x', 'ACCOUNT_BLOCKED')} />);

    expect(screen.getByTestId('billing-notice')).toHaveTextContent("can't start new videos");
    expect(screen.queryByTestId('minutes-callout')).toBeNull();
  });

  it('should say how long to wait when the burst limit sends retryAfterSec (429)', () => {
    renderIn(<RetryErrorNotice error={new ApiError(429, 'Up to 20 videos per hour', 'RATE_LIMITED', { retryAfterSec: 1200 })} />);

    const notice = screen.getByTestId('billing-notice');
    expect(notice).toHaveTextContent('Try again in 20m.');
    expect(notice).not.toHaveTextContent('Up to 20 videos per hour');
  });

  it('should show the message of any other failure', () => {
    renderIn(<RetryErrorNotice error={new ApiError(500, 'Server error', 'INTERNAL_ERROR')} />);

    expect(screen.getByTestId('retry-error')).toHaveTextContent('Server error');
  });

  it('should fall back to a general sentence for an error with no message', () => {
    renderIn(<RetryErrorNotice error="boom" />);

    expect(screen.getByTestId('retry-error')).toHaveTextContent("Couldn't retry this video.");
  });

  it('should localise a billing refusal', () => {
    renderIn(<RetryErrorNotice error={new ApiError(403, 'x', 'ACCOUNT_BLOCKED')} />, 'ar');

    expect(screen.getByTestId('billing-notice')).toHaveTextContent('لا يمكن لهذا الحساب بدء فيديوهات جديدة الآن.');
  });
});
