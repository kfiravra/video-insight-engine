import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Folder, Link2, ListVideo, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  VieMenu,
  VieMenuItem,
  VieMenuSeparator,
  VieMenuHeader,
} from '@/components/vie';
import { FolderTreeSelect } from '@/features/sidebar/folders/FolderTreeSelect';
import { useAddVideo } from '@/hooks/use-videos';
import { useFolders } from '@/hooks/use-folders';
import { usePlaylistPreview, usePlaylistImport } from '@/hooks/use-playlists';
import { getFolderColorStyle } from '@/features/sidebar/lib/style-utils';
import { hasPlaylistId, hasVideoId, isPlaylistPage, isYouTubeUrl } from '@/lib/youtube-utils';
import { withTransitionName } from '@/lib/view-transitions';
import { cn } from '@/lib/utils';
import type { PlaylistPreview as PlaylistPreviewType } from '@/api/playlists';
import { PlaylistPreview } from '@/components/playlists/PlaylistPreview';
import type { Folder as FolderEntity } from '@/types';
import { SAMPLE_VIDEO } from '@/features/video-output/lib/onboarding-constants';
import { showDuplicateToast } from '@/features/video-output/lib/duplicate-toast';
import { ApiError } from '@/api/client';
import { DailyLimitCallout } from '@/components/usage/DailyLimitCallout';

type Mode = 'video' | 'playlist';

/** Classifies a trimmed URL as playlist or video. Falls back to `fallback`
 *  when the URL is ambiguous (e.g. bare youtube.com). Used both by the paste
 *  effect (UI feedback) and at submit time (source of truth) so a fast click
 *  after paste can't read a stale `mode` state. */
function detectMode(trimmed: string, fallback: Mode): Mode {
  if (hasPlaylistId(trimmed) && !hasVideoId(trimmed)) return 'playlist';
  if (isPlaylistPage(trimmed)) return 'playlist';
  if (hasVideoId(trimmed)) return 'video';
  return fallback;
}

interface VideoIntakeFormProps {
  className?: string;
  /** URL handed over from another page (the landing form). Prefills the
   *  input and is submitted once on mount, as if the user had pressed
   *  Summarize here. */
  initialUrl?: string;
}

/**
 * Hero entry form — the Generate page's primary surface.
 * Distinct from the sidebar's compact AddVideoInput: glass surface, focus-glow,
 * larger controls, prominent Summarize CTA. Shares tokens, not markup.
 */
