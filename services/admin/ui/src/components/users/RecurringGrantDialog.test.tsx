import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

const { mockCreate, mockDelete } = vi.hoisted(() => ({ mockCreate: vi.fn(), mockDelete: vi.fn() }));

vi.mock('../../lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('../../lib/billing-actions')>('../../lib/billing-actions');
  return {
    ...actual,
    billingActions: { ...actual.billingActions, createRecurringGrant: mockCreate, deleteRecurringGrant: mockDelete },
  };
});

import { ApiError } from '../../lib/api';
import type { BillingRecurringGrant } from '../../lib/billing';
import { DeleteGrantButton, RecurringGrantDialog } from './RecurringGrantDialog';
import { renderWithClient, setField, submitDialog, typeReason, USER_ID } from './dialog-test-utils';

const GRANT: BillingRecurringGrant = {
  grantId: 'g'.repeat(24),
  minutes: 300,
  every: 'month',
  active: true,
  nextRunAt: '2026-11-01T00:00:00.000Z',
  lastRunAt: null,
  actorId: 'f'.repeat(24),
  auditId: 'c'.repeat(24),
  reason: 'eval account',
  createdAt: '2026-10-01T00:00:00.000Z',
  updatedAt: '2026-10-01T00:00:00.000Z',
};

describe('RecurringGrantDialog', () => {
  beforeEach(() => {
    mockCreate.mockReset();
    mockDelete.mockReset();
  });

  function setup() {
    const onDone = vi.fn();
    renderWithClient(<RecurringGrantDialog userId={USER_ID} onClose={vi.fn()} onDone={onDone} />);
    return { onDone };
  }

  it('should require a reason and send nothing without one', async () => {
    setup();
    setField('recurring-minutes', '300');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Reason is required');
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('should create an active monthly grant', async () => {
    mockCreate.mockResolvedValue({ auditId: 'x', grantId: 'g' });
    setup();
    setField('recurring-minutes', '300');
    typeReason('demo account');

    submitDialog();

    await waitFor(() => expect(mockCreate).toHaveBeenCalledOnce());
    expect(mockCreate).toHaveBeenCalledWith(
      USER_ID,
      { minutes: 300, every: 'month', active: true, reason: 'demo account' },
      expect.any(String),
    );
  });

  it('should create an inactive grant when the box is unticked', async () => {
    mockCreate.mockResolvedValue({ auditId: 'x', grantId: 'g' });
    setup();
    setField('recurring-minutes', '300');
    fireEvent.click(screen.getByTestId('recurring-active'));
    typeReason('paused');

    submitDialog();

    await waitFor(() => expect(mockCreate).toHaveBeenCalledOnce());
    expect(mockCreate.mock.calls[0][1]).toMatchObject({ active: false });
  });

  it.each(['0', '-3', ''])('should reject "%s" minutes before calling the server', async (minutes) => {
    setup();
    setField('recurring-minutes', minutes);
    typeReason('demo');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toMatch(/Minutes must be/);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('should show the explanation the server gave when it refuses', async () => {
    mockCreate.mockRejectedValue(new ApiError(400, 'API error: 400', 'minutes: Number must be at most 100000'));
    setup();
    setField('recurring-minutes', '300');
    typeReason('demo');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('minutes: Number must be at most 100000');
  });

  it('should report the grant that was added', async () => {
    mockCreate.mockResolvedValue({ auditId: 'x', grantId: 'g' });
    const { onDone } = setup();
    setField('recurring-minutes', '300');
    typeReason('demo');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Recurring grant of 300 minutes a month added.'));
  });
});

describe('DeleteGrantButton', () => {
  beforeEach(() => {
    mockDelete.mockReset();
  });

  function setup() {
    const onDone = vi.fn();
    renderWithClient(<DeleteGrantButton userId={USER_ID} grant={GRANT} onDone={onDone} />);
    return { onDone };
  }

  function openDialog() {
    fireEvent.click(screen.getByRole('button', { name: 'Delete recurring grant of 300 minutes' }));
  }

  it('should open no dialog until the button is pressed', () => {
    setup();

    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('should name the grant in the dialog', () => {
    setup();

    openDialog();

    expect(screen.getByRole('dialog', { name: 'Delete recurring grant' }).textContent).toContain('300 minutes');
  });

  it('should require a reason and send nothing without one', async () => {
    setup();
    openDialog();

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Reason is required');
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it('should delete that grant for that user with the reason', async () => {
    mockDelete.mockResolvedValue({ auditId: 'x' });
    setup();
    openDialog();
    typeReason('eval over');

    submitDialog();

    await waitFor(() => expect(mockDelete).toHaveBeenCalledOnce());
    expect(mockDelete).toHaveBeenCalledWith(USER_ID, GRANT.grantId, { reason: 'eval over' }, expect.any(String));
  });

  it('should close the dialog and report the deletion', async () => {
    mockDelete.mockResolvedValue({ auditId: 'x' });
    const { onDone } = setup();
    openDialog();
    typeReason('eval over');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Recurring grant deleted.'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('should show the explanation the server gave and keep the dialog open', async () => {
    mockDelete.mockRejectedValue(new ApiError(404, 'API error: 404', 'Recurring grant not found'));
    setup();
    openDialog();
    typeReason('eval over');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('Recurring grant not found');
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});
