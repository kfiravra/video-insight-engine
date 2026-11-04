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

  it('should show the tokens callout, with its link to billing, when the retry needs more tokens (402)', () => {
    const error = new ApiError(402, 'x', 'INSUFFICIENT_TOKENS', { needed: 840, available: 300 });

    renderIn(<RetryErrorNotice error={error} />);

    expect(screen.getByTestId('tokens-callout')).toHaveTextContent('This video needs ≈ 840 tokens and you have 300 tokens.');
    expect(screen.getByRole('link', { name: 'Add tokens' })).toHaveAttribute('href', '/settings/billing');
  });

  it('should lead with the shortfall when the retry is refused while the balance is below zero (402)', () => {
    const error = new ApiError(402, 'x', 'INSUFFICIENT_TOKENS', { needed: 840, available: -45 });

    renderIn(<RetryErrorNotice error={error} />);

    expect(screen.getByTestId('tokens-callout')).toHaveTextContent("You're 45 tokens short — add tokens to continue.");
  });

  it('should show the length limit when the video is too long (422)', () => {
    renderIn(<RetryErrorNotice error={new ApiError(422, 'x', 'VIDEO_TOO_LONG', { maxMinutes: 180 })} />);

    expect(screen.getByTestId('tokens-callout')).toHaveTextContent('up to 180 minutes long');
  });

  it('should say the account is blocked (403) without the tokens callout', () => {
    renderIn(<RetryErrorNotice error={new ApiError(403, 'x', 'ACCOUNT_BLOCKED')} />);

    expect(screen.getByTestId('billing-notice')).toHaveTextContent("can't start new videos");
    expect(screen.queryByTestId('tokens-callout')).toBeNull();
  });

  it.each(['INSUFFICIENT_MINUTES', 'VIDEO_TOO_LONG_FOR_PLAN'])(
    'should treat the retired minutes code %s as any other failure',
    (code) => {
      renderIn(<RetryErrorNotice error={new ApiError(402, 'Old server', code)} />);

      expect(screen.getByTestId('retry-error')).toHaveTextContent('Old server');
    },
  );

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
