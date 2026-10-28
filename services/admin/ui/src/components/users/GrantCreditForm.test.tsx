import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { mockGrantCredit } = vi.hoisted(() => ({ mockGrantCredit: vi.fn() }));

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, api: { ...actual.api, users: { ...actual.api.users, grantCredit: mockGrantCredit } } };
});

import { ApiError } from '../../lib/api';
import { GrantCreditForm } from './GrantCreditForm';

const USER_ID = 'a'.repeat(24);

function renderForm() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <GrantCreditForm userId={USER_ID} />
    </QueryClientProvider>,
  );
  return { invalidate };
}

function fill(minutes: string, reason: string, expiry = '') {
  fireEvent.change(screen.getByTestId('grant-credit-minutes'), { target: { value: minutes } });
  fireEvent.change(screen.getByTestId('grant-credit-reason'), { target: { value: reason } });
  if (expiry) fireEvent.change(screen.getByTestId('grant-credit-expiry'), { target: { value: expiry } });
}

function submit() {
  const form = screen.getByTestId('grant-credit-minutes').closest('form');
  if (!form) throw new Error('grant form not found');
  fireEvent.submit(form);
}

describe('GrantCreditForm', () => {
  beforeEach(() => {
    mockGrantCredit.mockReset();
    localStorage.clear();
  });

  it('should have minutes, reason and an optional expiry, and no admin id field', () => {
    renderForm();

    expect(screen.getByLabelText('Minutes')).toBeTruthy();
    expect(screen.getByLabelText('Reason')).toBeTruthy();
    expect(screen.getByLabelText('Expires (optional)')).toBeTruthy();
    expect(screen.queryByLabelText(/admin/i)).toBeNull();
  });

  it('should not read or write the old admin_user_id from localStorage', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    mockGrantCredit.mockResolvedValue({ lotId: 'l', auditId: 'a' });
    renderForm();

    fill('30', 'goodwill');
    submit();
    await waitFor(() => expect(mockGrantCredit).toHaveBeenCalled());

    expect(getItem).not.toHaveBeenCalledWith('admin_user_id');
    expect(setItem).not.toHaveBeenCalledWith('admin_user_id', expect.anything());
  });

  it('should send minutes and reason, without any admin id or dollar amount', async () => {
    mockGrantCredit.mockResolvedValue({ lotId: 'l', auditId: 'a' });
    renderForm();

    fill('150', '  goodwill  ');
    submit();

    await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledOnce());
    expect(mockGrantCredit).toHaveBeenCalledWith(USER_ID, { minutes: 150, reason: 'goodwill' }, expect.any(String));
  });

  it('should send the expiry as the end of the chosen day when one is picked', async () => {
    mockGrantCredit.mockResolvedValue({ lotId: 'l', auditId: 'a' });
    renderForm();

    fill('60', 'promo', '2099-12-31');
    submit();

    await waitFor(() => expect(mockGrantCredit).toHaveBeenCalled());
    expect(mockGrantCredit.mock.calls[0][1]).toEqual({ minutes: 60, reason: 'promo', expiresAt: '2099-12-31T23:59:59.000Z' });
  });

  it.each([
    ['5000000', 'Minutes must be between 1 and 100000'],
    ['0', 'Minutes must be between 1 and 100000'],
    ['2.5', 'Enter a whole number of minutes'],
  ])('should show an error and not call the API for %s minutes', async (minutes, text) => {
    renderForm();

    fill(minutes, 'a reason');
    submit();

    await waitFor(() => expect(screen.getByText(text)).toBeTruthy());
    expect(mockGrantCredit).not.toHaveBeenCalled();
  });

  it('should require a reason', async () => {
    renderForm();

    fill('30', '   ');
    submit();

    await waitFor(() => expect(screen.getByText('Reason is required')).toBeTruthy());
    expect(mockGrantCredit).not.toHaveBeenCalled();
  });

  it('should refuse an expiry in the past', async () => {
    renderForm();

    fill('30', 'x', '2020-01-01');
    submit();

    await waitFor(() => expect(screen.getByText('Expiry must be in the future')).toBeTruthy());
    expect(mockGrantCredit).not.toHaveBeenCalled();
  });

  it('should confirm, clear the form and refresh the account after a grant', async () => {
    mockGrantCredit.mockResolvedValue({ lotId: 'l', auditId: 'a' });
    const { invalidate } = renderForm();

    fill('150', 'goodwill');
    submit();

    await waitFor(() => expect(screen.getByText('Granted 150 minutes.')).toBeTruthy());
    expect((screen.getByTestId('grant-credit-minutes') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('grant-credit-reason') as HTMLInputElement).value).toBe('');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['billing-account', USER_ID] });
  });

  it('should show the server\'s explanation when the grant is refused', async () => {
    mockGrantCredit.mockRejectedValue(new ApiError(400, 'API error: 400 Bad Request', 'expiresAt must be in the future'));
    renderForm();

    fill('30', 'x', '2099-01-01');
    submit();

    await waitFor(() => expect(screen.getByText('expiresAt must be in the future')).toBeTruthy());
  });

  it('should keep what was typed after a failure so the admin can retry', async () => {
    mockGrantCredit.mockRejectedValue(new ApiError(504, 'API error: 504'));
    renderForm();

    fill('30', 'keep me');
    submit();

    await waitFor(() => expect(mockGrantCredit).toHaveBeenCalled());
    expect((screen.getByTestId('grant-credit-reason') as HTMLInputElement).value).toBe('keep me');
  });

  describe('retries', () => {
    const keyOf = (call: number) => mockGrantCredit.mock.calls[call][2] as string;

    it('should reuse the idempotency key when the identical request is submitted again after a failure', async () => {
      mockGrantCredit.mockRejectedValueOnce(new ApiError(504, 'timeout')).mockResolvedValue({ lotId: 'l', auditId: 'a' });
      renderForm();
      fill('30', 'comp');

      submit();
      await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledTimes(1));
      submit();
      await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledTimes(2));

      expect(keyOf(1)).toBe(keyOf(0));
    });

    it('should use a new key when the request was edited after a failure', async () => {
      mockGrantCredit.mockRejectedValueOnce(new ApiError(504, 'timeout')).mockResolvedValue({ lotId: 'l', auditId: 'a' });
      renderForm();
      fill('30', 'comp');
      submit();
      await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledTimes(1));

      fill('45', 'comp');
      submit();
      await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledTimes(2));

      expect(keyOf(1)).not.toBe(keyOf(0));
    });

    it('should use a new key for the next grant after a success', async () => {
      mockGrantCredit.mockResolvedValue({ lotId: 'l', auditId: 'a' });
      renderForm();
      fill('30', 'comp');
      submit();
      await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledTimes(1));
      await waitFor(() => expect((screen.getByTestId('grant-credit-minutes') as HTMLInputElement).value).toBe(''));

      fill('30', 'comp');
      submit();
      await waitFor(() => expect(mockGrantCredit).toHaveBeenCalledTimes(2));

      expect(keyOf(1)).not.toBe(keyOf(0));
    });
  });
});
