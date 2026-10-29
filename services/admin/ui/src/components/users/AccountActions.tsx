import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useBillingAccount } from '../../hooks/use-admin-api';
import { AdjustDialog } from './AdjustDialog';
import { CapsDialog } from './CapsDialog';
import { SECONDARY_BUTTON_CLASS } from './dialog-styles';
import { RecurringGrantDialog } from './RecurringGrantDialog';
import { SetPlanDialog } from './SetPlanDialog';

type OpenDialog = 'plan' | 'caps' | 'adjust' | 'recurring';

interface AccountActionsProps {
  userId: string;
  /** The last change that went through, shown beside the buttons. */
  notice: string | null;
  onDone: (message: string) => void;
}

/**
 * The Account tab's action buttons. Each opens a dialog that asks for a reason;
 * granting minutes stays the form above the tab. Reads the account from the same
 * query as the tab, so the dialogs start from the plan and caps on screen.
 */
export function AccountActions({ userId, notice, onDone }: AccountActionsProps) {
  const { data } = useBillingAccount(userId);
  const [open, setOpen] = useState<OpenDialog | null>(null);
  const close = () => setOpen(null);
  const done = (message: string) => {
    setOpen(null);
    onDone(message);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 w-full" data-testid="account-action-bar">
      {notice && (
        <span role="status" className="text-xs me-auto" style={{ color: 'var(--color-success)' }}>
          {notice}
        </span>
      )}
      <Link
        to={`/audit-log?targetId=${encodeURIComponent(userId)}`}
        className="text-xs text-[var(--color-primary)] hover:underline"
      >
        Audit log
      </Link>
      <button type="button" className={SECONDARY_BUTTON_CLASS} onClick={() => setOpen('plan')}>
        Set plan
      </button>
      <button type="button" className={SECONDARY_BUTTON_CLASS} onClick={() => setOpen('caps')}>
        Caps
      </button>
      <button type="button" className={SECONDARY_BUTTON_CLASS} onClick={() => setOpen('adjust')}>
        Adjust
      </button>
      <button type="button" className={SECONDARY_BUTTON_CLASS} onClick={() => setOpen('recurring')}>
        Add recurring grant
      </button>

      {open === 'plan' && (
        <SetPlanDialog
          userId={userId}
          currentPlan={data?.account?.plan.id ?? 'free'}
          onClose={close}
          onDone={done}
        />
      )}
      {open === 'caps' && (
        <CapsDialog userId={userId} currentCaps={data?.account?.caps} onClose={close} onDone={done} />
      )}
      {open === 'adjust' && <AdjustDialog userId={userId} onClose={close} onDone={done} />}
      {open === 'recurring' && <RecurringGrantDialog userId={userId} onClose={close} onDone={done} />}
    </div>
  );
}
