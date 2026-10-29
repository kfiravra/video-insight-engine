import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const { mockAccount, mockSetPlan, mockDelete, mockGrant } = vi.hoisted(() => ({
  mockAccount: vi.fn(),
  mockSetPlan: vi.fn(),
  mockDelete: vi.fn(),
  mockGrant: vi.fn(),
}));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    api: {
      ...actual.api,
      billing: { account: mockAccount },
      users: { ...actual.api.users, grantCredit: mockGrant },
    },
  };
});

vi.mock('../../lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('../../lib/billing-actions')>('../../lib/billing-actions');
  return {
    ...actual,
    billingActions: { ...actual.billingActions, setPlan: mockSetPlan, deleteRecurringGrant: mockDelete },
  };
});

import type { BillingAccountDetail, BillingAccountSnapshot } from '../../lib/billing';
import { AccountPanel } from './AccountPanel';

const USER_ID = 'a'.repeat(24);

function snapshot(overrides: Partial<BillingAccountSnapshot> = {}): BillingAccountSnapshot {
  return {
    accountId: 'b'.repeat(24),
    ownerType: 'user',
    ownerId: USER_ID,
    plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
    free: { perWindow: 1, usedInWindow: 0, available: true, nextFreeAt: null, maxVideoMinutes: 30 },
    planMinutes: { cycleStart: null, cycleEnd: null, granted: 0, used: 0, remaining: 0 },
    credits: { available: 0 },
    caps: {},
    openHolds: 0,
    version: 1,
    updatedAt: '2026-10-27T08:30:15.123Z',
    ...overrides,
  };
}

const GRANT = {
  grantId: 'g'.repeat(24),
  minutes: 300,
  every: 'month' as const,
  active: true,
  nextRunAt: '2026-11-01T00:00:00.000Z',
  lastRunAt: null,
  actorId: 'f'.repeat(24),
  auditId: 'c'.repeat(24),
  reason: 'eval account',
  createdAt: '2026-10-01T00:00:00.000Z',
  updatedAt: '2026-10-01T00:00:00.000Z',
};

function detail(overrides: Partial<BillingAccountDetail> = {}): BillingAccountDetail {
  return {
    account: snapshot(),
    lots: [],
    holds: [],
    ledger: [],
    recurringGrants: [],
    ledgerNextBefore: null,
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AccountPanel userId={USER_ID} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openAction(name: string) {
  fireEvent.click(await screen.findByRole('button', { name }));
}

function reasonAndSubmit(reason: string) {
  fireEvent.change(screen.getByTestId('action-reason'), { target: { value: reason } });
  const form = screen.getByTestId('action-reason').closest('form');
  if (!form) throw new Error('dialog form not found');
  fireEvent.submit(form);
}

describe('AccountPanel', () => {
  beforeEach(() => {
    mockAccount.mockReset().mockResolvedValue(detail());
    mockSetPlan.mockReset();
    mockDelete.mockReset();
    mockGrant.mockReset();
  });

  describe('action buttons', () => {
    it('should offer every action once the account has loaded', async () => {
      renderPanel();

      await screen.findByTestId('account-tab');
      const names = ['Set plan', 'Caps', 'Adjust', 'Add recurring grant'];

      expect(names.map((name) => screen.queryByRole('button', { name }) !== null)).toEqual([true, true, true, true]);
    });

    it('should keep granting minutes as the form above the account', async () => {
      renderPanel();

      expect(await screen.findByTestId('grant-credit-minutes')).toBeTruthy();
    });

    it('should still offer the actions to a user with no account yet', async () => {
      mockAccount.mockResolvedValue(detail({ account: null }));
      renderPanel();

      expect(await screen.findByRole('button', { name: 'Set plan' })).toBeTruthy();
    });

    it('should link to the audit log filtered to this user', async () => {
      renderPanel();

      const link = await screen.findByRole('link', { name: 'Audit log' });

      expect(link.getAttribute('href')).toBe(`/audit-log?targetId=${USER_ID}`);
    });

    it('should open one dialog at a time', async () => {
      renderPanel();
      await openAction('Set plan');

      expect(screen.getAllByRole('dialog')).toHaveLength(1);
    });

    it('should open the caps dialog on the account caps', async () => {
      mockAccount.mockResolvedValue(detail({ account: snapshot({ caps: { maxConcurrent: 2 } }) }));
      renderPanel();

      await openAction('Caps');

      expect((screen.getByTestId('cap-maxConcurrent') as HTMLInputElement).value).toBe('2');
    });

    it('should open the plan dialog on the other plan when the account is Pro', async () => {
      const pro = snapshot({ plan: { id: 'pro', status: 'active', source: 'manual', periodStart: null, periodEnd: null } });
      mockAccount.mockResolvedValue(detail({ account: pro }));
      renderPanel();

      await openAction('Set plan');

      expect((screen.getByTestId('plan-select') as HTMLSelectElement).value).toBe('free');
    });
  });

  describe('after a change', () => {
    it('should refetch the account, close the dialog and say what happened', async () => {
      mockSetPlan.mockResolvedValue({ auditId: 'x' });
      renderPanel();
      await openAction('Set plan');
      await waitFor(() => expect(mockAccount).toHaveBeenCalledTimes(1));

      reasonAndSubmit('invoice');

      await waitFor(() => expect(mockAccount).toHaveBeenCalledTimes(2));
      expect((await screen.findByRole('status')).textContent).toBe('Plan set to Pro.');
      expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('should keep the dialog open and show the server error when the change is refused', async () => {
      const { ApiError } = await import('../../lib/api');
      mockSetPlan.mockRejectedValue(new ApiError(403, 'API error: 403', 'A named admin session is required'));
      renderPanel();
      await openAction('Set plan');

      reasonAndSubmit('invoice');

      expect((await screen.findByTestId('action-error')).textContent).toBe('A named admin session is required');
      expect(screen.getByRole('dialog')).toBeTruthy();
    });

    it('should not refetch the account when the change is refused', async () => {
      const { ApiError } = await import('../../lib/api');
      mockSetPlan.mockRejectedValue(new ApiError(403, 'API error: 403', 'nope'));
      renderPanel();
      await openAction('Set plan');
      await waitFor(() => expect(mockAccount).toHaveBeenCalledTimes(1));

      reasonAndSubmit('invoice');
      await screen.findByTestId('action-error');

      expect(mockAccount).toHaveBeenCalledTimes(1);
    });
  });

  describe('recurring grants', () => {
    beforeEach(() => {
      mockAccount.mockResolvedValue(detail({ recurringGrants: [GRANT] }));
    });

    it('should put a Delete button on each recurring grant', async () => {
      renderPanel();

      expect(await screen.findByTestId(`delete-grant-${GRANT.grantId}`)).toBeTruthy();
    });

    it('should delete the grant, refetch the account and say so', async () => {
      mockDelete.mockResolvedValue({ auditId: 'x' });
      renderPanel();
      fireEvent.click(await screen.findByTestId(`delete-grant-${GRANT.grantId}`));
      await waitFor(() => expect(mockAccount).toHaveBeenCalledTimes(1));

      reasonAndSubmit('eval over');

      await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(USER_ID, GRANT.grantId, { reason: 'eval over' }, expect.any(String)));
      await waitFor(() => expect(mockAccount).toHaveBeenCalledTimes(2));
      expect((await screen.findByRole('status')).textContent).toBe('Recurring grant deleted.');
    });
  });
});
