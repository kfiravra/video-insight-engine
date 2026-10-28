import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Loader2, Folder, Link2, ListVideo } from "lucide-react";
import { withTransitionName } from "@/lib/view-transitions";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FolderTreeSelect } from "../folders/FolderTreeSelect";
import { PlaylistPreview } from "@/components/playlists/PlaylistPreview";
import { PlaylistImportSummary } from "@/components/playlists/PlaylistImportSummary";
import { useUIStore } from "@/stores/ui-store";
import { useAddVideo } from "@/hooks/use-videos";
import { usePlaylistPreview, usePlaylistImport } from "@/hooks/use-playlists";
import { useFolders } from "@/hooks/use-folders";
import { getFolderColorStyle } from "@/features/sidebar/lib/style-utils";
import { cn } from "@/lib/utils";
import { hasVideoId, hasPlaylistId, isPlaylistPage } from "@/lib/youtube-utils";
import { showDuplicateToast } from "@/features/video-output/lib/duplicate-toast";
import type {
  PlaylistImportResult,
  PlaylistPreview as PlaylistPreviewType,
} from "@/api/playlists";

type Mode = "video" | "playlist";

// Sentinel value to indicate "use sidebar selection" instead of explicit user choice
const USE_SIDEBAR_SELECTION = Symbol.for("use-sidebar-selection");
type FolderSelection = string | null | typeof USE_SIDEBAR_SELECTION;

