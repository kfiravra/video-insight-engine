import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '@/components/theme-provider';
import { AppHeader } from '../AppHeader';
import { useAuthStore } from '@/stores/auth-store';
import * as usageHook from '@/hooks/use-usage';
import { usageSummary } from '@/components/usage/__tests__/usage-fixtures';

function renderHeader() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider defaultTheme="dark" storageKey="test-theme">
        <MemoryRouter>
          <AppHeader />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function openUserMenu() {
  await userEvent.setup().click(screen.getByRole('button', { name: 'Test User' }));
}

describe('AppHeader user menu', () => {
  let usageSpy: { mockRestore: () => void } | undefined;

  beforeEach(() => {
    useAuthStore.setState({
      user: { id: 'user-1', email: 'test@example.com', name: 'Test User' },
      accessToken: 'token',
      isAuthenticated: true,
      isLoading: false,
    });
  });

  afterEach(() => {
    usageSpy?.mockRestore();
  });

  it('should list Minutes & plan as a menu item beside the usage tile', async () => {
    usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockReturnValue({
      data: usageSummary(),
      isError: false,
    } as unknown as ReturnType<typeof usageHook.useUserUsage>);
    renderHeader();

    await openUserMenu();

    expect(screen.getByRole('menuitem', { name: 'Minutes & plan' })).toBeInTheDocument();
    expect(screen.getByTestId('usage-tile')).toBeInTheDocument();
  });

  it('should keep the rest of the menu when the usage tile crashes', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    usageSpy = vi.spyOn(usageHook, 'useUserUsage').mockImplementation(() => {
      throw new Error('tile crashed');
    });
    renderHeader();

    await openUserMenu();

    expect(screen.getByRole('menuitem', { name: 'Minutes & plan' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /Sign out/ })).toBeInTheDocument();
    expect(screen.queryByTestId('usage-tile')).toBeNull();
    consoleError.mockRestore();
  });
});
