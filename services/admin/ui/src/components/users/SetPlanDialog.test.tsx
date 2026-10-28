import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

const { mockSetPlan } = vi.hoisted(() => ({ mockSetPlan: vi.fn() }));

vi.mock('../../lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('../../lib/billing-actions')>('../../lib/billing-actions');
  return { ...actual, billingActions: { ...actual.billingActions, setPlan: mockSetPlan } };
});

import { ApiError } from '../../lib/api';
import { renderWithClient, setField, submitDialog, typeReason, USER_ID } from './dialog-test-utils';
import { SetPlanDialog } from './SetPlanDialog';

function setup(currentPlan: 'free' | 'pro' = 'free') {
  const onDone = vi.fn();
  const onClose = vi.fn();
  renderWithClient(<SetPlanDialog userId={USER_ID} currentPlan={currentPlan} onClose={onClose} onDone={onDone} />);
  return { onDone, onClose };
}

describe('SetPlanDialog', () => {
  beforeEach(() => {
    mockSetPlan.mockReset();
  });

  it('should be a dialog named Set plan', () => {
    setup();

    expect(screen.getByRole('dialog', { name: 'Set plan' })).toBeTruthy();
  });

  it.each([
    ['free', 'pro'],
    ['pro', 'free'],
  ] as const)('should start on the other plan when the account is %s', (current, expected) => {
    setup(current);

    expect((screen.getByTestId('plan-select') as HTMLSelectElement).value).toBe(expected);
  });

  it('should require a reason and send nothing without one', async () => {
    setup();

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Reason is required');
    expect(mockSetPlan).not.toHaveBeenCalled();
  });

  it('should send the plan and reason for the user', async () => {
    mockSetPlan.mockResolvedValue({ auditId: 'x' });
    setup();
    typeReason('invoice 1042');

    submitDialog();

    await waitFor(() => expect(mockSetPlan).toHaveBeenCalledOnce());
    expect(mockSetPlan).toHaveBeenCalledWith(USER_ID, { planId: 'pro', reason: 'invoice 1042' }, expect.any(String));
  });

  it('should send the end of the chosen day as the term end', async () => {
    mockSetPlan.mockResolvedValue({ auditId: 'x' });
    setup();
    setField('plan-period-end', '2099-01-31');
    typeReason('pilot');

    submitDialog();

    await waitFor(() => expect(mockSetPlan).toHaveBeenCalledOnce());
    expect(mockSetPlan.mock.calls[0][1]).toEqual({
      planId: 'pro',
      periodEnd: '2099-01-31T23:59:59.000Z',
      reason: 'pilot',
    });
  });

  it('should offer no term end, and send none, when setting the free plan', async () => {
    mockSetPlan.mockResolvedValue({ auditId: 'x' });
    setup();
    setField('plan-period-end', '2099-01-31');
    fireEvent.change(screen.getByTestId('plan-select'), { target: { value: 'free' } });
    typeReason('ended');

    expect(screen.queryByTestId('plan-period-end')).toBeNull();
    submitDialog();

    await waitFor(() => expect(mockSetPlan).toHaveBeenCalledOnce());
    expect(mockSetPlan.mock.calls[0][1]).toEqual({ planId: 'free', reason: 'ended' });
  });

  it('should reject a term end in the past before calling the server', async () => {
    setup();
    setField('plan-period-end', '2001-01-01');
    typeReason('pilot');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Term end must be in the future');
    expect(mockSetPlan).not.toHaveBeenCalled();
  });

  it('should show the explanation the server gave when it refuses', async () => {
    mockSetPlan.mockRejectedValue(new ApiError(400, 'API error: 400 Bad Request', 'periodEnd must be in the future'));
    setup();
    typeReason('pilot');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('periodEnd must be in the future');
  });

  it('should tell a machine-key session why it cannot change anything', async () => {
    mockSetPlan.mockRejectedValue(new ApiError(403, 'API error: 403 Forbidden', 'A named admin session is required'));
    setup();
    typeReason('pilot');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('A named admin session is required');
  });

  it('should report the new plan when it is applied', async () => {
    mockSetPlan.mockResolvedValue({ auditId: 'x' });
    const { onDone } = setup();
    typeReason('invoice');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Plan set to Pro.'));
  });
});