export function AddVideoInput() {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<Mode>("video");
  const [error, setError] = useState<string | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [playlistPreview, setPlaylistPreview] = useState<PlaylistPreviewType | null>(null);
  const [showPreviewDialog, setShowPreviewDialog] = useState(false);
  /** Set when an import left items out; the dialog then shows why instead of the preview. */
  const [importResult, setImportResult] = useState<PlaylistImportResult | null>(null);
  /** Form is the morph source for the "Crystallization" navigation transition —
   *  the input shape becomes the streaming detail page's spine. */
  const formRef = useRef<HTMLFormElement>(null);

  const selectedFolderId = useUIStore((s) => s.selectedFolderId);
  const activeSection = useUIStore((s) => s.activeSection);
  const addVideo = useAddVideo();
  const previewMutation = usePlaylistPreview();
  const importMutation = usePlaylistImport();
  const navigate = useNavigate();

  // Fetch folders for the selector
  const { data: foldersData } = useFolders();

  // Track user's explicit folder selection
  const [userSelection, setUserSelection] = useState<FolderSelection>(USE_SIDEBAR_SELECTION);

  // Derive target folder: user selection takes precedence over sidebar
  const targetFolderId = userSelection === USE_SIDEBAR_SELECTION
    ? selectedFolderId
    : userSelection;

  const handleFolderSelect = (folderId: string | null) => {
    setUserSelection(folderId);
    setDropdownOpen(false);
  };

  const toggleMode = () => {
    setMode(mode === "video" ? "playlist" : "video");
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    // Only allow adding videos in summarized section
    if (activeSection !== "summarized") {
      setError("You can only add videos from the Library tab.");
      return;
    }

    const trimmedUrl = url.trim();

    if (mode === "video") {
      // Validate URL has video ID
      if (!hasVideoId(trimmedUrl)) {
        if (hasPlaylistId(trimmedUrl)) {
          setError("That's a playlist link. Switch to Playlist mode to import it.");
        } else {
          setError("That doesn't look like a YouTube video URL. Paste the full link from YouTube.");
        }
        return;
      }

      try {
        setError(null);
        // For videos: only pass folderId if user explicitly selected one (not sidebar default)
        // This ensures videos go to root by default, matching expected behavior
        const explicitFolderId = userSelection !== USE_SIDEBAR_SELECTION ? userSelection : undefined;
        const result = await addVideo.mutateAsync({ url: trimmedUrl, folderId: explicitFolderId ?? undefined });
        setUrl("");
        setUserSelection(USE_SIDEBAR_SELECTION);

        if (result?.video?.id) {
          const videoId = result.video.id;
          if (result.duplicate) {
            void showDuplicateToast();
          }
          withTransitionName(
            formRef.current,
            "vie-input-spine",
            () => navigate(`/video/${videoId}`),
            { type: "stream-input-spine" },
          );
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't start processing. Try again in a moment.");
      }
    } else {
      // Playlist mode - validate URL has playlist ID
      if (!hasPlaylistId(trimmedUrl)) {
        if (hasVideoId(trimmedUrl) && !isPlaylistPage(trimmedUrl)) {
          setError("That's a single video link. Switch to Video mode to process it.");
        } else {
          setError("That doesn't look like a YouTube playlist URL.");
        }
        return;
      }

      // Fetch playlist preview
      try {
        setError(null);
        const preview = await previewMutation.mutateAsync({ url: trimmedUrl });
        setPlaylistPreview(preview);
        setShowPreviewDialog(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't read that playlist. It may be private, or the link is wrong.");
      }
    }
  };

  const handleImportPlaylist = async () => {
    if (!playlistPreview) return;

    try {
      // For playlists: only pass folderId if user explicitly selected one (not sidebar default)
      // This ensures playlists create their own folder by default
      const explicitFolderId = userSelection !== USE_SIDEBAR_SELECTION ? userSelection : undefined;
      const result = await importMutation.mutateAsync({ url: url.trim(), folderId: explicitFolderId ?? undefined });
      setShowPreviewDialog(result.failedCount > 0);
      setImportResult(result.failedCount > 0 ? result : null);
      setPlaylistPreview(null);
      setUrl("");
      setUserSelection(USE_SIDEBAR_SELECTION);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start the playlist import. Try again.");
    }
  };

  const folders = foldersData?.folders || [];
  const selectedFolder = folders.find((f) => f.id === targetFolderId);
  const folderTooltip = selectedFolder?.name || "No folder selected";

  const isLoading = addVideo.isPending || previewMutation.isPending || importMutation.isPending;

  return (
    <>
      <form ref={formRef} onSubmit={handleSubmit} className="relative">
        <div className="relative flex items-center">
          {/* Mode toggle button */}
          <TooltipProvider delayDuration={400}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute start-1 h-7 w-7 z-10"
                  onClick={toggleMode}
                  disabled={isLoading}
                  aria-label={mode === "video" ? "Switch to playlist mode" : "Switch to single video mode"}
                >
                  {mode === "video" ? (
                    <Link2 className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ListVideo className="h-4 w-4 text-primary" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">
                {mode === "video" ? "Single Video mode (click to switch to Playlist)" : "Playlist mode (click to switch to Video)"}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>

          {/* URL input */}
          <Input
            type="url"
            aria-label={mode === "video" ? "Paste a YouTube video URL" : "Paste a YouTube playlist URL"}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "add-video-error" : undefined}
            placeholder={mode === "video" ? "Paste YouTube URL..." : "Paste playlist URL..."}
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              if (error) setError(null);
            }}
            className="h-8 ps-9 pe-[70px] text-xs bg-muted border-border"
            disabled={isLoading}
          />

          {/* Right side: folder + add buttons */}
          <div className="absolute end-1 flex items-center gap-0.5">
            {/* Folder dropdown trigger with tooltip */}
            <TooltipProvider delayDuration={400}>
              <Tooltip>
                <DropdownMenu open={dropdownOpen} onOpenChange={setDropdownOpen}>
                  <TooltipTrigger asChild>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="group h-7 w-7 hover:bg-accent hover:scale-110 transition-[background-color,transform]"
                        type="button"
                        disabled={isLoading}
                        aria-label={`Pick target folder (currently ${folderTooltip})`}
                      >
                        <Folder
                          className={cn(
                            "h-4 w-4 transition-colors",
                            selectedFolder
                              ? "group-hover:opacity-80"
                              : "text-muted-foreground group-hover:text-foreground"
                          )}
                          style={selectedFolder ? getFolderColorStyle(selectedFolder.color) : undefined}
                        />
                      </Button>
                    </DropdownMenuTrigger>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">
                    {folderTooltip}
                  </TooltipContent>
                  <DropdownMenuContent align="end" className="w-56 max-h-60 overflow-y-auto">
                    <FolderTreeSelect
                      folders={folders}
                      currentFolderId={null}
                      onSelect={handleFolderSelect}
                      showRemoveOption={true}
                      removeLabel="No folder"
                    />
                  </DropdownMenuContent>
                </DropdownMenu>
              </Tooltip>
            </TooltipProvider>

            {/* Add button */}
            <Button
              type="submit"
              size="icon"
              className="h-7 w-7"
              disabled={!url.trim() || isLoading}
              aria-label={mode === "video" ? "Add video" : "Preview playlist"}
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>

        {/* Error message — absolute to avoid pushing header height */}
        {error && (
          <p
            id="add-video-error"
            role="alert"
            className="absolute top-full inset-x-0 text-xs text-destructive mt-1 px-1 z-50"
          >
            {error}
          </p>
        )}
      </form>

      {/* Playlist Preview Dialog */}
      <Dialog
        open={showPreviewDialog}
        onOpenChange={(open) => {
          setShowPreviewDialog(open);
          if (!open) setImportResult(null);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Import Playlist</DialogTitle>
          </DialogHeader>
          {importResult && (
            <PlaylistImportSummary
              result={importResult}
              onDismiss={() => {
                setShowPreviewDialog(false);
                setImportResult(null);
              }}
            />
          )}
          {!importResult && playlistPreview && (
            <PlaylistPreview
              playlist={playlistPreview}
              onImport={handleImportPlaylist}
              isImporting={importMutation.isPending}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
