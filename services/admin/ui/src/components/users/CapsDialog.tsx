import { useState } from 'react';
import { billingActions } from '../../lib/billing-actions';
import type { BillingCaps } from '../../lib/billing';
import {
  buildCapsRequest,
  CAP_LIMITS,
  capsToForm,
  type CapsFormValues,
  type NumericCap,
} from '../../lib/billing-forms';
import { ActionDialog, DialogField } from './ActionDialog';
import { INPUT_CLASS } from './dialog-styles';

interface CapsDialogProps {
  userId: string;
  currentCaps: BillingCaps | undefined;
  onClose: () => void;
  onDone: (message: string) => void;
}

type CapFields = Omit<CapsFormValues, 'reason'>;

const NUMERIC_FIELDS: Array<{ name: NumericCap; label: string; hint: string }> = [
  { name: 'maxVideoMinutes', label: 'Max video length (minutes)', hint: 'Lowers the plan limit for this account.' },
  { name: 'freeVideosPerDay', label: 'Free videos per day', hint: '0 turns the free video off.' },
  { name: 'maxConcurrent', label: 'Runs at once', hint: 'Most videos this account may have in flight.' },
];

/**
 * Per-account limits. A field left as it was is not sent, an emptied field clears
 * its cap, and a new number sets it. Caps only ever tighten what the plan allows.
 */
export function CapsDialog({ userId, currentCaps, onClose, onDone }: CapsDialogProps) {
  const [initial] = useState<CapFields>(() => capsToForm(currentCaps));
  const [values, setValues] = useState<CapFields>(initial);

  return (
    <ActionDialog
      title="Account caps"
      description="Leave a field as it is to keep it, empty it to clear the cap, or enter a number to set it."
      submitLabel="Save caps"
      userId={userId}
      buildRequest={(reason) => buildCapsRequest({ ...values, reason }, initial)}
      send={(request, key) => billingActions.setCaps(userId, request, key)}
      successMessage={() => 'Caps updated.'}
      onClose={onClose}
      onDone={onDone}
    >
      {NUMERIC_FIELDS.map(({ name, label, hint }) => (
        <DialogField key={name} label={label} hint={`${hint} ${CAP_LIMITS[name].min} to ${CAP_LIMITS[name].max}.`}>
          <input
            type="number"
            step="1"
            min={CAP_LIMITS[name].min}
            max={CAP_LIMITS[name].max}
            value={values[name]}
            onChange={(e) => setValues((current) => ({ ...current, [name]: e.target.value }))}
            placeholder="No cap"
            aria-label={label}
            data-testid={`cap-${name}`}
            className={INPUT_CLASS}
          />
        </DialogField>
      ))}
      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={values.blocked}
          onChange={(e) => setValues((current) => ({ ...current, blocked: e.target.checked }))}
          data-testid="cap-blocked"
        />
        Block this account (it cannot start new videos)
      </label>
    </ActionDialog>
  );
}
