import type { ReactNode } from 'react';

type PanelTone = 'default' | 'raised' | 'dim';
type PanelPadding = 'none' | 'sm' | 'md' | 'lg';

interface PanelProps {
  children: ReactNode;
  title?: ReactNode;
  actions?: ReactNode;
  padding?: PanelPadding;
  tone?: PanelTone;
  className?: string;
}

const TONE_BG: Record<PanelTone, string> = {
  default: 'bg-[var(--color-surface)]',
  raised: 'bg-[var(--color-surface-raised)]',
  dim: 'bg-[var(--color-surface-dim)]',
};

const PADDING_CLASS: Record<PanelPadding, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

export function Panel({
  children,
  title,
  actions,
  padding = 'md',
  tone = 'raised',
  className,
}: PanelProps) {
  const classes = [
    'rounded-xl border border-[var(--color-border)]',
    TONE_BG[tone],
    PADDING_CLASS[padding],
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');

  const hasHeader = title !== undefined || actions !== undefined;
  // A flush panel (an edge-to-edge table) still insets its header, or the title touches the border.
  const headerInset = padding === 'none' ? 'px-4 pt-3' : '';

  return (
    <div className={classes} data-slot="panel" data-tone={tone} data-padding={padding}>
      {hasHeader && (
        <div className={`flex items-center justify-between mb-3 ${headerInset}`} data-slot="panel-header">
          {title !== undefined ? (
            <div className="text-sm font-medium text-[var(--color-text)]" data-slot="panel-title">
              {title}
            </div>
          ) : (
            <div />
          )}
          {actions !== undefined && (
            <div className="flex items-center gap-2" data-slot="panel-actions">
              {actions}
            </div>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
