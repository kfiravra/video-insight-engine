import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { describeApiError } from '../../lib/api';
import { MAX_REASON_LENGTH } from '../../lib/billing-forms';
import { useAttemptKey } from '../../hooks/use-attempt-key';
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
 * The shell of every Account action: a modal with the action's fields, a required
 * reason, and the error from the server shown as the server words it. Each
 * submission carries an Idempotency-Key; resubmitting the identical request after
 * a failure or timeout reuses the key, so a change that did apply is not applied
 * twice. A success refetches the account and the audit log.
 */
export function ActionDialog<TRequest>({
  title,
  description,
  submitLabel,
  userId,
  buildRequest,
  send,
  successMessage,
  onClose,
  onDone,
  children,
}: ActionDialogProps<TRequest>) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const { keyFor, reset } = useAttemptKey();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const mutation = useMutation({
    mutationFn: async () => {
      const request = buildRequest(reason);
      await send(request, keyFor(request));
      return request;
    },
    onSuccess: (request) => {
      reset();
      queryClient.invalidateQueries({ queryKey: ['billing-account', userId] });
      queryClient.invalidateQueries({ queryKey: ['audit'] });
      onDone(successMessage(request));
    },
  });

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/40 flex items-end sm:items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] w-full max-w-md max-h-[90vh] overflow-y-auto p-4 space-y-3 outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h3 className="font-semibold text-sm">{title}</h3>
          {description && <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">{description}</p>}
        </header>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          {children}
          <DialogField label="Reason" hint="Required. Recorded in the ledger and the audit log.">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              maxLength={MAX_REASON_LENGTH}
              aria-label="Reason"
              aria-required="true"
              data-testid="action-reason"
              className={INPUT_CLASS}
            />
          </DialogField>

          {mutation.error && (
            <p role="alert" className="text-xs text-[var(--color-danger)]" data-testid="action-error">
              {describeApiError(mutation.error)}
            </p>
          )}

          <div className="flex items-center justify-end gap-2">
            <button type="button" onClick={onClose} className={SECONDARY_BUTTON_CLASS}>
              Cancel
            </button>
            <button type="submit" disabled={mutation.isPending} className={PRIMARY_BUTTON_CLASS}>
              {mutation.isPending ? 'Saving…' : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
