import type { ReactNode } from 'react';

export type PillTone = 'primary' | 'success' | 'warning' | 'danger' | 'neutral';

// The *-ink tokens are the readable text shades of the tone colours (WCAG AA on their soft fills).
const TONE_CLASS: Record<PillTone, string> = {
  primary: 'bg-[var(--color-primary-soft)] text-[var(--color-primary-ink)]',
  success: 'bg-[var(--color-success-soft)] text-[var(--color-success-ink)]',
  warning: 'bg-[var(--color-warning-soft)] text-[var(--color-warning-ink)]',
  danger: 'bg-[var(--color-danger-soft)] text-[var(--color-danger-ink)]',
  neutral: 'bg-[var(--color-surface-dim)] text-[var(--color-text-muted)]',
};

interface TonePillProps {
  tone: PillTone;
  children: ReactNode;
  title?: string;
}

/** The small uppercase status pill the usage tables put beside ids and in status cells. */
export function TonePill({ tone, children, title }: TonePillProps) {
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${TONE_CLASS[tone]}`}
      title={title}
      data-slot="tone-pill"
      data-tone={tone}
    >
      {children}
    </span>
  );
}
