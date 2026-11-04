import { useState } from 'react';
import { billingActions } from '../../lib/billing-actions';
import { formatSignedTokens } from '../../lib/billing';
import { buildAdjustRequest, MAX_ADMIN_TOKENS } from '../../lib/billing-forms';
import { formatNumber } from '../../lib/format';
import { ActionDialog, DialogField } from './ActionDialog';
import { INPUT_CLASS } from './dialog-styles';

interface AdjustDialogProps {
  userId: string;
  onClose: () => void;
  onDone: (message: string) => void;
}

/** A signed correction to the plan or credit tokens; a positive number adds, a negative one removes. */
export function AdjustDialog({ userId, onClose, onDone }: AdjustDialogProps) {
  const [bucket, setBucket] = useState<'plan' | 'credit'>('credit');
  const [tokens, setTokens] = useState('');

  return (
    <ActionDialog
      title="Adjust tokens"
      description="A correction entry in the ledger. Positive adds tokens and repays debt first; negative removes them, never below zero."
      submitLabel="Adjust"
      userId={userId}
      buildRequest={(reason) => buildAdjustRequest({ bucket, tokens, reason })}
      send={(request, key) => billingActions.adjust(userId, request, key)}
      successMessage={(request) => `Adjusted ${request.bucket} tokens by ${formatSignedTokens(request.tokens)}.`}
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
      <DialogField label="Tokens (signed)" hint={`Whole tokens, not 0, at most ${formatNumber(MAX_ADMIN_TOKENS)} either way. A positive adjustment repays debt first.`}>
        <input
          type="number"
          step="1"
          value={tokens}
          onChange={(e) => setTokens(e.target.value)}
          placeholder="e.g. 500 or -500"
          aria-label="Tokens"
          data-testid="adjust-tokens"
          className={INPUT_CLASS}
        />
      </DialogField>
    </ActionDialog>
  );
}
