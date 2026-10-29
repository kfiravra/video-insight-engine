import { describeApiError } from '../lib/api-error';

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}

/** An API failure shows the server's own explanation (vie-api's validation text, FastAPI's 422), not "API error: 403 Forbidden". */
function getMessage(error: unknown): string {
  if (error instanceof Error) return describeApiError(error);
  if (error == null) return 'Unknown error';
  return String(error);
}

function AlertTriangle({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

export function ErrorState({ error, onRetry, title = 'Something went wrong', compact = false }: ErrorStateProps) {
  const message = getMessage(error);

  if (compact) {
    return (
      <div
        role="alert"
        data-testid="error-state"
        className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-danger-soft)] text-xs"
      >
        <span className="text-[var(--color-danger)] flex-shrink-0">
          <AlertTriangle size={14} />
        </span>
        <span className="font-medium text-[var(--color-text)] truncate">{title}</span>
        <span className="text-[var(--color-text-muted)] truncate flex-1">{message}</span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="flex-shrink-0 px-2 py-1 rounded-md bg-[var(--color-primary)] text-white text-xs font-medium hover:opacity-90 transition-opacity"
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      role="alert"
      data-testid="error-state"
      className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-danger-soft)]"
    >
      <div className="flex items-start gap-3">
        <span className="text-[var(--color-danger)] mt-0.5 flex-shrink-0">
          <AlertTriangle size={18} />
        </span>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">{title}</h3>
          <p className="mt-1 text-xs text-[var(--color-text-muted)] break-words">{message}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 px-3 py-1.5 rounded-lg bg-[var(--color-primary)] text-white text-xs font-medium hover:opacity-90 transition-opacity"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