export function VideoIntakeForm({ className, initialUrl }: VideoIntakeFormProps) {
  const [url, setUrl] = useState(initialUrl ?? '');
  const [mode, setMode] = useState<Mode>(() => detectMode((initialUrl ?? '').trim(), 'video'));
  const [error, setError] = useState<string | null>(null);
  const [dailyLimit, setDailyLimit] = useState<{ resetAt: string | null; limitUsd: number | null } | null>(null);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [playlistPreview, setPlaylistPreview] = useState<PlaylistPreviewType | null>(null);

  const formRef = useRef<HTMLFormElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const addVideo = useAddVideo();
  const previewMutation = usePlaylistPreview();
  const importMutation = usePlaylistImport();
  const { data: foldersData } = useFolders();
  const folders = foldersData?.folders ?? [];
  const selectedFolder = folders.find((f) => f.id === selectedFolderId);

  const isLoading =
    addVideo.isPending || previewMutation.isPending || importMutation.isPending;

  const trimmed = url.trim();
  const isInvalidUrl = trimmed.length > 0 && !isYouTubeUrl(trimmed);

  // Submit a handed-over URL once the form has mounted. Deferred a tick with
  // cleanup so StrictMode's mount → unmount → mount fires a single submit from
  // the surviving mount, keeping the mutation observer attached to it.
  useEffect(() => {
    if (!initialUrl) return;
    const timer = setTimeout(() => formRef.current?.requestSubmit(), 0);
    return () => clearTimeout(timer);
  }, [initialUrl]);

  const handleSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;

    // Source of truth: derive mode from the URL at submit time, not state.
    const resolvedMode = detectMode(trimmed, mode);
    if (resolvedMode !== mode) setMode(resolvedMode);

    if (resolvedMode === 'video') {
      if (!hasVideoId(trimmed)) {
        setError(
          hasPlaylistId(trimmed)
            ? "That's a playlist link — switch to Playlist mode to import it."
            : "That doesn't look like a YouTube video URL. Paste the full link from YouTube.",
        );
        return;
      }
      try {
        setError(null);
        setDailyLimit(null);
        const result = await addVideo.mutateAsync({
          url: trimmed,
          folderId: selectedFolderId ?? undefined,
        });
        if (result?.video?.id) {
          const videoId = result.video.id;
          // Idempotency hit: server returned the existing videoSummary instead
          // of starting a new pipeline. Surface a low-key toast so the user
          // understands their submit was redirected rather than ignored.
          if (result.duplicate) {
            void showDuplicateToast();
          }
          withTransitionName(
            formRef.current,
            'vie-input-spine',
            () => navigate(`/video/${videoId}`),
            { type: 'stream-input-spine' },
          );
        }
      } catch (err) {
        if (err instanceof ApiError && err.code === 'DAILY_LIMIT_REACHED') {
          const details = err.details ?? {};
          setDailyLimit({
            resetAt: typeof details.resetAt === 'string' ? details.resetAt : null,
            limitUsd: typeof details.limitUsd === 'number' ? details.limitUsd : null,
          });
          setError(null);
          return;
        }
        setError(
          err instanceof Error
            ? err.message
            : "Couldn't start processing. Try again in a moment.",
        );
      }
    } else {
      if (!hasPlaylistId(trimmed)) {
        setError("That doesn't look like a YouTube playlist URL.");
        return;
      }
      try {
        setError(null);
        const preview = await previewMutation.mutateAsync({ url: trimmed });
        setPlaylistPreview(preview);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Couldn't read that playlist. It may be private, or the link is wrong.",
        );
      }
    }
  };

  const handleImportPlaylist = async () => {
    if (!playlistPreview) return;
    try {
      await importMutation.mutateAsync({
        url: url.trim(),
        folderId: selectedFolderId ?? undefined,
      });
      setPlaylistPreview(null);
      setUrl('');
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Couldn't start the playlist import. Try again.",
      );
    }
  };

  const handleSampleClick = () => {
    setUrl(SAMPLE_VIDEO.url);
    setError(null);
    setMode('video');
    inputRef.current?.focus();
    requestAnimationFrame(() => {
      inputRef.current?.setSelectionRange(SAMPLE_VIDEO.url.length, SAMPLE_VIDEO.url.length);
    });
  };

  return (
    <div className={cn('w-full', className)}>
      <form
        ref={formRef}
        onSubmit={handleSubmit}
        noValidate
        className={cn(
          'rounded-2xl p-2 bg-[var(--glass-bg)] border border-[var(--glass-border)]',
          'backdrop-blur-[var(--glass-blur,20px)] shadow-[var(--glass-shadow)]',
          'transition-shadow duration-200 ease-[var(--ease-out-expo)]',
          isInvalidUrl
            ? 'ring-1 ring-destructive/40 focus-within:ring-destructive/60'
            : 'focus-within:shadow-[0_0_40px_-16px_oklch(from_var(--primary)_l_c_h_/_0.6),var(--glass-shadow)]',
          'motion-reduce:transition-none',
        )}
        aria-busy={isLoading}
      >
        {/* Row 1: URL input + Summarize */}
        <div className="flex items-stretch gap-2">
          <div className="flex-1 flex items-center ps-3">
            <Input
              ref={inputRef}
              type="url"
              value={url}
              onChange={(e) => {
                const next = e.target.value;
                setUrl(next);
                // Auto-detect mode as the user pastes. Purely UI feedback —
                // submission derives mode fresh from the URL at submit time.
                if (next.trim()) setMode((prev) => detectMode(next.trim(), prev));
                if (error) setError(null);
              }}
              placeholder="Paste a YouTube video or playlist URL…"
              aria-label="YouTube URL"
              aria-invalid={error || isInvalidUrl ? true : undefined}
              aria-describedby={error ? 'intake-error' : isInvalidUrl ? 'intake-url-hint' : undefined}
              disabled={isLoading}
              className="h-12 border-0 bg-transparent text-base shadow-none focus-visible:ring-0 focus-visible:border-0 px-0"
            />
          </div>
          <Button
            type="submit"
            size="lg"
            disabled={!url.trim() || isLoading}
            className={cn(
              'shrink-0 font-bold px-5 gap-2 rounded-xl',
              'shadow-[0_6px_20px_-8px_oklch(from_var(--primary)_l_c_h_/_0.45)]',
              'hover:-translate-y-px active:translate-y-0 motion-reduce:hover:translate-y-0 transition-transform',
            )}
          >
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Starting…
              </>
            ) : (
              <>
                Summarize
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </>
            )}
          </Button>
        </div>

        {/* Row 2: mode pill + folder picker */}
        <div className="flex items-center justify-between gap-2 px-1 pt-2">
          <ModePillSwitch mode={mode} onChange={setMode} disabled={isLoading} />
          <FolderPickerChip
            folder={selectedFolder}
            folders={folders}
            onSelect={setSelectedFolderId}
            disabled={isLoading}
          />
        </div>
      </form>

      {/* Inline URL validation hint */}
      {isInvalidUrl && !error && (
        <p
          id="intake-url-hint"
          className="mt-2 text-xs text-destructive/90 text-center"
          role="alert"
        >
          That doesn&apos;t look like a YouTube link — try a watch URL like{' '}
          <span className="font-mono">youtube.com/watch?v=…</span>
        </p>
      )}

      {/* Daily cost limit callout (429) */}
      {dailyLimit && (
        <DailyLimitCallout
          limitUsd={dailyLimit.limitUsd}
          resetAtIso={dailyLimit.resetAt}
        />
      )}

      {/* Error */}
      {error && !dailyLimit && (
        <p
          id="intake-error"
          role="alert"
          className="mt-3 text-sm text-destructive text-center"
        >
          {error}
        </p>
      )}

      {/* Sample chip */}
      {!url.trim() && !error && (
        <button
          type="button"
          onClick={handleSampleClick}
          disabled={isLoading}
          data-testid="sample-url-button"
          className={cn(
            'mt-4 mx-auto flex items-center gap-2 text-xs text-muted-foreground/80',
            'hover:text-foreground transition-colors',
            'rounded-full px-3 py-1.5 bg-muted/30 hover:bg-muted/50 border border-border/40',
          )}
        >
          <span aria-hidden="true">↘</span>
          Try an example: {SAMPLE_VIDEO.label}
        </button>
      )}

      {/* Playlist preview — inline, not a modal */}
      {playlistPreview && mode === 'playlist' && (
        <div className="mt-6 rounded-2xl bg-[var(--glass-bg)] border border-[var(--glass-border)] p-4">
          <PlaylistPreview
            playlist={playlistPreview}
            onImport={handleImportPlaylist}
            isImporting={importMutation.isPending}
          />
        </div>
      )}
    </div>
  );
}

