import { Link } from "react-router-dom";
import { AlertCircle, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useBillingText } from "@/hooks/use-billing-text";
import { describeFailedImport, isMinutesFailure } from "@/lib/billing-notice-text";
import { fillTemplate } from "@/lib/format-minutes";
import type { PlaylistImportResult } from "@/api/playlists";

interface PlaylistImportSummaryProps {
  result: PlaylistImportResult;
  onDismiss: () => void;
}

/**
 * What a playlist import left out: "3 of 5 videos added" and, for each item
 * that failed, why. Shown only when something failed; a clean import has
 * nothing to report. Items stopped by the user's minutes point at /settings/billing.
 */
export function PlaylistImportSummary({ result, onDismiss }: PlaylistImportSummaryProps) {
  const text = useBillingText();
  const { labels } = text;
  const showMinutesLink = result.failedVideos.some(isMinutesFailure);

  return (
    <div
      role="status"
      data-testid="playlist-import-summary"
      className="mt-4 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-start"
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="h-4 w-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-foreground">
            {fillTemplate(labels.playlistResult, {
              added: text.number(result.videos.length),
              total: text.videos(result.totalVideos),
            })}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">{labels.playlistFailedHeading}</p>
          <ul className="mt-2 space-y-1.5">
            {result.failedVideos.map((item) => (
              <li key={`${item.youtubeId}-${item.position}`} className="flex flex-col gap-0.5 text-xs">
                <span className="truncate text-foreground">{item.title || item.youtubeId}</span>
                <span className="text-muted-foreground">{describeFailedImport(item, text)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        {showMinutesLink && (
          <Link
            to="/settings/billing"
            data-testid="playlist-import-minutes-link"
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
          >
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            {labels.calloutCta}
          </Link>
        )}
        <Button type="button" size="sm" variant="ghost" onClick={onDismiss}>
          {labels.playlistDismiss}
        </Button>
      </div>
    </div>
  );
}
