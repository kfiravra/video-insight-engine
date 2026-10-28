import { useState } from 'react';
import { billingActions } from '../../lib/billing-actions';
import { buildAdjustRequest, MAX_ADMIN_MINUTES } from '../../lib/billing-forms';
import { ActionDialog, DialogField } from './ActionDialog';
import { INPUT_CLASS } from './dialog-styles';

interface AdjustDialogProps {
  userId: string;
  onClose: () => void;
  onDone: (message: string) => void;
}

/** A signed correction to the plan or credit minutes; a positive number adds, a negative one removes. */
export function AdjustDialog({ userId, onClose, onDone }: AdjustDialogProps) {
  const [bucket, setBucket] = useState<'plan' | 'credit'>('credit');
  const [minutes, setMinutes] = useState('');

  return (
    <ActionDialog
      title="Adjust minutes"
      description="A correction entry in the ledger. Positive adds minutes, negative removes them; a balance never goes below zero."
      submitLabel="Adjust"
      userId={userId}
      buildRequest={(reason) => buildAdjustRequest({ bucket, minutes, reason })}
      send={(request, key) => billingActions.adjust(userId, request, key)}
      successMessage={(request) =>
        `Adjusted ${request.bucket} minutes by ${request.minutes > 0 ? '+' : ''}${request.minutes}.`
      }
      onClose={onClose}
      onDone={onDone}
    >
      <DialogField label="Bucket">
        <select
          value={bucket}
          onChange={(e) => setBucket(e.target.value === 'plan' ? 'plan' : 'credit')}
          aria-label="Bucket"
          data-testid="adjust-bucket"
          className={INPUT_CLASS}
        >
          <option value="credit">Credit</option>
          <option value="plan">Plan</option>
        </select>
      </DialogField>
      <DialogField label="Minutes (signed)" hint={`Whole minutes, not 0, at most ${MAX_ADMIN_MINUTES} either way.`}>
        <input
          type="number"
          step="1"
          value={minutes}
          onChange={(e) => setMinutes(e.target.value)}
          placeholder="e.g. 30 or -30"
          aria-label="Minutes"
          data-testid="adjust-minutes"
          className={INPUT_CLASS}
        />
      </DialogField>
    </ActionDialog>
  );
}
