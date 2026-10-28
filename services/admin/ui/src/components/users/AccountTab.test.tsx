import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import type {
  BillingAccountDetail,
  BillingAccountSnapshot,
  BillingCreditLot,
  BillingLedgerEntry,
} from '../../lib/billing';

const { mockAccount } = vi.hoisted(() => ({ mockAccount: vi.fn() }));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { ...actual.api, billing: { account: mockAccount } } };
});

import { AccountTab } from './AccountTab';

const USER_ID = 'a'.repeat(24);

function renderTab(actions?: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <AccountTab userId={USER_ID} actions={actions} />
    </QueryClientProvider>,
  );
}

function snapshot(overrides: Partial<BillingAccountSnapshot> = {}): BillingAccountSnapshot {
  return {
    accountId: 'b'.repeat(24),
    ownerType: 'user',
    ownerId: USER_ID,
    plan: {
      id: 'pro',
      status: 'active',
      source: 'manual',
      periodStart: '2026-10-01T00:00:00.000Z',
      periodEnd: '2027-10-01T00:00:00.000Z',
    },
    free: { perWindow: 1, usedInWindow: 0, available: true, nextFreeAt: null, maxVideoMinutes: 30 },
    planMinutes: {
      cycleStart: '2026-10-01T00:00:00.000Z',
      cycleEnd: '2026-11-01T00:00:00.000Z',
      granted: 600,
      used: 140,
      remaining: 460,
    },
    credits: { available: 90 },
    caps: {},
    openHolds: 0,
    version: 7,
    updatedAt: '2026-10-27T08:30:15.123Z',
    ...overrides,
  };
}

function lot(overrides: Partial<BillingCreditLot> = {}): BillingCreditLot {
  return {
    lotId: '1'.repeat(24),
    minutesGranted: 150,
    minutesRemaining: 90,
    source: { kind: 'payment', ref: 'txn_1' },
    priceUsdCents: 500,
    expiresAt: '2027-10-01T00:00:00.000Z',
    expired: false,
    createdAt: '2026-10-01T00:00:00.000Z',
    ...overrides,
  };
}

function entry(overrides: Partial<BillingLedgerEntry> = {}): BillingLedgerEntry {
  return {
    id: 'e'.repeat(24),
    userId: USER_ID,
    type: 'grant',
    bucket: 'credit',
    minutes: 150,
    source: { kind: 'admin', ref: 'c'.repeat(24) },
    idempotencyKey: 'admin:1',
    createdAt: '2026-10-20T09:00:00.000Z',
    ...overrides,
  };
}

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

beforeEach(() => {
  mockAccount.mockReset();
});

