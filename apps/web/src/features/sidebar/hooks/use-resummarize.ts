import { toast } from "sonner";
import { useBillingText } from "@/hooks/use-billing-text";
import { useRetryVideo } from "@/hooks/use-videos";
import { describeRetryFailure } from "@/lib/billing-notice-text";
import type { Video } from "@/types";

interface Resummarize {
  /** Re-summarize `video`; a refusal (not enough minutes, blocked, rate limited, …) shows as a toast. */
  resummarize: (video: Pick<Video, "youtubeId" | "folderId">) => void;
  isPending: boolean;
}

/**
 * Re-summarize from the sidebar. It is a billed submit like any other, so the
 * ledger can refuse it; the sidebar has no room for an inline notice, so the
 * reason goes to a toast, in the user's language.
 */
export function useResummarize(): Resummarize {
  const retryVideo = useRetryVideo();
  const billingText = useBillingText();

  const resummarize = (video: Pick<Video, "youtubeId" | "folderId">): void => {
    if (!video.youtubeId) return;
    retryVideo.mutate(
      { youtubeId: video.youtubeId, folderId: video.folderId },
      { onError: (error) => toast.error(describeRetryFailure(error, billingText, Date.now())) },
    );
  };

  return { resummarize, isPending: retryVideo.isPending };
}
