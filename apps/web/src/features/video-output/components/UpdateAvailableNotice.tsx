import { Loader2, RefreshCw } from "lucide-react";
import { ApiError } from "@/api/client";
import type { VideoDetail } from "@/api/videos";
import { Button } from "@/components/ui/button";
import { BillingNoticeAlert } from "@/components/usage/BillingNoticeAlert";
import { DirectionProvider } from "@/contexts/DirectionContext";
import { useUpdateVideoToLatest } from "@/hooks/use-videos";
import { classifyBillingError } from "@/lib/billing-errors";
import { useLabels } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const DEMO_RESTRICTED_CODE = "DEMO_RESTRICTED";

type NoticeVideo = Pick<VideoDetail, "id" | "youtubeId" | "folderId" | "status" | "updateAvailable">;

interface UpdateAvailableNoticeProps {
  video: NoticeVideo;
  /** Language and direction of the video's content: the copy reads in them,
   *  like the rest of the output (the page itself sits above that context). */
  language: string;
  isRTL: boolean;
  className?: string;
}

/**
 * Offers a stale copy an update. Opening an existing copy is always free; the
 * update is a separate, explicit action that runs as a new, billed video, so it
 * never starts by itself. Shown only for a completed video the API marks
 * `updateAvailable`. The API never sets that flag for the shared demo account, and
 * refuses the update there (403 `DEMO_RESTRICTED`) as a backstop.
 */
export function UpdateAvailableNotice({ video, language, isRTL, className }: UpdateAvailableNoticeProps) {
  if (video.status !== "completed" || video.updateAvailable !== true) return null;

  return (
    <DirectionProvider language={language} isRTL={isRTL}>
      <UpdateAvailableCard video={video} className={className} />
    </DirectionProvider>
  );
}

interface UpdateAvailableCardProps {
  video: NoticeVideo;
  className?: string;
}

function UpdateAvailableCard({ video, className }: UpdateAvailableCardProps) {
  const { billing } = useLabels();
  const update = useUpdateVideoToLatest();
  // A started update stays disabled until the refetch swaps the page to the
  // pending view: a second click would ask for a second run.
  const busy = update.isPending || update.isSuccess;

  const handleUpdate = () => {
    update.mutate({ videoId: video.id, youtubeId: video.youtubeId, folderId: video.folderId });
  };

  return (
    <div className={cn("mx-auto mb-4 max-w-3xl px-4", className)}>
      <div
        role="status"
        data-testid="update-available"
        className="flex flex-wrap items-center gap-3 rounded-xl border border-border/60 bg-card px-4 py-3"
      >
        <RefreshCw className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">{billing.updateAvailable}</p>
          <p className="text-xs text-muted-foreground">{billing.updateCost}</p>
        </div>
        <Button size="sm" variant="outline" onClick={handleUpdate} disabled={busy}>
          {busy ? (
            <Loader2
              className="me-2 h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
          ) : null}
          {billing.updateAction}
        </Button>
      </div>
      <UpdateError error={update.error} />
    </div>
  );
}

interface UpdateErrorProps {
  error: unknown;
}

/** Why the update did not start: a billing refusal gets the usual notice, the demo account's refusal and any other failure a localised line. */
function UpdateError({ error }: UpdateErrorProps) {
  const { billing } = useLabels();
  if (!error) return null;

  const notice = classifyBillingError(error);
  if (notice) return <BillingNoticeAlert notice={notice} className="mt-2" />;

  const isDemoRefusal = error instanceof ApiError && error.code === DEMO_RESTRICTED_CODE;
  return (
    <p role="alert" data-testid="update-error" className="mt-2 text-sm text-destructive">
      {isDemoRefusal ? billing.updateDemo : billing.updateFailed}
    </p>
  );
}
