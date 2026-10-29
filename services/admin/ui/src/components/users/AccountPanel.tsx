import { useState } from 'react';
import { AccountActions } from './AccountActions';
import { AccountTab } from './AccountTab';
import { GrantCreditForm } from './GrantCreditForm';
import { DeleteGrantButton } from './RecurringGrantDialog';

interface AccountPanelProps {
  userId: string;
}

/**
 * Everything on the user drawer's Account tab: the grant-minutes form, the
 * action buttons and the read-only account view. Holds the "last change"
 * notice so a delete in the grants table and a dialog result show in one place.
 */
export function AccountPanel({ userId }: AccountPanelProps) {
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <GrantCreditForm userId={userId} />
      <AccountTab
        userId={userId}
        actions={<AccountActions userId={userId} notice={notice} onDone={setNotice} />}
        grantActions={(grant) => <DeleteGrantButton userId={userId} grant={grant} onDone={setNotice} />}
      />
    </div>
  );
}
