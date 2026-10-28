import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { useMutation } from '@tanstack/react-query';
import { describeApiError } from '../../lib/api';
import { MAX_REASON_LENGTH } from '../../lib/billing-forms';
import { useAttemptKey } from '../../hooks/use-attempt-key';
import { useModalDialog } from '../../hooks/use-modal-dialog';
import { useRefreshAfterSend } from '../../hooks/use-refresh-after-send';
import { INPUT_CLASS, PRIMARY_BUTTON_CLASS, SECONDARY_BUTTON_CLASS } from './dialog-styles';

interface DialogFieldProps {
  label: string;
  hint?: string;
  children: ReactNode;
}

/** A labelled control inside an action dialog. */
export function DialogField({ label, hint, children }: DialogFieldProps) {
  return (
    <label className="block space-y-1">
      <span className="text-[11px] font-medium text-[var(--color-text-muted)]">{label}</span>
      {children}
      {hint && <span className="block text-[10px] text-[var(--color-text-faint)]">{hint}</span>}
    </label>
  );
}

interface ActionDialogProps<TRequest> {
  title: string;
  description?: string;
  submitLabel: string;
  userId: string;
  /** Builds the request from the fields and the reason, or throws an Error saying what to fix. */
  buildRequest: (reason: string) => TRequest;
  send: (request: TRequest, idempotencyKey: string) => Promise<unknown>;
  successMessage: (request: TRequest) => string;
  onClose: () => void;
  /** Called with the success message once the change is applied; the caller closes the dialog. */
  onDone: (message: string) => void;
  /** The action's own fields; the reason field and buttons come with the dialog. */
  children?: ReactNode;
}

/**
 * Sends the action with one Idempotency-Key per distinct request, and re-reads the
 * account and the audit log afterwards unless vie-api refused it (see
 * `useRefreshAfterSend`): a timeout can arrive after the change was applied, and
 * the operator must see that before deciding to try again.
 */
function useActionMutation<TRequest>(props: ActionDialogProps<TRequest>, reason: string) {
  const { userId, buildRequest, send, successMessage, onDone } = props;
  const { keyFor, reset } = useAttemptKey();
  const refreshAfter = useRefreshAfterSend(userId);

  return useMutation({
    mutationFn: async () => {
      const request = buildRequest(reason);
      await refreshAfter(() => send(request, keyFor(request)));
      return request;
    },
    onSuccess: (request) => {
      reset();
      onDone(successMessage(request));
    },
  });
}

interface ReasonFieldProps {
  value: string;
  onChange: (value: string) => void;
}

function ReasonField({ value, onChange }: ReasonFieldProps) {
  return (
    <DialogField label="Reason" hint="Required. Recorded in the ledger and the audit log.">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={2}
        maxLength={MAX_REASON_LENGTH}
        aria-label="Reason"
        aria-required="true"
        data-testid="action-reason"
        className={INPUT_CLASS}
      />
    </DialogField>
  );
}

interface DialogButtonsProps {
  pending: boolean;
  submitLabel: string;
  onCancel: () => void;
}

function DialogButtons({ pending, submitLabel, onCancel }: DialogButtonsProps) {
  return (
    <div className="flex items-center justify-end gap-2">
      <button type="button" onClick={onCancel} disabled={pending} className={SECONDARY_BUTTON_CLASS}>
        Cancel
      </button>
      <button type="submit" disabled={pending} className={PRIMARY_BUTTON_CLASS}>
        {pending ? 'Saving…' : submitLabel}
      </button>
    </div>
  );
}

/**
 * The shell of every Account action: a modal with the action's fields, a required
 * reason, and the error from the server shown as the server words it. Each
 * submission carries an Idempotency-Key; resubmitting the identical request after
 * a failure or timeout reuses the key, so a change that did apply is not applied
 * twice. The dialog cannot be closed while the request is in flight, so an
 * uncertain outcome is always seen before the operator can open a fresh dialog
 * (and a fresh key).
 */
export function ActionDialog<TRequest>(props: ActionDialogProps<TRequest>) {
  const { title, description, submitLabel, onClose, children } = props;
  const [reason, setReason] = useState('');
  const mutation = useActionMutation(props, reason);
  const { panelRef, backdropProps, requestClose } = useModalDialog({ onClose, locked: mutation.isPending });
  const descriptionId = useId();

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/40 flex items-end sm:items-center justify-center p-4"
      data-testid="dialog-backdrop"
      {...backdropProps}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] w-full max-w-md max-h-[90vh] overflow-y-auto p-4 space-y-3 outline-none"
      >
        <header>
          <h3 className="font-semibold text-sm">{title}</h3>
          {description && (
            <p id={descriptionId} className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              {description}
            </p>
          )}
        </header>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          {children}
          <ReasonField value={reason} onChange={setReason} />
          {mutation.error && (
            <p role="alert" className="text-xs text-[var(--color-danger)]" data-testid="action-error">
              {describeApiError(mutation.error)}
            </p>
          )}
          <DialogButtons pending={mutation.isPending} submitLabel={submitLabel} onCancel={requestClose} />
        </form>
      </div>
    </div>
  );
}
