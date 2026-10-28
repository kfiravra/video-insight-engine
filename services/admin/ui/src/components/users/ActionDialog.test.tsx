import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
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

/** Press and release the pointer on `element`, as a click does. */
function clickLikeAUser(element: Element): void {
  fireEvent.mouseDown(element);
  fireEvent.mouseUp(element);
  fireEvent.click(element);
}

/** A request that stays in flight until `finish` is called. */
function pendingSend() {
  let finish: (value?: unknown) => void = () => undefined;
  let fail: (error: Error) => void = () => undefined;
  const send = vi.fn(
    () =>
      new Promise((resolve, reject) => {
        finish = resolve;
        fail = reject;
      }),
  );
  return { send, finish: (value?: unknown) => finish(value), fail: (error: Error) => fail(error) };
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
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['audit'] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['billing-account', USER_ID] });
  });

  it('should refetch the account and the audit log after a failure too, since a timeout can follow an applied change', async () => {
    const { invalidate } = setup(vi.fn().mockRejectedValue(new ApiError(504, 'timeout', 'the change may have been applied')));
    typeReason('because');

    submitDialog();

    await screen.findByTestId('action-error');
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['audit'] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['billing-account', USER_ID] });
  });

  it('should not refetch after a refusal, which changed nothing', async () => {
    const { invalidate } = setup(vi.fn().mockRejectedValue(new ApiError(403, 'API error: 403', 'A named admin session is required')));
    typeReason('because');

    submitDialog();

    await screen.findByTestId('action-error');
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('should not refetch when the form is invalid and nothing was sent', async () => {
    const { invalidate } = setup(vi.fn());

    submitDialog();

    await screen.findByRole('alert');
    expect(invalidate).not.toHaveBeenCalled();
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

    clickLikeAUser(screen.getByText('Fields go here'));
    expect(onClose).not.toHaveBeenCalled();

    clickLikeAUser(screen.getByTestId('dialog-backdrop'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('should not close when a press that began inside the panel is released on the backdrop', () => {
    const { onClose } = setup(vi.fn());

    fireEvent.mouseDown(screen.getByTestId('action-reason'));
    fireEvent.mouseUp(screen.getByTestId('dialog-backdrop'));
    fireEvent.click(screen.getByTestId('dialog-backdrop'));

    expect(onClose).not.toHaveBeenCalled();
  });

  describe('while the request is in flight', () => {
    async function startPending() {
      const pending = pendingSend();
      const view = setup(pending.send);
      typeReason('because');
      submitDialog();
      await screen.findByRole('button', { name: 'Saving…' });
      return { ...view, ...pending };
    }

    it('should not close on Escape', async () => {
      const { onClose } = await startPending();

      fireEvent.keyDown(document, { key: 'Escape' });

      expect(onClose).not.toHaveBeenCalled();
    });

    it('should not close from the backdrop', async () => {
      const { onClose } = await startPending();

      clickLikeAUser(screen.getByTestId('dialog-backdrop'));

      expect(onClose).not.toHaveBeenCalled();
    });

    it('should disable Cancel', async () => {
      await startPending();

      expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    });

    it('should close again once a failure comes back', async () => {
      const { onClose, fail } = await startPending();

      await act(async () => fail(new ApiError(504, 'timeout', 'may have applied')));
      await screen.findByTestId('action-error');
      fireEvent.keyDown(document, { key: 'Escape' });

      expect(onClose).toHaveBeenCalledOnce();
    });
  });

  describe('focus', () => {
    it('should move focus into the dialog when it opens', () => {
      setup(vi.fn());

      expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
    });

    it('should not pull focus back to the panel when the parent re-renders while the operator is typing', () => {
      function Parent() {
        const [ticks, setTicks] = useState(0);
        return (
          <>
            <button type="button" onClick={() => setTicks((n) => n + 1)}>
              tick {ticks}
            </button>
            {/* A new onClose closure on every render, as the real callers pass. */}
            <ActionDialog<Request>
              title="Do a thing"
              submitLabel="Do it"
              userId={USER_ID}
              buildRequest={(reason) => ({ reason })}
              send={vi.fn()}
              successMessage={() => 'ok'}
              onClose={() => setTicks(0)}
              onDone={vi.fn()}
            />
          </>
        );
      }
      renderWithClient(<Parent />);
      const reason = screen.getByTestId('action-reason');
      reason.focus();

      fireEvent.click(screen.getByRole('button', { name: /tick/ }));

      expect(document.activeElement).toBe(reason);
    });

    it('should return focus to what opened the dialog when it closes', () => {
      const opener = document.createElement('button');
      document.body.appendChild(opener);
      opener.focus();
      const { unmount } = setup(vi.fn());
      expect(document.activeElement).not.toBe(opener);

      unmount();

      expect(document.activeElement).toBe(opener);
      opener.remove();
    });

    it('should keep Tab inside the dialog: from the last control to the first', () => {
      setup(vi.fn());
      const submit = screen.getByRole('button', { name: 'Do it' });
      submit.focus();

      fireEvent.keyDown(document, { key: 'Tab' });

      expect(document.activeElement).toBe(screen.getByTestId('action-reason'));
    });

    it('should keep Shift+Tab inside the dialog: from the first control to the last', () => {
      setup(vi.fn());
      screen.getByTestId('action-reason').focus();

      fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });

      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Do it' }));
    });

    it('should bring focus back inside when Tab is pressed with it outside the dialog', () => {
      setup(vi.fn());
      const outside = document.createElement('button');
      document.body.appendChild(outside);
      outside.focus();

      fireEvent.keyDown(document, { key: 'Tab' });

      expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
      outside.remove();
    });
  });

  it('should tie the description to the dialog for screen readers', () => {
    setup(vi.fn());

    const describedBy = screen.getByRole('dialog').getAttribute('aria-describedby');

    expect(describedBy && document.getElementById(describedBy)?.textContent).toBe('Explains the thing.');
  });

  it('should disable the submit button while the request is in flight', async () => {
    setup(vi.fn(() => new Promise(() => undefined)));
    typeReason('because');

    submitDialog();

    expect((await screen.findByRole('button', { name: 'Saving…' })).hasAttribute('disabled')).toBe(true);
  });
});
