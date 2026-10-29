import { memo, useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Link2, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAddVideo } from '@/hooks/use-videos';
import { hasPlaylistId, hasVideoId, isYouTubeUrl } from '@/lib/youtube-utils';
import { cn } from '@/lib/utils';
import { showDuplicateToast } from '@/features/video-output/lib/duplicate-toast';
import { BillingNoticeAlert } from '@/components/usage/BillingNoticeAlert';
import { classifyBillingError, type BillingNotice } from '@/lib/billing-errors';

interface BoardInlinePasteProps {
  /** Folder to add the video into — passed from the current Board selection.
   *  null => add to the library root (no folder). */
  folderId: string | null;
  className?: string;
}

/**
 * Compact URL paste bar for the Board page — the single-field counterpart to
 * VideoIntakeForm's hero layout on /generate. Exists because Jordan's first
 * post-signup view is Board; forcing a nav to /generate before the first paste
 * is a step we don't need to charge for.
 *
 * Inherits the currently-selected folder so a paste inside a folder view
 * drops the new video straight into that folder instead of the root.
 */
export const BoardInlinePaste = memo(function BoardInlinePaste({
  folderId,
  className,
}: BoardInlinePasteProps) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [billingNotice, setBillingNotice] = useState<BillingNotice | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const addVideo = useAddVideo();

  const trimmed = url.trim();
  const isInvalidUrl = trimmed.length > 0 && !isYouTubeUrl(trimmed);
  const isLoading = addVideo.isPending;

  const handleSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const value = url.trim();
    if (!value) return;

    if (!hasVideoId(value)) {
      setError(
        hasPlaylistId(value)
          ? "That's a playlist link — open the Summarize page to import it."
          : "That doesn't look like a YouTube video URL. Paste the full watch link.",
      );
      return;
    }

    try {
      setError(null);
      setBillingNotice(null);
      const result = await addVideo.mutateAsync({
        url: value,
        folderId: folderId ?? undefined,
      });
      if (result?.video?.id) {
        if (result.duplicate) {
          void showDuplicateToast();
        }
        navigate(`/video/${result.video.id}`);
      }
    } catch (err) {
      const notice = classifyBillingError(err);
      if (notice) {
        setBillingNotice(notice);
        return;
      }
      setError(
        err instanceof Error
          ? err.message
          : "Couldn't start processing. Try again in a moment.",
      );
    }
  };

  return (
    <div className={cn('w-full', className)}>
      <form
        onSubmit={handleSubmit}
        noValidate
        className={cn(
          'flex items-stretch gap-2 rounded-xl p-1.5',
          'bg-card border border-border/60',
          'transition-shadow duration-200 ease-[var(--ease-out-expo)]',
          isInvalidUrl
            ? 'ring-1 ring-destructive/40 focus-within:ring-destructive/60'
            : 'focus-within:border-border focus-within:shadow-[0_0_24px_-12px_oklch(from_var(--primary)_l_c_h_/_0.35)]',
          'motion-reduce:transition-none',
        )}
        aria-busy={isLoading}
      >
        <div className="flex-1 flex items-center gap-2 ps-2">
          <Link2 className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />
          <Input
            ref={inputRef}
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              if (error) setError(null);
              if (billingNotice) setBillingNotice(null);
            }}
            placeholder="Paste a YouTube URL to add a new summary…"
            aria-label="YouTube URL"
            aria-invalid={error || isInvalidUrl ? true : undefined}
            aria-describedby={
              error ? 'board-paste-error' : isInvalidUrl ? 'board-paste-hint' : undefined
            }
            disabled={isLoading}
            className="h-9 border-0 bg-transparent text-sm shadow-none focus-visible:ring-0 focus-visible:border-0 px-0"
          />
        </div>
        <Button
          type="submit"
          size="sm"
          disabled={!trimmed || isLoading}
          className="shrink-0 font-semibold px-4 gap-1.5 rounded-lg"
        >
          {isLoading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Starting…
            </>
          ) : (
            <>
              Summarize
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </>
          )}
        </Button>
      </form>

      {isInvalidUrl && !error && (
        <p
          id="board-paste-hint"
          className="mt-1.5 text-xs text-destructive/90"
          role="alert"
        >
          That doesn&apos;t look like a YouTube link.
        </p>
      )}

      {billingNotice && <BillingNoticeAlert notice={billingNotice} className="mt-2" />}

      {error && (
        <p
          id="board-paste-error"
          role="alert"
          className="mt-1.5 text-xs text-destructive"
        >
          {error}
        </p>
      )}
    </div>
  );
});
