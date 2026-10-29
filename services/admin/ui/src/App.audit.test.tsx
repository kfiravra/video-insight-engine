import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const { mockList } = vi.hoisted(() => ({ mockList: vi.fn() }));

vi.mock('./lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('./lib/billing-actions')>('./lib/billing-actions');
  return { ...actual, auditApi: { list: mockList } };
});

import App from './App';

function renderAppAt(path: string) {
  window.history.pushState({}, 'Test page', path);
  return render(<App />);
}

describe('Audit log route', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('admin_api_key', 'session-token');
    mockList.mockReset().mockResolvedValue({ entries: [], nextBefore: null });
  });

  it('should render the audit log at /audit-log', async () => {
    renderAppAt('/audit-log');

    expect(await screen.findByRole('heading', { name: 'Audit log' })).toBeTruthy();
  });

  it('should list Audit in the navigation, pointing at the audit log', async () => {
    renderAppAt('/audit-log');

    const link = await screen.findByRole('link', { name: 'Audit' });

    expect(link.getAttribute('href')).toBe('/audit-log');
  });

  it('should read the filter from the URL so a link from the Account tab lands on that user', async () => {
    renderAppAt(`/audit-log?targetId=${'a'.repeat(24)}`);

    await screen.findByRole('heading', { name: 'Audit log' });

    expect(mockList).toHaveBeenCalledWith({ targetId: 'a'.repeat(24), limit: 50, before: undefined });
  });
});
