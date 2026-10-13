import { Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface DegradedNoticeProps {
  /** Re-runs the pipeline via the bypass-cache path (useRetryVideo). */
  onRetry: () => void;
  /** True while the retry mutation is in flight — disables the button. */
  retrying?: boolean;
  className?: string;
}

/**
 * Partial-result banner for degraded pipeline runs.
 *
 * Shown when the summarizer flagged the run `degraded` (an extraction batch
 * or field group failed, or coverage was critical — see meta.degraded / the
 * SSE terminal events). Offers a one-click retry wired to the existing
 * `?bypassCache=true` re-summarization path.
 */
export function DegradedNotice({ onRetry, retrying = false, className }: DegradedNoticeProps) {
  return (
    <div
      role="status"
      className={cn(
        "mx-auto mb-4 flex max-w-3xl flex-wrap items-center gap-3 rounded-xl",
        "border border-[var(--warning)]/40 bg-[var(--warning-soft)] px-4 py-3",
        className,
      )}
    >
      <TriangleAlert
        className="h-5 w-5 shrink-0 text-[var(--warning)]"
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-foreground">Partial result</p>
        <p className="text-xs text-muted-foreground">
          Some of this video didn&apos;t make it into the summary. Retry to
          reprocess it from scratch.
        </p>
      </div>
      <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying}>
        {retrying ? (
          <Loader2
            className="me-2 h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
        ) : (
          <RefreshCw className="me-2 h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        Retry
      </Button>
    </div>
  );
}
