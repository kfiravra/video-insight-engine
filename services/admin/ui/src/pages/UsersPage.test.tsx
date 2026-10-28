import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { UserCostRow, UserActivityResponse } from '../lib/api';

// Mock the API module
const mockCosts = vi.fn();
const mockCostDetail = vi.fn();
const mockActivity = vi.fn();
const mockGrantCredit = vi.fn();
const mockBillingAccount = vi.fn();

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      users: {
        costs: mockCosts,
        costDetail: mockCostDetail,
        activity: mockActivity,
        grantCredit: mockGrantCredit,
      },
      billing: { account: mockBillingAccount },
    },
  };
});

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

const USER_ROW: UserCostRow = {
  userId: 'user-abc123',
  email: 'test@example.com',
  name: 'Test User',
  tier: 'free',
  totalCostUsd: 0.15,
  videoCount: 3,
  creditAdjustmentUsd: 0,
  effectiveUsd: 0.15,
  days: 7,
};

const ACTIVITY: UserActivityResponse = {
  userId: 'user-abc123',
  videos: [
    {
      userVideoId: 'uv-1',
      videoSummaryId: 'vs-1',
      youtubeId: 'ytid-001',
      title: 'Test Video Title',
      channel: 'Test Channel',
      duration: 300,
      thumbnailUrl: null,
      status: 'completed',
      addedAt: '2026-06-01T10:00:00.000Z',
    },
  ],
  assistantCalls: [
    {
      id: 'call-1',
      feature: 'assistant:rag_chat',
      videoId: 'ytid-001',
      model: 'claude-3-5-haiku',
      costUsd: 0.001,
      tokensIn: 500,
      tokensOut: 100,
      requestId: 'req-xyz',
      timestamp: '2026-06-01T11:00:00.000Z',
    },
  ],
  costTimeline: [
    {
      date: '2026-06-01',
      totalCostUsd: 0.05,
      creditAdjustmentUsd: 0,
      effectiveUsd: 0.05,
    },
  ],
};

const COST_DETAIL = {
  user: USER_ROW,
  daily: [
    { date: '2026-06-01', totalCostUsd: 0.05, videoCount: 1, creditAdjustmentUsd: 0, effectiveUsd: 0.05 },
  ],
  adjustments: [],
};

beforeEach(() => {
  mockCosts.mockReset();
  mockCostDetail.mockReset();
  mockActivity.mockReset();
  mockGrantCredit.mockReset();
  mockBillingAccount.mockReset();
  // The Account tab loads the ledger view; these tests are about the grant form beside it.
  mockBillingAccount.mockReturnValue(new Promise(() => undefined));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('UsersPage', () => {
  it('should export UsersPage component', async () => {
    const mod = await import('./UsersPage');
    expect(typeof mod.UsersPage).toBe('function');
  });

  it('should render user rows after data loads', async () => {
    mockCosts.mockResolvedValue([USER_ROW]);
    const { UsersPage } = await import('./UsersPage');
    const wrapper = makeWrapper();
    render(<UsersPage days={7} />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText('test@example.com')).toBeTruthy();
    });
  });

  it('should filter users by email', async () => {
    mockCosts.mockResolvedValue([USER_ROW]);
    const { UsersPage } = await import('./UsersPage');
    const wrapper = makeWrapper();
    render(<UsersPage days={7} />, { wrapper });
    await waitFor(() => screen.getByText('test@example.com'));

    const input = screen.getByTestId('users-filter');
    fireEvent.change(input, { target: { value: 'nomatch' } });
    expect(screen.queryByText('test@example.com')).toBeNull();
  });
});

