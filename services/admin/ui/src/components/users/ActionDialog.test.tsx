import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { ApiError } from '../../lib/api';
import { ActionDialog } from './ActionDialog';
import { renderWithClient, submitDialog, typeReason, USER_ID } from './dialog-test-utils';

interface Request {
  reason: string;
}

function setup(send: (request: Request, key: string) => Promise<unknown>) {
  const onClose = vi.fn();
  const onDone = vi.fn();
  const view = renderWithClient(
    <ActionDialog<Request>
      title="Do a thing"
      description="Explains the thing."
      submitLabel="Do it"
      userId={USER_ID}
      buildRequest={(reason) => {
        if (!reason.trim()) throw new Error('Reason is required');
        return { reason: reason.trim() };
      }}
      send={send}
      successMessage={(request) => `Done: ${request.reason}`}
      onClose={onClose}
      onDone={onDone}
    >
      <p>Fields go here</p>
    </ActionDialog>,
  );
  return { ...view, onClose, onDone };
}

describe('ActionDialog', () => {
  it('should be a modal dialog named after the action', () => {
    setup(vi.fn());

    expect(screen.getByRole('dialog', { name: 'Do a thing' }).getAttribute('aria-modal')).toBe('true');
  });

  it('should show the description and the fields it was given', () => {
    setup(vi.fn());

    expect(screen.getByText('Explains the thing.')).toBeTruthy();
    expect(screen.getByText('Fields go here')).toBeTruthy();
  });

  it('should show the error from the request builder and send nothing when the reason is missing', async () => {
    const send = vi.fn();
    setup(send);

    submitDialog();

    expect((await screen.findByRole('alert')).textContent).toBe('Reason is required');
    expect(send).not.toHaveBeenCalled();
  });

  it('should send the request with an idempotency key and report success', async () => {
    const send = vi.fn().mockResolvedValue({});
    const { onDone } = setup(send);
    typeReason('  because  ');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Done: because'));
    expect(send).toHaveBeenCalledWith({ reason: 'because' }, expect.stringMatching(/^[A-Za-z0-9_-]{8,128}$/));
  });

  it('should refetch the account and the audit log after a success', async () => {
    const { invalidate, onDone } = setup(vi.fn().mockResolvedValue({}));
    typeReason('because');

    submitDialog();

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['billing-account', USER_ID] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['audit'] });
  });

  it('should show the explanation the server gave for a failure', async () => {
    const failure = new ApiError(422, 'API error: 422 Unprocessable', 'periodEnd must be in the future');
    setup(vi.fn().mockRejectedValue(failure));
    typeReason('because');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('periodEnd must be in the future');
  });

  it('should fall back to the generic message when the server gave no explanation', async () => {
    setup(vi.fn().mockRejectedValue(new ApiError(502, 'API error: 502 Bad Gateway')));
    typeReason('because');

    submitDialog();

    expect((await screen.findByTestId('action-error')).textContent).toBe('API error: 502 Bad Gateway');
  });

  it('should not report success when the request fails', async () => {
    const { onDone } = setup(vi.fn().mockRejectedValue(new ApiError(500, 'boom', 'down')));
    typeReason('because');

    submitDialog();

    await screen.findByTestId('action-error');
    expect(onDone).not.toHaveBeenCalled();
  });

  it('should reuse the idempotency key when the identical request is resubmitted after a failure', async () => {
    const send = vi.fn().mockRejectedValueOnce(new ApiError(504, 'timeout', 'may have applied')).mockResolvedValue({});
    setup(send);
    typeReason('because');

    submitDialog();
    await screen.findByTestId('action-error');
    submitDialog();

    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1][1]).toBe(send.mock.calls[0][1]);
  });

  it('should use a new idempotency key when the request changes', async () => {
    const send = vi.fn().mockRejectedValue(new ApiError(500, 'boom', 'down'));
    setup(send);
    typeReason('first');
    submitDialog();
    await screen.findByTestId('action-error');

    typeReason('second');
    submitDialog();

    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1][1]).not.toBe(send.mock.calls[0][1]);
  });

  it('should close from the Cancel button', () => {
    const { onClose } = setup(vi.fn());

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it('should close on Escape', () => {
    const { onClose } = setup(vi.fn());

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onClose).toHaveBeenCalledOnce();
  });

  it('should close when the backdrop is clicked but not when the panel is', () => {
    const { onClose } = setup(vi.fn());

    fireEvent.click(screen.getByText('Fields go here'));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('should disable the submit button while the request is in flight', async () => {
    setup(vi.fn(() => new Promise(() => undefined)));
    typeReason('because');

    submitDialog();

    expect((await screen.findByRole('button', { name: 'Saving…' })).hasAttribute('disabled')).toBe(true);
  });
});
