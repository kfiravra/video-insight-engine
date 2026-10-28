import { useState } from 'react';
import type { UserCostRow } from '../../lib/api';
import { shortId } from '../../lib/format';
import { AccountPanel } from './AccountPanel';
import { AdjustmentsTab, AssistantTab, OverviewTab, VideosTab } from './UserCostTabs';

type DrawerTab = 'overview' | 'account' | 'videos' | 'assistant' | 'adjustments';

interface TabButtonProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function TabButton({ label, active, onClick }: TabButtonProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
        active
          ? 'bg-[var(--color-primary)] text-white'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-dim)]'
      }`}
    >
      {label}
    </button>
  );
}

interface UserDetailDrawerProps {
  user: UserCostRow;
  onClose: () => void;
}

export function UserDetailDrawer({ user, onClose }: UserDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<DrawerTab>('overview');

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-end sm:items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="User detail"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="sticky top-0 bg-[var(--color-surface)] border-b border-[var(--color-border)] p-4 flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h3 className="font-semibold text-sm truncate">{user.email ?? user.name ?? shortId(user.userId)}</h3>
            <p className="text-[10px] font-mono text-[var(--color-text-faint)] truncate">{user.userId}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-xs px-2 py-1 rounded border border-[var(--color-border)] hover:bg-[var(--color-surface-dim)]"
            aria-label="Close"
          >
            Close
          </button>
        </header>

        {/* Tab bar */}
        <div
          className="flex gap-1 px-4 pt-3 pb-0 border-b border-[var(--color-border)]"
          role="tablist"
          aria-label="User detail tabs"
        >
          <TabButton label="Overview" active={activeTab === 'overview'} onClick={() => setActiveTab('overview')} />
          <TabButton label="Account" active={activeTab === 'account'} onClick={() => setActiveTab('account')} />
          <TabButton label="Videos" active={activeTab === 'videos'} onClick={() => setActiveTab('videos')} />
          <TabButton label="Assistant" active={activeTab === 'assistant'} onClick={() => setActiveTab('assistant')} />
          <TabButton label="Adjustments" active={activeTab === 'adjustments'} onClick={() => setActiveTab('adjustments')} />
        </div>

        <div className="p-4">
          {activeTab === 'overview' && <OverviewTab userId={user.userId} />}
          {activeTab === 'account' && <AccountPanel userId={user.userId} />}
          {activeTab === 'videos' && <VideosTab userId={user.userId} />}
          {activeTab === 'assistant' && <AssistantTab userId={user.userId} />}
          {activeTab === 'adjustments' && <AdjustmentsTab userId={user.userId} />}
        </div>
      </div>
    </div>
  );
}
