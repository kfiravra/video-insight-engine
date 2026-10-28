import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

const { mockSetCaps } = vi.hoisted(() => ({ mockSetCaps: vi.fn() }));

vi.mock('../../lib/billing-actions', async () => {
  const actual = await vi.importActual<typeof import('../../lib/billing-actions')>('../../lib/billing-actions');
  return { ...actual, billingActions: { ...actual.billingActions, setCaps: mockSetCaps } };
});

import { ApiError } from '../../lib/api';
import type { BillingCaps } from '../../lib/billing';
import { CapsDialog } from './CapsDialog';
import { renderWithClient, setField, submitDialog, typeReason, USER_ID } from './dialog-test-utils';

function setup(currentCaps?: BillingCaps) {
  const onDone = vi.fn();
  renderWithClient(<CapsDialog userId={USER_ID} currentCaps={currentCaps} onClose={vi.fn()} onDone={onDone} />);
  return { onDone };
}

function input(name: string): HTMLInputElement {
  return screen.getByTestId(name) as HTMLInputElement;
}

describe('CapsDialog', () => {
  beforeEach(() => {
    mockSetCaps.mockReset();
  });

  it('should start from the caps the account has now', () => {
    setup({ maxVideoMinutes: 60, freeVideosPerDay: 0, maxConcurrent: null, blocked: true });

    expect([
      input('cap-maxVideoMinutes').value,
      input('cap-freeVideosPerDay').value,
      input('cap-maxConcurrent').value,
      input('cap-blocked').checked,
    ]).toEqual(['60', '0', '', true]);
  });

  it('should require a reason and send nothing without one', async () => {
    setup();
    setField('cap-maxConcurrent', '2');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Reason is required');
    expect(mockSetCaps).not.toHaveBeenCalled();
  });

  it('should ask for a change when nothing was changed', async () => {
    setup({ maxConcurrent: 2 });
    typeReason('no-op');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Change at least one cap');
    expect(mockSetCaps).not.toHaveBeenCalled();
  });

  it('should send only the caps that changed', async () => {
    mockSetCaps.mockResolvedValue({ auditId: 'x' });
    setup({ maxConcurrent: 2 });
    setField('cap-maxVideoMinutes', '45');
    typeReason('trial limit');

    submitDialog();

    await waitFor(() => expect(mockSetCaps).toHaveBeenCalledOnce());
    expect(mockSetCaps).toHaveBeenCalledWith(USER_ID, { maxVideoMinutes: 45, reason: 'trial limit' }, expect.any(String));
  });

  it('should send null to clear a cap that was emptied', async () => {
    mockSetCaps.mockResolvedValue({ auditId: 'x' });
    setup({ maxVideoMinutes: 60, maxConcurrent: 2 });
    setField('cap-maxConcurrent', '');
    typeReason('lifted');

    submitDialog();

    await waitFor(() => expect(mockSetCaps).toHaveBeenCalledOnce());
    expect(mockSetCaps.mock.calls[0][1]).toEqual({ maxConcurrent: null, reason: 'lifted' });
  });

  it('should block the account when the box is ticked', async () => {
    mockSetCaps.mockResolvedValue({ auditId: 'x' });
    setup();
    fireEvent.click(screen.getByTestId('cap-blocked'));
    typeReason('abuse');

    submitDialog();

    await waitFor(() => expect(mockSetCaps).toHaveBeenCalledOnce());
    expect(mockSetCaps.mock.calls[0][1]).toEqual({ blocked: true, reason: 'abuse' });
  });

  it('should clear the block, not store false, when the box is unticked', async () => {
    mockSetCaps.mockResolvedValue({ auditId: 'x' });
    setup({ blocked: true });
    fireEvent.click(screen.getByTestId('cap-blocked'));
    typeReason('resolved');

    submitDialog();

    await waitFor(() => expect(mockSetCaps).toHaveBeenCalledOnce());
    expect(mockSetCaps.mock.calls[0][1]).toEqual({ blocked: null, reason: 'resolved' });
  });

  it('should reject a cap outside the allowed range before calling the server', async () => {
    setup();
    setField('cap-maxVideoMinutes', '500');
    typeReason('too long');

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Max video length must be between 3 and 180');
    expect(mockSetCaps).not.toHaveBeenCalled();
  });

  it('should show the explanation the server gave when it refuses', async () => {
    mockSetCaps.mockRejectedValue(new ApiError(400, 'API error: 400', 'maxConcurrent: must be at least 1'));
    setup();
    setField('cap-maxConcurrent', '2');
    typeReason('limit');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('maxConcurrent: must be at least 1');
  });

  it('should report that the caps were updated', async () => {
    mockSetCaps.mockResolvedValue({ auditId: 'x' });
    const { onDone } = setup();
    setField('cap-maxConcurrent', '2');
    typeReason('limit');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Caps updated.'));
  });
});