describe('AccountTab', () => {
  describe('summary', () => {
    it('should show the plan, its status and source', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      const plan = await screen.findByTestId('account-plan');

      expect(within(plan).getByText('Pro')).toBeTruthy();
      expect(plan.textContent).toContain('active, manual');
    });

    it('should show the free video as ready when one is open', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      const free = await screen.findByTestId('account-free-video');

      expect(within(free).getByText('Ready')).toBeTruthy();
    });

    it('should show when the next free video opens once the window is full', async () => {
      const used = snapshot({
        free: {
          perWindow: 1,
          usedInWindow: 1,
          available: false,
          nextFreeAt: '2026-10-29T10:00:00.000Z',
          maxVideoMinutes: 30,
        },
      });
      mockAccount.mockResolvedValue(detail({ account: used }));
      renderTab();

      const free = await screen.findByTestId('account-free-video');

      expect(free.textContent).toMatch(/Next at .*2026/);
    });

    it('should show the free video as disabled when its cap is zero', async () => {
      const off = snapshot({
        free: { perWindow: 0, usedInWindow: 0, available: false, nextFreeAt: null, maxVideoMinutes: 30 },
      });
      mockAccount.mockResolvedValue(detail({ account: off }));
      renderTab();

      const free = await screen.findByTestId('account-free-video');

      expect(within(free).getByText('Disabled')).toBeTruthy();
    });

    it('should show plan minutes used and remaining', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      const minutes = await screen.findByTestId('account-plan-minutes');

      expect(minutes.textContent).toContain('460 min left');
      expect(minutes.textContent).toContain('140 min of 600 min used');
    });

    it('should show no active cycle on the free plan', async () => {
      const free = snapshot({
        plan: { id: 'free', status: 'active', source: null, periodStart: null, periodEnd: null },
        planMinutes: { cycleStart: null, cycleEnd: null, granted: 0, used: 0, remaining: 0 },
      });
      mockAccount.mockResolvedValue(detail({ account: free }));
      renderTab();

      const minutes = await screen.findByTestId('account-plan-minutes');

      expect(minutes.textContent).toContain('No active cycle');
    });

    it('should show spendable credit minutes', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      const credits = await screen.findByTestId('account-credits');

      expect(within(credits).getByText('90 min')).toBeTruthy();
    });

    it('should flag a blocked account', async () => {
      mockAccount.mockResolvedValue(detail({ account: snapshot({ caps: { blocked: true } }) }));
      renderTab();

      const caps = await screen.findByTestId('account-caps');

      expect(within(caps).getByText('Blocked')).toBeTruthy();
    });

    it('should list the caps an admin set', async () => {
      const capped = snapshot({ caps: { maxVideoMinutes: 20, maxConcurrent: 2, blocked: null } });
      mockAccount.mockResolvedValue(detail({ account: capped }));
      renderTab();

      const caps = await screen.findByTestId('account-caps');

      expect(caps.textContent).toContain('max video 20 min, 2 runs at once');
    });

    it('should say plan defaults apply when no cap is set', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      const caps = await screen.findByTestId('account-caps');

      expect(caps.textContent).toContain('Plan defaults apply');
    });
  });

  describe('credit lots', () => {
    it('should render a row per lot with minutes remaining and expiry', async () => {
      mockAccount.mockResolvedValue(detail({ lots: [lot()] }));
      renderTab();

      const row = await screen.findByTestId(`lot-row-${'1'.repeat(24)}`);

      expect(row.textContent).toContain('90 min');
      expect(row.textContent).toContain('payment:txn_1');
      expect(row.textContent).toContain('$5.00');
    });

    it('should show Never for a lot that does not expire', async () => {
      mockAccount.mockResolvedValue(detail({ lots: [lot({ expiresAt: null })] }));
      renderTab();

      const row = await screen.findByTestId(`lot-row-${'1'.repeat(24)}`);

      expect(within(row).getByText('Never')).toBeTruthy();
    });

    it.each([
      ['Open', { minutesRemaining: 90, expired: false }],
      ['Expired', { minutesRemaining: 90, expired: true }],
      ['Used up', { minutesRemaining: 0, expired: false }],
    ])('should mark a lot as %s', async (status, overrides) => {
      mockAccount.mockResolvedValue(detail({ lots: [lot(overrides)] }));
      renderTab();

      const row = await screen.findByTestId(`lot-row-${'1'.repeat(24)}`);

      expect(within(row).getByText(status)).toBeTruthy();
    });

    it('should show an empty message when there are no lots', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      expect(await screen.findByText('No credit lots.')).toBeTruthy();
    });
  });

  describe('holds and recurring grants', () => {
    it('should render open holds with where their minutes came from', async () => {
      const hold = {
        requestId: 'req-1',
        userId: USER_ID,
        youtubeId: 'dQw4w9WgXcQ',
        durationSec: 1830,
        minutes: 31,
        split: { free: 0, plan: 11, credit: 20, lots: [] },
        freeVideoAt: null,
        planCycleStart: null,
        createdAt: '2026-10-28T11:55:00.000Z',
      };
      mockAccount.mockResolvedValue(detail({ holds: [hold] }));
      renderTab();

      const video = await screen.findByText('dQw4w9WgXcQ');
      const row = video.closest('tr');

      expect(row?.textContent).toContain('31 min');
      expect(row?.textContent).toContain('plan 11, credit 20');
    });

    it('should show an empty message when no run is in flight', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      expect(await screen.findByText('No runs in flight.')).toBeTruthy();
    });

    it('should render recurring grants with their status and reason', async () => {
      const grant = {
        grantId: 'g'.repeat(24),
        minutes: 300,
        every: 'month' as const,
        active: false,
        nextRunAt: '2026-11-01T00:00:00.000Z',
        lastRunAt: null,
        actorId: 'f'.repeat(24),
        auditId: 'c'.repeat(24),
        reason: 'eval account',
        createdAt: '2026-10-01T00:00:00.000Z',
        updatedAt: '2026-10-01T00:00:00.000Z',
      };
      mockAccount.mockResolvedValue(detail({ recurringGrants: [grant] }));
      renderTab();

      const reason = await screen.findByText('eval account');
      const row = reason.closest('tr');

      expect(row?.textContent).toContain('300 min');
      expect(row?.textContent).toContain('Paused');
    });

    it('should show an empty message when there are no recurring grants', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      expect(await screen.findByText('No recurring grants.')).toBeTruthy();
    });
  });

  describe('ledger', () => {
    it('should render type, bucket, signed minutes, reason and actor', async () => {
      const grant = entry({ reason: 'goodwill', actorId: 'f'.repeat(24) });
      mockAccount.mockResolvedValue(detail({ ledger: [grant] }));
      renderTab();

      const row = await screen.findByTestId(`ledger-row-${grant.id}`);

      expect(row.textContent).toContain('grant');
      expect(row.textContent).toContain('credit');
      expect(row.textContent).toContain('+150 min');
      expect(row.textContent).toContain('goodwill');
      expect(row.textContent).toContain('ffffff…ffff');
    });

    it('should show the run, its cost and a minus sign for a debit', async () => {
      const debit = entry({
        id: 'd'.repeat(24),
        type: 'debit',
        bucket: 'plan',
        minutes: -12,
        source: { kind: 'run', ref: 'req-9' },
        youtubeId: 'dQw4w9WgXcQ',
        costUsd: 0.0412,
        reason: null,
        actorId: null,
      });
      mockAccount.mockResolvedValue(detail({ ledger: [debit] }));
      renderTab();

      const row = await screen.findByTestId(`ledger-row-${debit.id}`);

      expect(row.textContent).toContain('-12 min');
      expect(row.textContent).toContain('run:req-9');
      expect(row.textContent).toContain('dQw4w9WgXcQ');
      expect(row.textContent).toContain('$0.0412');
    });

    it('should show an empty message when the ledger has no entries', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      expect(await screen.findByText('No ledger entries yet.')).toBeTruthy();
    });

    it('should request the first page without a cursor', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      await screen.findByTestId('account-tab');

      expect(mockAccount).toHaveBeenCalledWith(USER_ID, { limit: 50, before: undefined });
    });

    it('should not offer older entries on the last page', async () => {
      mockAccount.mockResolvedValue(detail({ ledger: [entry()] }));
      renderTab();

      await screen.findByTestId('account-tab');

      expect(screen.queryByRole('button', { name: /load older entries/i })).toBeNull();
    });

    it('should load the next page with the cursor and append its entries', async () => {
      const newer = entry({ id: '2'.repeat(24), idempotencyKey: 'admin:2', reason: 'newer entry' });
      const older = entry({ id: '3'.repeat(24), idempotencyKey: 'admin:3', reason: 'older entry' });
      mockAccount
        .mockResolvedValueOnce(detail({ ledger: [newer], ledgerNextBefore: '2026-10-20T09:00:00.000Z' }))
        .mockResolvedValueOnce(detail({ ledger: [older], ledgerNextBefore: null }));
      renderTab();

      fireEvent.click(await screen.findByRole('button', { name: /load older entries/i }));

      await waitFor(() => expect(screen.getByText('older entry')).toBeTruthy());
      expect(screen.getByText('newer entry')).toBeTruthy();
      expect(mockAccount).toHaveBeenLastCalledWith(USER_ID, {
        limit: 50,
        before: '2026-10-20T09:00:00.000Z',
      });
      expect(screen.queryByRole('button', { name: /load older entries/i })).toBeNull();
    });
  });

  describe('states', () => {
    it('should show the no-account message and no tables for a user without an account', async () => {
      mockAccount.mockResolvedValue(detail({ account: null }));
      renderTab();

      expect(await screen.findByTestId('account-none')).toBeTruthy();
      expect(screen.queryByTestId('account-summary')).toBeNull();
    });

    it('should show an error with retry when the account cannot be loaded', async () => {
      mockAccount.mockRejectedValue(new Error('API error: 500'));
      renderTab();

      expect(await screen.findByText('Failed to load billing account')).toBeTruthy();
    });

    it('should render the actions slot above the summary', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab(<button type="button">Grant minutes</button>);

      const slot = await screen.findByTestId('account-actions');

      expect(within(slot).getByRole('button', { name: 'Grant minutes' })).toBeTruthy();
    });

    it('should keep the actions slot when the user has no account yet', async () => {
      mockAccount.mockResolvedValue(detail({ account: null }));
      renderTab(<button type="button">Set plan</button>);

      const slot = await screen.findByTestId('account-actions');

      expect(within(slot).getByRole('button', { name: 'Set plan' })).toBeTruthy();
    });

    it('should omit the actions slot when no actions are given', async () => {
      mockAccount.mockResolvedValue(detail());
      renderTab();

      await screen.findByTestId('account-tab');

      expect(screen.queryByTestId('account-actions')).toBeNull();
    });
  });
});
