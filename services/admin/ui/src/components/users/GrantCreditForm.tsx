import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api, describeApiError } from '../../lib/api';
import { MAX_ADMIN_MINUTES, MAX_REASON_LENGTH } from '../../lib/billing-forms';
import { buildGrantRequest, type GrantFormValues } from '../../lib/grant-credit';
import { useAttemptKey } from '../../hooks/use-attempt-key';
import { useRefreshAfterSend } from '../../hooks/use-refresh-after-send';

interface GrantCreditFormProps {
  userId: string;
}

interface FormMessage {
  kind: 'ok' | 'err';
  text: string;
}

const EMPTY_VALUES: GrantFormValues = { minutes: '', reason: '', expiry: '' };
const FIELD_CLASS =
  'px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-xs';

/**
 * The form's state and its request. Each submission carries an Idempotency-Key;
 * resubmitting the identical request after a failure or timeout reuses it, so a
 * grant that went through the first time is not granted twice, while changing a
 * field, or a success, starts a new key. The account and the audit log are
 * re-read after a success and after a failure that may have followed an applied
 * grant (`useRefreshAfterSend`).
 */
function useGrantCredit(userId: string) {
  const [values, setValues] = useState<GrantFormValues>(EMPTY_VALUES);
  const [message, setMessage] = useState<FormMessage | null>(null);
  const { keyFor, reset } = useAttemptKey();
  const refreshAfter = useRefreshAfterSend(userId);

  const mutation = useMutation({
    mutationFn: async () => {
      const request = buildGrantRequest(values);
      await refreshAfter(() => api.users.grantCredit(userId, request, keyFor(request)));
      return request;
    },
    onSuccess: (request) => {
      reset();
      setMessage({ kind: 'ok', text: `Granted ${request.minutes} minutes.` });
      setValues(EMPTY_VALUES);
    },
    onError: (e: unknown) => setMessage({ kind: 'err', text: describeApiError(e) }),
  });

  const setField = (name: keyof GrantFormValues) => (e: { target: { value: string } }) =>
    setValues((current) => ({ ...current, [name]: e.target.value }));

  const submit = () => {
    setMessage(null);
    mutation.mutate();
  };

  return { values, message, setField, submit, pending: mutation.isPending };
}

interface GrantFieldsProps {
  values: GrantFormValues;
  setField: (name: keyof GrantFormValues) => (e: { target: { value: string } }) => void;
}

function GrantFields({ values, setField }: GrantFieldsProps) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
      <input
        type="number"
        step="1"
        min="1"
        max={MAX_ADMIN_MINUTES}
        value={values.minutes}
        onChange={setField('minutes')}
        placeholder="Minutes"
        aria-label="Minutes"
        data-testid="grant-credit-minutes"
        className={FIELD_CLASS}
      />
      <input
        value={values.reason}
        onChange={setField('reason')}
        placeholder="Reason (audited)"
        aria-label="Reason"
        data-testid="grant-credit-reason"
        maxLength={MAX_REASON_LENGTH}
        className={FIELD_CLASS}
      />
      <input
        type="date"
        value={values.expiry}
        onChange={setField('expiry')}
        aria-label="Expires (optional)"
        title="Expires at the end of this day (UTC). Leave empty for no expiry."
        data-testid="grant-credit-expiry"
        className={FIELD_CLASS}
      />
    </div>
  );
}

/**
 * Grant minutes to a user (a credit lot). Who is granting comes from the
 * signed-in admin session on the server, so there is no admin id to type; the
 * reason is required and lands in the ledger and the audit log.
 */
export function GrantCreditForm({ userId }: GrantCreditFormProps) {
  const { values, message, setField, submit, pending } = useGrantCredit(userId);

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--color-border)] p-3 bg-[var(--color-surface-dim)]"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">Grant minutes</h4>
      <GrantFields values={values} setField={setField} />
      <p className="text-[10px] text-[var(--color-text-faint)]">
        Adds a credit lot to this account; no expiry unless you pick a date. Recorded in the audit log under your
        admin session.
      </p>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded-md bg-[var(--color-primary)] text-white text-xs font-medium disabled:opacity-60"
        >
          {pending ? 'Granting…' : 'Grant minutes'}
        </button>
        {message && (
          <span
            className="text-xs"
            style={{ color: message.kind === 'ok' ? 'var(--color-success)' : 'var(--color-danger)' }}
            role="status"
          >
            {message.text}
          </span>
        )}
      </div>
    </form>
  );
}
