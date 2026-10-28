import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { UserCostRow } from '../lib/api';

const { mockCosts, mockCostDetail, mockAccount } = vi.hoisted(() => ({
  mockCosts: vi.fn(),
  mockCostDetail: vi.fn(),
  mockAccount: vi.fn(),
}));

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      users: { ...actual.api.users, costs: mockCosts, costDetail: mockCostDetail },
      billing: { account: mockAccount },
    },
  };
});

import { UsersPage } from './UsersPage';

const USER: UserCostRow = {
  userId: 'a'.repeat(24),
  email: 'test@example.com',
  name: 'Test User',
  tier: 'free',
  totalCostUsd: 0.15,
  videoCount: 3,
  creditAdjustmentUsd: 0,
  effectiveUsd: 0.15,
  days: 7,
};

const ACCOUNT = {
  account: {
    accountId: 'b'.repeat(24),
    ownerType: 'user',
    ownerId: USER.userId,
    plan: { id: 'pro', status: 'active', source: 'manual', periodStart: null, periodEnd: null },
    free: { perWindow: 1, usedInWindow: 0, available: true, nextFreeAt: null, maxVideoMinutes: 30 },
    planMinutes: { cycleStart: null, cycleEnd: null, granted: 600, used: 0, remaining: 600 },
    credits: { available: 0 },
    caps: {},
    openHolds: 0,
    version: 1,
    updatedAt: '2026-10-27T08:30:15.123Z',
  },
  lots: [],
  holds: [],
  ledger: [],
  recurringGrants: [],
  ledgerNextBefore: null,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <UsersPage days={7} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

async function openDrawer() {
  fireEvent.click(await screen.findByTestId(`user-row-${USER.userId}`));
  return screen.findByRole('tab', { name: /account/i });
}

beforeEach(() => {
  mockCosts.mockReset().mockResolvedValue([USER]);
  mockCostDetail.mockReset().mockResolvedValue({ user: USER, daily: [], adjustments: [] });
  mockAccount.mockReset().mockResolvedValue(ACCOUNT);
});

describe('UsersPage Account tab', () => {
  it('should offer an Account tab in the user detail drawer', async () => {
    renderPage();

    expect(await openDrawer()).toBeTruthy();
  });

  it('should not read the billing account until the Account tab is opened', async () => {
    renderPage();
    await openDrawer();

    expect(mockAccount).not.toHaveBeenCalled();
  });

  it('should load the selected user billing account when the tab is opened', async () => {
    renderPage();

    fireEvent.click(await openDrawer());

    await waitFor(() => expect(screen.getByTestId('account-plan')).toBeTruthy());
    expect(mockAccount).toHaveBeenCalledWith(USER.userId, { limit: 50, before: undefined });
  });
});
