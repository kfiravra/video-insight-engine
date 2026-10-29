import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

const { mockAdjust } = vi.hoisted(() => ({ mockAdjust: vi.fn() }));

vi.mock('../../lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('../../lib/billing-actions')>('../../lib/billing-actions');
  return { ...actual, billingActions: { ...actual.billingActions, adjust: mockAdjust } };
});

import { ApiError } from '../../lib/api';
import { AdjustDialog } from './AdjustDialog';
import { renderWithClient, setField, submitDialog, typeReason, USER_ID } from './dialog-test-utils';

function setup() {
  const onDone = vi.fn();
  renderWithClient(<AdjustDialog userId={USER_ID} onClose={vi.fn()} onDone={onDone} />);
  return { onDone };
}

describe('AdjustDialog', () => {
  beforeEach(() => {
    mockAdjust.mockReset();
  });

  it('should require a reason and send nothing without one', async () => {
    setup();
    setField('adjust-minutes', '30');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Reason is required');
    expect(mockAdjust).not.toHaveBeenCalled();
  });

  it('should send a positive credit adjustment', async () => {
    mockAdjust.mockResolvedValue({ auditId: 'x' });
    setup();
    setField('adjust-minutes', '30');
    typeReason('support credit');

    submitDialog();

    await waitFor(() => expect(mockAdjust).toHaveBeenCalledOnce());
    expect(mockAdjust).toHaveBeenCalledWith(
      USER_ID,
      { bucket: 'credit', minutes: 30, reason: 'support credit' },
      expect.any(String),
    );
  });

  it('should send a negative adjustment to the plan bucket with its sign', async () => {
    mockAdjust.mockResolvedValue({ auditId: 'x' });
    setup();
    fireEvent.change(screen.getByTestId('adjust-bucket'), { target: { value: 'plan' } });
    setField('adjust-minutes', '-45');
    typeReason('double counted');

    submitDialog();

    await waitFor(() => expect(mockAdjust).toHaveBeenCalledOnce());
    expect(mockAdjust.mock.calls[0][1]).toEqual({ bucket: 'plan', minutes: -45, reason: 'double counted' });
  });

  it('should never send 0 minutes', async () => {
    setup();
    setField('adjust-minutes', '0');
    typeReason('nothing');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Minutes must not be 0');
    expect(mockAdjust).not.toHaveBeenCalled();
  });

  it('should reject minutes that are not whole numbers', async () => {
    setup();
    setField('adjust-minutes', '2.5');
    typeReason('half');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Minutes must be a whole number');
    expect(mockAdjust).not.toHaveBeenCalled();
  });

  it('should show the explanation the server gave when the balance would go below zero', async () => {
    mockAdjust.mockRejectedValue(new ApiError(400, 'API error: 400', 'adjustment would take credit below zero'));
    setup();
    setField('adjust-minutes', '-500');
    typeReason('correction');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('adjustment would take credit below zero');
  });

  it.each([
    ['30', 'Adjusted credit minutes by +30.'],
    ['-30', 'Adjusted credit minutes by -30.'],
  ])('should report a %s minute adjustment', async (minutes, message) => {
    mockAdjust.mockResolvedValue({ auditId: 'x' });
    const { onDone } = setup();
    setField('adjust-minutes', minutes);
    typeReason('r');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(message));
  });
});
