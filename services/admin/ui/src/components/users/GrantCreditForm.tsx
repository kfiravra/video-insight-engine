import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, describeApiError, type GrantCreditRequest } from '../../lib/api';
import { buildGrantRequest, MAX_GRANT_MINUTES, MAX_REASON_LENGTH, type GrantFormValues } from '../../lib/grant-credit';
import { newIdempotencyKey } from '../../lib/idempotency';

interface GrantCreditFormProps {
  userId: string;
}

interface Attempt {
  payload: string;
  key: string;
}

/**
 * Grant minutes to a user (a credit lot). Who is granting comes from the
 * signed-in admin session on the server, so there is no admin id to type; the
 * reason is required and lands in the ledger and the audit log.
 *
 * Each submission carries an Idempotency-Key. Resubmitting the identical
 * request after a failure or timeout reuses the key, so a grant that actually
 * went through the first time is not granted twice; changing any field, or a
 * success, starts a new key.
 */
export function GrantCreditForm({ userId }: GrantCreditFormProps) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<GrantFormValues>({ minutes: '', reason: '', expiry: '' });
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const lastAttempt = useRef<Attempt | null>(null);

  function keyFor(request: GrantCreditRequest): string {
    const payload = JSON.stringify(request);
    if (lastAttempt.current?.payload !== payload) {
      lastAttempt.current = { payload, key: newIdempotencyKey() };
    }
    return lastAttempt.current.key;
  }

  const mutation = useMutation({
    mutationFn: async () => {
      const request = buildGrantRequest(values);
      const response = await api.users.grantCredit(userId, request, keyFor(request));
      return { request, response };
    },
    onSuccess: ({ request }) => {
      lastAttempt.current = null;
      setMessage({ kind: 'ok', text: `Granted ${request.minutes} minutes.` });
      setValues({ minutes: '', reason: '', expiry: '' });
      queryClient.invalidateQueries({ queryKey: ['billing-account', userId] });
    },
    onError: (e: unknown) => setMessage({ kind: 'err', text: describeApiError(e) }),
  });

  const field = (name: keyof GrantFormValues) => (e: { target: { value: string } }) =>
    setValues((current) => ({ ...current, [name]: e.target.value }));

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--color-border)] p-3 bg-[var(--color-surface-dim)]"
      onSubmit={(e) => {
        e.preventDefault();
        setMessage(null);
        mutation.mutate();
      }}
    >
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        Grant minutes
      </h4>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <input
          type="number"
          step="1"
          min="1"
          max={MAX_GRANT_MINUTES}
          value={values.minutes}
          onChange={field('minutes')}
          placeholder="Minutes"
          aria-label="Minutes"
          data-testid="grant-credit-minutes"
          className="px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-xs"
        />
        <input
          value={values.reason}
          onChange={field('reason')}
          placeholder="Reason (audited)"
          aria-label="Reason"
          data-testid="grant-credit-reason"
          maxLength={MAX_REASON_LENGTH}
          className="px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-xs"
        />
        <input
          type="date"
          value={values.expiry}
          onChange={field('expiry')}
          aria-label="Expires (optional)"
          title="Expires at the end of this day (UTC). Leave empty for no expiry."
          data-testid="grant-credit-expiry"
          className="px-2 py-1.5 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-xs"
        />
      </div>
      <p className="text-[10px] text-[var(--color-text-faint)]">
        Adds a credit lot to this account; no expiry unless you pick a date. Recorded in the audit log under your
        admin session.
      </p>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={mutation.isPending}
          className="px-3 py-1.5 rounded-md bg-[var(--color-primary)] text-white text-xs font-medium disabled:opacity-60"
        >
          {mutation.isPending ? 'Granting…' : 'Grant minutes'}
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