interface ModePillSwitchProps {
  mode: Mode;
  onChange: (m: Mode) => void;
  disabled?: boolean;
}

/** Mode pill switcher — Video · Playlist. */
function ModePillSwitch({ mode, onChange, disabled }: ModePillSwitchProps) {
  return (
    <div
      className="inline-flex rounded-full bg-muted/40 p-0.5 gap-0.5"
      role="radiogroup"
      aria-label="Input mode"
    >
      <ModePillButton
        active={mode === 'video'}
        onClick={() => onChange('video')}
        disabled={disabled}
        icon={<Link2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
        label="Video"
      />
      <ModePillButton
        active={mode === 'playlist'}
        onClick={() => onChange('playlist')}
        disabled={disabled}
        icon={<ListVideo className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
        label="Playlist"
      />
    </div>
  );
}

interface ModePillButtonProps {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  icon: ReactNode;
  label: string;
}

function ModePillButton({ active, onClick, disabled, icon, label }: ModePillButtonProps) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors',
        active
          ? 'bg-background text-foreground shadow-sm'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {icon}
      {label}
    </button>
  );
}

interface FolderPickerChipProps {
  folder: FolderEntity | undefined;
  folders: FolderEntity[];
  onSelect: (id: string | null) => void;
  disabled?: boolean;
}

/** Folder picker — tag chip opening VIE Menu. */
function FolderPickerChip({ folder, folders, onSelect, disabled }: FolderPickerChipProps) {
  return (
    <VieMenu
      align="end"
      side="top"
      accent={folder?.color ?? undefined}
      trigger={
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          className={cn(
            'h-7 rounded-full gap-1.5 px-3 text-xs font-medium',
            'border border-border/50 bg-muted/20 hover:bg-muted/40',
          )}
        >
          <span
            aria-hidden="true"
            className="inline-block size-1.5 rounded-full shrink-0"
            style={{
              background: folder?.color || 'var(--muted-foreground)',
              ...(folder?.color ? getFolderColorStyle(folder.color) : {}),
            }}
          />
          {folder ? `→ ${folder.name}` : 'No folder'}
        </Button>
      }
    >
      <VieMenuHeader>
        <span className="type-eyebrow text-[10px] text-muted-foreground">Add to folder</span>
      </VieMenuHeader>
      <VieMenuItem
        icon={<Folder className="h-4 w-4" />}
        onSelect={() => onSelect(null)}
      >
        No folder
      </VieMenuItem>
      {folders.length > 0 && <VieMenuSeparator />}
      <div className="max-h-64 overflow-y-auto">
        <FolderTreeSelect
          folders={folders}
          currentFolderId={null}
          onSelect={onSelect}
          showRemoveOption={false}
        />
      </div>
    </VieMenu>
  );
}