describe('User-360 drawer tabs', () => {
  it('should open the User-360 drawer when a user row is clicked', async () => {
    mockCosts.mockResolvedValue([USER_ROW]);
    mockCostDetail.mockResolvedValue(COST_DETAIL);
    mockActivity.mockResolvedValue(ACTIVITY);
    const { UsersPage } = await import('./UsersPage');
    const wrapper = makeWrapper();
    render(<UsersPage days={7} />, { wrapper });
    await waitFor(() => screen.getByTestId(`user-row-${USER_ROW.userId}`));

    fireEvent.click(screen.getByTestId(`user-row-${USER_ROW.userId}`));
    await waitFor(() => {
      // Drawer should be open with tab navigation
      expect(screen.getByRole('dialog')).toBeTruthy();
    });
  });

  it('should show Overview tab by default in the drawer', async () => {
    mockCosts.mockResolvedValue([USER_ROW]);
    mockCostDetail.mockResolvedValue(COST_DETAIL);
    mockActivity.mockResolvedValue(ACTIVITY);
    const { UsersPage } = await import('./UsersPage');
    const wrapper = makeWrapper();
    render(<UsersPage days={7} />, { wrapper });
    await waitFor(() => screen.getByTestId(`user-row-${USER_ROW.userId}`));
    fireEvent.click(screen.getByTestId(`user-row-${USER_ROW.userId}`));

    await waitFor(() => {
      // Overview tab should be visible/active
      expect(screen.getByRole('tab', { name: /overview/i })).toBeTruthy();
    });
  });

  it('should switch to Videos tab and show video title', async () => {
    mockCosts.mockResolvedValue([USER_ROW]);
    mockCostDetail.mockResolvedValue(COST_DETAIL);
    mockActivity.mockResolvedValue(ACTIVITY);
    const { UsersPage } = await import('./UsersPage');
    const wrapper = makeWrapper();
    render(<UsersPage days={7} />, { wrapper });
    await waitFor(() => screen.getByTestId(`user-row-${USER_ROW.userId}`));
    fireEvent.click(screen.getByTestId(`user-row-${USER_ROW.userId}`));

    // Click Videos tab
    await waitFor(() => screen.getByRole('tab', { name: /videos/i }));
    fireEvent.click(screen.getByRole('tab', { name: /videos/i }));

    await waitFor(() => {
      expect(screen.getByText('Test Video Title')).toBeTruthy();
    });
  });

  it('should switch to Assistant tab and show assistant call feature', async () => {
    mockCosts.mockResolvedValue([USER_ROW]);
    mockCostDetail.mockResolvedValue(COST_DETAIL);
    mockActivity.mockResolvedValue(ACTIVITY);
    const { UsersPage } = await import('./UsersPage');
    const wrapper = makeWrapper();
    render(<UsersPage days={7} />, { wrapper });
    await waitFor(() => screen.getByTestId(`user-row-${USER_ROW.userId}`));
    fireEvent.click(screen.getByTestId(`user-row-${USER_ROW.userId}`));

    await waitFor(() => screen.getByRole('tab', { name: /assistant/i }));
    fireEvent.click(screen.getByRole('tab', { name: /assistant/i }));

    await waitFor(() => {
      expect(screen.getByText('assistant:rag_chat')).toBeTruthy();
    });
  });

  async function openUserTab(tabName: RegExp) {
    mockCosts.mockResolvedValue([USER_ROW]);
    mockCostDetail.mockResolvedValue(COST_DETAIL);
    mockActivity.mockResolvedValue(ACTIVITY);
    const { UsersPage } = await import('./UsersPage');
    render(<UsersPage days={7} />, { wrapper: makeWrapper() });
    await waitFor(() => screen.getByTestId(`user-row-${USER_ROW.userId}`));
    fireEvent.click(screen.getByTestId(`user-row-${USER_ROW.userId}`));
    await waitFor(() => screen.getByRole('tab', { name: tabName }));
    fireEvent.click(screen.getByRole('tab', { name: tabName }));
  }

  it('should show the grant-minutes form on the Account tab', async () => {
    await openUserTab(/account/i);

    await waitFor(() => {
      expect(screen.getByTestId('grant-credit-minutes')).toBeTruthy();
    });
  });

  it('should grant minutes for the selected user with no admin id to type', async () => {
    mockGrantCredit.mockResolvedValue({ lotId: 'l', auditId: 'a' });
    await openUserTab(/account/i);
    await waitFor(() => screen.getByTestId('grant-credit-minutes'));

    fireEvent.change(screen.getByTestId('grant-credit-minutes'), { target: { value: '150' } });
    fireEvent.change(screen.getByTestId('grant-credit-reason'), { target: { value: 'goodwill' } });
    const form = screen.getByTestId('grant-credit-minutes').closest('form');
    if (!form) throw new Error('grant form not found');
    fireEvent.submit(form);

    await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledOnce());
    expect(mockGrantCredit).toHaveBeenCalledWith(USER_ROW.userId, { minutes: 150, reason: 'goodwill' }, expect.any(String));
    expect(screen.queryByTestId('grant-credit-admin-id')).toBeNull();
  });

  it('should surface a range error and not call grantCredit when minutes are out of range', async () => {
    await openUserTab(/account/i);
    await waitFor(() => screen.getByTestId('grant-credit-minutes'));

    fireEvent.change(screen.getByTestId('grant-credit-minutes'), { target: { value: '5000000' } });
    fireEvent.change(screen.getByTestId('grant-credit-reason'), { target: { value: 'over the cap' } });
    const form = screen.getByTestId('grant-credit-minutes').closest('form');
    if (!form) throw new Error('grant form not found');
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText('Minutes must be between 1 and 100000')).toBeTruthy();
    });
    expect(mockGrantCredit).not.toHaveBeenCalled();
  });

  it('should keep the Adjustments tab as read-only USD history, with no grant form', async () => {
    await openUserTab(/adjustments/i);

    await waitFor(() => {
      expect(screen.getByText('Past USD adjustments')).toBeTruthy();
    });
    expect(screen.queryByTestId('grant-credit-minutes')).toBeNull();
  });
});
