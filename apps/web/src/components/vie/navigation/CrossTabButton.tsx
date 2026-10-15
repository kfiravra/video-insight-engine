import { memo, useRef } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

interface CrossTabButtonProps {
  label: string;
  /** Why go there — the target tab's goal. When present it is the link text
   *  and the label drops to a "Next: …" subline. */
  reason?: string;
  description?: string;
  onClick: () => void;
  className?: string;
}

export const CrossTabButton = memo(function CrossTabButton({
  label,
  reason,
  description,
  onClick,
  className,
}: CrossTabButtonProps) {
  const hostRef = useRef<HTMLSpanElement>(null);

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>): void => {
    const host = hostRef.current;
    if (host) {
      const rect = host.getBoundingClientRect();
      host.style.setProperty('--ripple-x', `${e.clientX - rect.left}px`);
      host.style.setProperty('--ripple-y', `${e.clientY - rect.top}px`);
      host.classList.remove('vie-rippling');
      void host.offsetWidth;
      host.classList.add('vie-rippling');
    }
    onClick();
  };

  return (
    <span
      ref={hostRef}
      className={cn(
        'vie-ripple vie-shimmer-hover block rounded-xl hover:-translate-y-0.5 active:scale-[0.98] transition-transform duration-150 ease-[var(--ease-out-expo)] motion-reduce:hover:translate-y-0 motion-reduce:active:scale-100 motion-reduce:transition-none',
        className,
      )}
    >
      <Button
        variant="ghost"
        size="bare"
        onClick={handleClick}
        className={cn(
          'w-full flex items-center justify-between px-4 py-3 rounded-xl',
          'bg-[var(--vie-accent-muted)] border border-[var(--vie-accent-border)]',
          'text-[var(--vie-accent)] text-sm font-medium',
          'shadow-[0_4px_16px_-8px_oklch(from_var(--primary)_l_c_h_/_0.25)]',
        )}
      >
        <div className="flex flex-col items-start gap-0.5 text-start">
          {reason ? (
            <>
              <span className="font-semibold tracking-tight text-pretty">{reason}</span>
              <span className="text-xs font-normal leading-relaxed text-muted-foreground">Next: {label}</span>
            </>
          ) : (
            <>
              <span className="font-semibold tracking-tight">Next: {label}</span>
              {description && (
                <span className="text-xs font-normal leading-relaxed text-muted-foreground">{description}</span>
              )}
            </>
          )}
        </div>
        <ChevronRight
          className="h-4 w-4 shrink-0 rtl:rotate-180 animate-[float_1.8s_ease-in-out_infinite]"
          aria-hidden="true"
        />
      </Button>
    </span>
  );
});
