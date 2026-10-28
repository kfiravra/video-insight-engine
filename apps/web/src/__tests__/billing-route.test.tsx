import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { App } from '@/App';
import { useAuthStore } from '@/stores/auth-store';
import { setAccessToken } from '@/api/client';
import * as usageHook from '@/hooks/use-usage';
import { usageSummary } from '@/components/usage/__tests__/usage-fixtures';
import { createTestQueryClient } from '@/test/test-utils';
import { QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/hooks/use-websocket', () => ({ useWebSocket: vi.fn() }));
vi.mock('@/features/video-output/hooks/use-processing-manager', () => ({ useProcessingManager: vi.fn() }));
vi.mock('@/components/layout/Layout', () => ({
  Layout: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

function renderAppAt(path: string) {
  window.history.pushState({}, 'Test page', path);
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <App />
    </QueryClientProvider>,
  );
}

function signIn(): void {
  useAuthStore.setState({
    user: { id: 'user-1', email: 'test@example.com', name: 'Test User' },
    accessToken: 'test-access-token',
    isAuthenticated: true,
    isLoading: false,
  });
  setAccessToken('test-access-token');
}

describe('/settings/billing route', () => {
  let usageSpy: { mockRestore: () => void };

  beforeEach(() => {
    usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockReturnValue({
      data: usageSummary(),
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof usageHook.useUserUsage>);
  });

  afterEach(() => {
    usageSpy.mockRestore();
  });

  it('should render the billing page for a signed-in user', async () => {
    signIn();

    renderAppAt('/settings/billing');

    expect(await screen.findByRole('heading', { level: 1, name: 'Minutes & plan' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/settings/billing');
  });

  it('should send a signed-out visitor to the login page', async () => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: false,
    });
    setAccessToken(null);

    renderAppAt('/settings/billing');

    await vi.waitFor(() => expect(window.location.pathname).toBe('/login'));
  });
});
