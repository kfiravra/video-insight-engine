import { useState } from 'react';
import { billingActions } from '../../lib/billing-actions';
import type { BillingRecurringGrant } from '../../lib/billing';
import { buildDeleteGrantRequest, buildRecurringGrantRequest, MAX_ADMIN_TOKENS } from '../../lib/billing-forms';
import { ActionDialog, DialogField } from './ActionDialog';
import { INPUT_CLASS, SECONDARY_BUTTON_CLASS } from './dialog-styles';

interface RecurringGrantDialogProps {
  userId: string;
  onClose: () => void;
  onDone: (message: string) => void;
}

/** A credit lot granted every month to a demo or eval account, applied by vie-api's repair job. */
export function RecurringGrantDialog({ userId, onClose, onDone }: RecurringGrantDialogProps) {
  const [tokens, setTokens] = useState('');
  const [active, setActive] = useState(true);

  return (
    <ActionDialog
      title="Add recurring grant"
      description="Grants a credit lot of this size every month. Each month's lot is recorded in the ledger."
      submitLabel="Add grant"
      userId={userId}
      buildRequest={(reason) => buildRecurringGrantRequest({ tokens, active, reason })}
      send={(request, key) => billingActions.createRecurringGrant(userId, request, key)}
      successMessage={(request) => `Recurring grant of ${request.tokens} tokens a month added.`}
      onClose={onClose}
      onDone={onDone}
    >
      <DialogField label="Tokens per month" hint={`Whole tokens, 1 to ${MAX_ADMIN_TOKENS}.`}>
        <input
          type="number"
          step="1"
          min="1"
          max={MAX_ADMIN_TOKENS}
          value={tokens}
          onChange={(e) => setTokens(e.target.value)}
          aria-label="Tokens per month"
          data-testid="recurring-tokens"
          className={INPUT_CLASS}
        />
      </DialogField>
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
          data-testid="recurring-active"
        />
        Active (starts granting at the next monthly run)
      </label>
    </ActionDialog>
  );
}

interface DeleteGrantDialogProps {
  userId: string;
  grant: BillingRecurringGrant;
  onClose: () => void;
  onDone: (message: string) => void;
}

function DeleteGrantDialog({ userId, grant, onClose, onDone }: DeleteGrantDialogProps) {
  return (
    <ActionDialog
      title="Delete recurring grant"
      description={`Stops the monthly grant of ${grant.tokens} tokens. Lots it already granted stay on the account.`}
      submitLabel="Delete grant"
      userId={userId}
      buildRequest={(reason) => buildDeleteGrantRequest(reason)}
      send={(request, key) => billingActions.deleteRecurringGrant(userId, grant.grantId, request, key)}
      successMessage={() => 'Recurring grant deleted.'}
      onClose={onClose}
      onDone={onDone}
    />
  );
}

interface DeleteGrantButtonProps {
  userId: string;
  grant: BillingRecurringGrant;
  onDone: (message: string) => void;
}

/** The row action in the recurring-grants table: a Delete button that opens the reason dialog. */
export function DeleteGrantButton({ userId, grant, onDone }: DeleteGrantButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={SECONDARY_BUTTON_CLASS}
        aria-label={`Delete recurring grant of ${grant.tokens} tokens`}
        data-testid={`delete-grant-${grant.grantId}`}
      >
        Delete
      </button>
      {open && (
        <DeleteGrantDialog
          userId={userId}
          grant={grant}
          onClose={() => setOpen(false)}
          onDone={(message) => {
            setOpen(false);
            onDone(message);
          }}
        />
      )}
    </>
  );
}
