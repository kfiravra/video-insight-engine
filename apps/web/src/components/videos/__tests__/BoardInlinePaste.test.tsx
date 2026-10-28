import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent, waitFor } from '@/test/test-utils';
import { server } from '@/test/mocks/server';
import { createMockVideo } from '@/test/mocks/handlers';
import { useAuthStore } from '@/stores/auth-store';
import { setAccessToken } from '@/api/client';
import { BoardInlinePaste } from '../BoardInlinePaste';

const API_URL = 'http://localhost:3000/api';
const VIDEO_URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';

function rejectWith(status: number, body: Record<string, unknown>): void {
  server.use(http.post(`${API_URL}/videos`, () => HttpResponse.json(body, { status })));
}

async function submit(): Promise<void> {
  const user = userEvent.setup();
  renderWithProviders(<BoardInlinePaste folderId={null} />, { route: '/board' });
  await user.type(screen.getByLabelText('YouTube URL'), VIDEO_URL);
  await user.click(screen.getByRole('button', { name: /summarize/i }));
}

describe('BoardInlinePaste billing refusals', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: { id: 'user-1', email: 'test@example.com', name: 'Test User' },
      accessToken: 'test-access-token',
      isAuthenticated: true,
      isLoading: false,
    });
    setAccessToken('test-access-token');
  });

  it('should show the minutes callout when the account cannot pay for the video (402)', async () => {
    rejectWith(402, {
      error: 'INSUFFICIENT_MINUTES',
      message: 'This video needs 31 minutes; 12 available',
      statusCode: 402,
      needed: 31,
      available: 12,
      nextFreeAt: null,
    });

    await submit();

    expect(await screen.findByTestId('minutes-callout')).toHaveTextContent(
      'This video needs 31 minutes and you have 12 minutes to spend.',
    );
  });

  it('should point the callout at the billing page', async () => {
    rejectWith(402, { error: 'INSUFFICIENT_MINUTES', message: 'x', statusCode: 402, needed: 5, available: 0 });

    await submit();

    expect(await screen.findByRole('link', { name: 'See your minutes' })).toHaveAttribute(
      'href',
      '/settings/billing',
    );
  });

  it('should still show the callout when the 402 carries no amounts', async () => {
    rejectWith(402, { error: 'INSUFFICIENT_MINUTES', message: 'x', statusCode: 402 });

    await submit();

    expect(await screen.findByTestId('minutes-callout')).toHaveTextContent(
      "You don't have enough minutes for this video.",
    );
  });

  it('should show the video length limit when the video is too long (422)', async () => {
    rejectWith(422, { error: 'VIDEO_TOO_LONG_FOR_PLAN', message: 'x', statusCode: 422, maxMinutes: 30 });

    await submit();

    expect(await screen.findByTestId('minutes-callout')).toHaveTextContent('up to 30 minutes long');
  });

  it('should say the account is blocked (403)', async () => {
    rejectWith(403, { error: 'ACCOUNT_BLOCKED', message: 'x', statusCode: 403 });

    await submit();

    expect(await screen.findByRole('alert')).toHaveTextContent("This account can't start new videos right now.");
  });

  it('should say how many videos are running when the concurrency cap is hit (429)', async () => {
    rejectWith(429, { error: 'TOO_MANY_RUNS', message: 'x', statusCode: 429, maxConcurrent: 2 });

    await submit();

    expect(await screen.findByTestId('billing-notice')).toHaveTextContent('You already have 2 videos running.');
  });

  it('should show the server message for the burst limit (429)', async () => {
    rejectWith(429, { error: 'RATE_LIMITED', message: 'Up to 20 videos per hour.', statusCode: 429 });

    await submit();

    expect(await screen.findByTestId('billing-notice')).toHaveTextContent('Up to 20 videos per hour.');
  });

  it('should keep showing the plain error for a failure that is not a billing refusal', async () => {
    rejectWith(500, { error: 'INTERNAL_ERROR', message: 'Something broke', statusCode: 500 });

    await submit();

    expect(await screen.findByRole('alert')).toHaveTextContent('Something broke');
    expect(screen.queryByTestId('minutes-callout')).toBeNull();
  });

  it('should clear the callout when the user edits the URL', async () => {
    rejectWith(402, { error: 'INSUFFICIENT_MINUTES', message: 'x', statusCode: 402, needed: 5, available: 0 });
    await submit();
    await screen.findByTestId('minutes-callout');

    await userEvent.setup().type(screen.getByLabelText('YouTube URL'), 'x');

    await waitFor(() => expect(screen.queryByTestId('minutes-callout')).toBeNull());
  });

  it('should open the new video when the submit succeeds', async () => {
    server.use(
      http.post(`${API_URL}/videos`, () =>
        HttpResponse.json({ video: createMockVideo({ id: 'video-42' }), cached: false }),
      ),
    );

    await submit();

    await waitFor(() => expect(window.location.pathname).toBe('/video/video-42'));
  });
});
