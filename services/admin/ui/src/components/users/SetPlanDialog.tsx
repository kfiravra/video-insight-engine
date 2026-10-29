import { useState } from 'react';
import { billingActions } from '../../lib/billing-actions';
import { buildPlanRequest, PRO_PERIOD_MINUTES } from '../../lib/billing-forms';
import { ActionDialog, DialogField } from './ActionDialog';
import { INPUT_CLASS } from './dialog-styles';

interface SetPlanDialogProps {
  userId: string;
  currentPlan: 'free' | 'pro';
  onClose: () => void;
  onDone: (message: string) => void;
}

/** Set the account's plan by hand: Pro grants the month's minutes now for a term; free stops plan minutes. */
export function SetPlanDialog({ userId, currentPlan, onClose, onDone }: SetPlanDialogProps) {
  const [planId, setPlanId] = useState<'free' | 'pro'>(currentPlan === 'pro' ? 'free' : 'pro');
  const [periodEnd, setPeriodEnd] = useState('');

  return (
    <ActionDialog
      title="Set plan"
      description={`A manual plan, no payment. Pro grants ${PRO_PERIOD_MINUTES} minutes now and renews monthly for the term; free stops plan minutes.`}
      submitLabel="Set plan"
      userId={userId}
      buildRequest={(reason) => buildPlanRequest({ planId, periodEnd, reason })}
      send={(request, key) => billingActions.setPlan(userId, request, key)}
      successMessage={(request) => `Plan set to ${request.planId === 'pro' ? 'Pro' : 'Free'}.`}
      onClose={onClose}
      onDone={onDone}
    >
      <DialogField label="Plan">
        <select
          value={planId}
          onChange={(e) => setPlanId(e.target.value === 'pro' ? 'pro' : 'free')}
          aria-label="Plan"
          data-testid="plan-select"
          className={INPUT_CLASS}
        >
          <option value="pro">Pro</option>
          <option value="free">Free</option>
        </select>
      </DialogField>
      {planId === 'pro' && (
        <DialogField label="Term ends (optional)" hint="Through the end of that day (UTC). Empty = 12 months from now.">
          <input
            type="date"
            value={periodEnd}
            onChange={(e) => setPeriodEnd(e.target.value)}
            aria-label="Term ends"
            data-testid="plan-period-end"
            className={INPUT_CLASS}
          />
        </DialogField>
      )}
    </ActionDialog>
  );
}
