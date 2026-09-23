import { useState } from 'react';
import { usePurgeVideo } from '../hooks/use-admin-api';
import type { VideoPurgeResponse } from '../lib/api';
import { ErrorState } from './ErrorState';

interface PurgeVideoControlsProps {
  videoId: string;
}

const BUTTON = 'px-2.5 py-1 text-xs rounded-md font-medium disabled:opacity-50';

/**
 * Two-step "delete everywhere": confirm, then run vie-api's global cascade
 * (Mongo references, Qdrant vectors, S3 objects, Redis keys, audit row).
 * Modeled on the DLQ replay controls.
 */
export function PurgeVideoControls({ videoId }: PurgeVideoControlsProps) {
  const [confirming, setConfirming] = useState(false);
  const purge = usePurgeVideo(videoId);

  return (
    <div className="flex flex-col items-start sm:items-end gap-2 flex-shrink-0" data-testid="purge-controls">
      <div className="flex items-center gap-2">
        {!confirming ? (
          <button
            type="button"
            className={`${BUTTON} border border-[var(--color-border)] hover:bg-[var(--color-surface-dim)]`}
            disabled={purge.isSuccess}
            onClick={() => setConfirming(true)}
          >
            {purge.isSuccess ? 'Deleted everywhere' : 'Delete everywhere…'}
          </button>
        ) : (
          <>
            <button
              type="button"
              autoFocus
              className={`${BUTTON} bg-[var(--color-danger)] text-white`}
              disabled={purge.isPending}
              onClick={() => purge.mutate(undefined, { onSettled: () => setConfirming(false) })}
            >
              {purge.isPending ? 'Deleting…' : 'Confirm delete'}
            </button>
            <button
              type="button"
              className={`${BUTTON} text-[var(--color-text-muted)] hover:text-[var(--color-text)]`}
              disabled={purge.isPending}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
          </>
        )}
      </div>
      {purge.isSuccess && purge.data && <PurgeSummary result={purge.data} />}
      {purge.isError && (
        <ErrorState error={purge.error} onRetry={() => purge.reset()} title="Delete failed" compact />
      )}
    </div>
  );
}

function PurgeSummary({ result }: { result: VideoPurgeResponse }) {
  const removed = Object.entries(result.counts)
    .filter(([, count]) => count > 0)
    .map(([store, count]) => `${store} ${count}`)
    .join(' · ');
  return (
    <p role="status" className="text-[11px] text-[var(--color-text-muted)] text-right" data-testid="purge-result">
      Removed: {removed || 'nothing left to remove'}
      {result.warnings.length > 0 && (
        <span className="text-[var(--color-warning)]"> · warnings: {result.warnings.join('; ')}</span>
      )}
    </p>
  );
}
