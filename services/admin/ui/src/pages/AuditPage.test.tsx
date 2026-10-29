import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

const { mockList } = vi.hoisted(() => ({ mockList: vi.fn() }));

vi.mock('../lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('../lib/billing-actions')>('../lib/billing-actions');
  return { ...actual, auditApi: { list: mockList } };
});

import type { AuditEntry } from '../lib/billing-actions';
import { AuditPage } from './AuditPage';

const USER_ID = 'a'.repeat(24);
const ADMIN_ID = 'f'.repeat(24);

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: 'e1'.padEnd(24, '0'),
    actorId: ADMIN_ID,
    actorEmail: 'admin@example.com',
    action: 'billing.plan.set',
    targetType: 'user',
    targetId: USER_ID,
    before: { plan: { id: 'free' }, version: 1 },
    after: { plan: { id: 'pro' }, version: 2 },
    reason: 'invoice 1042',
    createdAt: '2026-10-27T09:00:00.000Z',
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="search">{location.search}</output>;
}

function renderPage(initialEntry = '/audit-log') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuditPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AuditPage', () => {
  beforeEach(() => {
    mockList.mockReset().mockResolvedValue({ entries: [entry()], nextBefore: null });
  });

  describe('rows', () => {
    it('should show the action, actor, target and reason of each entry', async () => {
      renderPage();

      const row = await screen.findByTestId(`audit-row-${entry().id}`);

      expect(row.textContent).toContain('billing.plan.set');
      expect(row.textContent).toContain('admin@example.com');
      expect(row.textContent).toContain('user:');
      expect(row.textContent).toContain('aaaaaa…aaaa');
      expect(row.textContent).toContain('invoice 1042');
    });

    it('should show a short actor id when no email was recorded', async () => {
      mockList.mockResolvedValue({ entries: [entry({ actorEmail: undefined })], nextBefore: null });
      renderPage();

      const row = await screen.findByTestId(`audit-row-${entry().id}`);

      expect(row.textContent).toContain('ffffff…ffff');
    });

    it('should show system actors as they are', async () => {
      mockList.mockResolvedValue({
        entries: [entry({ actorId: 'system:boot', actorEmail: undefined, action: 'roles.seed.promote' })],
        nextBefore: null,
      });
      renderPage();

      const row = await screen.findByTestId(`audit-row-${entry().id}`);

      expect(row.textContent).toContain('system:boot');
    });

    it('should show a dash when there is no reason', async () => {
      mockList.mockResolvedValue({ entries: [entry({ reason: undefined })], nextBefore: null });
      renderPage();

      const row = await screen.findByTestId(`audit-row-${entry().id}`);

      expect(within(row).getAllByRole('cell')[4].textContent).toBe('—');
    });

    it('should keep the order the server sent, newest first', async () => {
      mockList.mockResolvedValue({
        entries: [entry({ id: 'n'.repeat(24), action: 'queue.replay' }), entry({ id: 'o'.repeat(24), action: 'users.delete' })],
        nextBefore: null,
      });
      renderPage();

      await screen.findByTestId(`audit-row-${'n'.repeat(24)}`);

      expect(screen.getAllByTestId(/^audit-row-/).map((row) => row.getAttribute('data-testid'))).toEqual([
        `audit-row-${'n'.repeat(24)}`,
        `audit-row-${'o'.repeat(24)}`,
      ]);
    });

    it('should say when nothing matches', async () => {
      mockList.mockResolvedValue({ entries: [], nextBefore: null });
      renderPage();

      expect(await screen.findByText('No audit entries match.')).toBeTruthy();
    });

    it('should offer a retry when the log cannot be loaded', async () => {
      mockList.mockRejectedValue(new Error('API error: 500'));
      renderPage();

      expect(await screen.findByText('Failed to load the audit log')).toBeTruthy();
    });
  });

  describe('before and after', () => {
    it('should hide the changes until asked', async () => {
      renderPage();

      await screen.findByTestId(`audit-row-${entry().id}`);

      expect(screen.queryByTestId(`audit-diff-${entry().id}`)).toBeNull();
    });

    it('should show only the fields that changed when the toggle is opened', async () => {
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: 'Show changes for billing.plan.set' }));

      const diff = await screen.findByTestId(`audit-diff-${entry().id}`);

      expect(within(diff).getAllByRole('row').slice(1).map((row) => row.textContent)).toEqual(['plan.idfreepro']);
    });

    it('should hide the changes again', async () => {
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: 'Show changes for billing.plan.set' }));
      await screen.findByTestId(`audit-diff-${entry().id}`);

      fireEvent.click(screen.getByRole('button', { name: 'Hide changes for billing.plan.set' }));

      expect(screen.queryByTestId(`audit-diff-${entry().id}`)).toBeNull();
    });

    it('should say so when the action recorded no before or after', async () => {
      mockList.mockResolvedValue({ entries: [entry({ before: undefined, after: undefined })], nextBefore: null });
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Show changes/ }));

      expect(await screen.findByText('No before/after recorded for this action.')).toBeTruthy();
    });

    it('should mark the toggle expanded while the changes show', async () => {
      renderPage();
      const toggle = await screen.findByRole('button', { name: /Show changes/ });

      fireEvent.click(toggle);

      expect(screen.getByRole('button', { name: /Hide changes/ }).getAttribute('aria-expanded')).toBe('true');
    });
  });

  describe('paging', () => {
    it('should ask for the first page without a cursor', async () => {
      renderPage();

      await screen.findByTestId(`audit-row-${entry().id}`);

      expect(mockList).toHaveBeenCalledWith({ limit: 50, before: undefined });
    });

    it('should offer older entries only when the server sent a cursor', async () => {
      renderPage();

      await screen.findByTestId(`audit-row-${entry().id}`);

      expect(screen.queryByRole('button', { name: 'Load older entries' })).toBeNull();
    });

    it('should load the next page with the cursor and append it', async () => {
      const cursor = '2026-10-27T09:00:00.000Z';
      mockList
        .mockResolvedValueOnce({ entries: [entry({ id: 'n'.repeat(24), action: 'queue.replay' })], nextBefore: cursor })
        .mockResolvedValueOnce({ entries: [entry({ id: 'o'.repeat(24), action: 'users.delete' })], nextBefore: null });
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Load older entries' }));

      await screen.findByTestId(`audit-row-${'o'.repeat(24)}`);
      expect(mockList).toHaveBeenLastCalledWith({ limit: 50, before: cursor });
      expect(screen.getByTestId(`audit-row-${'n'.repeat(24)}`)).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Load older entries' })).toBeNull();
    });
  });

  describe('filters', () => {
    it('should start from the filters in the URL', async () => {
      renderPage(`/audit-log?targetId=${USER_ID}&actorId=${ADMIN_ID}`);

      await screen.findByTestId(`audit-row-${entry().id}`);

      expect(mockList).toHaveBeenCalledWith({ targetId: USER_ID, actorId: ADMIN_ID, limit: 50, before: undefined });
      expect((screen.getByTestId('audit-filter-target') as HTMLInputElement).value).toBe(USER_ID);
    });

    it('should filter by target id and put the filter in the URL', async () => {
      renderPage();
      await screen.findByTestId(`audit-row-${entry().id}`);

      fireEvent.change(screen.getByTestId('audit-filter-target'), { target: { value: `  ${USER_ID} ` } });
      fireEvent.click(screen.getByRole('button', { name: 'Filter' }));

      await waitFor(() =>
        expect(mockList).toHaveBeenLastCalledWith({ targetId: USER_ID, limit: 50, before: undefined }),
      );
      expect(screen.getByTestId('search').textContent).toBe(`?targetId=${USER_ID}`);
    });

    it('should filter by actor id', async () => {
      renderPage();
      await screen.findByTestId(`audit-row-${entry().id}`);

      fireEvent.change(screen.getByTestId('audit-filter-actor'), { target: { value: ADMIN_ID } });
      fireEvent.click(screen.getByRole('button', { name: 'Filter' }));

      await waitFor(() => expect(mockList).toHaveBeenLastCalledWith({ actorId: ADMIN_ID, limit: 50, before: undefined }));
    });

    it('should clear the filters', async () => {
      renderPage(`/audit-log?targetId=${USER_ID}`);
      await screen.findByTestId(`audit-row-${entry().id}`);

      fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

      await waitFor(() => expect(mockList).toHaveBeenLastCalledWith({ limit: 50, before: undefined }));
      expect(screen.getByTestId('search').textContent).toBe('');
    });
  });
});
