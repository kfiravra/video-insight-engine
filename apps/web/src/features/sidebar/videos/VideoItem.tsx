import { useState, useCallback, useMemo, useRef, memo } from "react";
import { Link, useLocation } from "react-router-dom";
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { Film, Star } from "lucide-react";
import { StatusIcon } from "@/components/ui/status-icon";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { DeleteVideoDialog } from "@/features/sidebar/dialogs/DeleteVideoDialog";
import { cn } from "@/lib/utils";

import { SIDEBAR_LAYOUT } from "@/features/sidebar/lib/layout-constants";
import { useMoveVideo, useDeleteVideo, useRetryVideo } from "@/hooks/use-videos";
import { useSidebarTextClasses } from "@/features/sidebar/hooks/use-sidebar-text-size";
import { useLongPress } from "@/features/sidebar/hooks/use-long-press";
import { useIsTruncated } from "@/hooks/use-is-truncated";
import { useUIStore, useSelectionMode, useIsFavorite } from "@/stores/ui-store";

import type { Video, Folder as FolderData } from "@/types";
import { VideoContextMenu } from "./VideoContextMenu";

interface VideoItemProps {
  video: Video;
  level: number;
  folders?: FolderData[];
}

export const VideoItem = memo(function VideoItem({ video, level, folders = [] }: VideoItemProps) {
  const moveVideo = useMoveVideo();
  const deleteVideo = useDeleteVideo();
  const retryVideo = useRetryVideo();
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const textClasses = useSidebarTextClasses();
  const titleRef = useRef<HTMLAnchorElement>(null);
  const isTruncated = useIsTruncated(titleRef);
  const [tooltipOpen, setTooltipOpen] = useState(false);

  // Router-aware active check — component is memoized, so only items whose
  // pathname match flips actually re-render.
  const { pathname } = useLocation();
  const isActiveVideo = pathname === `/video/${video.id}`;

  // Selection mode state
  const selectionMode = useSelectionMode();
  const enterSelectionMode = useUIStore((s) => s.enterSelectionMode);
  const handleVideoSelection = useUIStore((s) => s.handleVideoSelection);
  const isVideoSelected = useUIStore((s) => s.isVideoSelected);
  const isSelected = isVideoSelected(video.id);
  const selectedVideoIds = useUIStore((s) => s.selectedVideoIds);
  const selectedFolderIds = useUIStore((s) => s.selectedFolderIds);
  const setSelectedFolder = useUIStore((s) => s.setSelectedFolder);
  const isFavorite = useIsFavorite(video.id);
  const toggleFavorite = useUIStore((s) => s.toggleFavorite);

  const handleToggleFavorite = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      toggleFavorite(video.id);
    },
    [toggleFavorite, video.id]
  );

  // Long press for entering selection mode
  const longPress = useLongPress({
    onLongPress: () => enterSelectionMode(video.id, undefined),
    disabled: selectionMode,
  });

  // DnD draggable - use prefixed ID to ensure uniqueness
  // Enable drag for selected items in selection mode, disable for unselected
  const isMultiDrag = selectionMode && isSelected;
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `video-${video.id}`,
    data: {
      type: isMultiDrag ? "multi" : "video",
      id: video.id,
      title: video.title || "Untitled",
      selectedVideoIds: isMultiDrag ? selectedVideoIds : [video.id],
      selectedFolderIds: isMultiDrag ? selectedFolderIds : [],
    },
    // Only disable drag when in selection mode AND this item is NOT selected
    disabled: selectionMode && !isSelected,
  });

  const transformString = transform ? CSS.Translate.toString(transform) : undefined;

  // Use consistent padding with FolderItem (level already includes +1 from parent)
  const paddingLeft = SIDEBAR_LAYOUT.BASE_PADDING + level * SIDEBAR_LAYOUT.INDENT_PER_LEVEL;


  // Memoized so the object identity is stable across re-renders triggered by
  // unrelated state (hover, tooltip), preserving memo() benefits.
  const rowStyle = useMemo<React.CSSProperties>(
    () => ({
      transform: transformString,
      paddingInlineStart: `${paddingLeft}px`,
      paddingInlineEnd: "8px",
    }),
    [transformString, paddingLeft],
  );

  const handleMoveToFolder = (folderId: string | null) => {
    moveVideo.mutate({ id: video.id, folderId });
  };

  const handleDeleteConfirm = () => {
    deleteVideo.mutate(video.id, {
      onSuccess: () => {
        setShowDeleteDialog(false);
      },
    });
  };

  const handleResummarize = () => {
    if (!video.youtubeId) return;
    retryVideo.mutate({ youtubeId: video.youtubeId, folderId: video.folderId });
  };

  // Handle click - supports Shift+Click and Ctrl+Click for selection
  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      // If Shift or Ctrl is held, use selection mode behavior
      if (e.shiftKey || e.ctrlKey || e.metaKey) {
        e.preventDefault();
        e.stopPropagation();
        handleVideoSelection(video.id, e.shiftKey, e.ctrlKey || e.metaKey);
        return;
      }

      // In selection mode, toggle selection on click (add or remove)
      if (selectionMode) {
        e.preventDefault();
        e.stopPropagation();
        // Pass true for ctrlKey to toggle selection
        handleVideoSelection(video.id, false, true);
      }
    },
    [selectionMode, handleVideoSelection, video.id]
  );

  return (
    <TooltipProvider delayDuration={400}>
      <Tooltip open={isTruncated && tooltipOpen} onOpenChange={setTooltipOpen}>
        <TooltipTrigger asChild>
          <div
            ref={setNodeRef}
            data-sidebar-item="video"
            style={rowStyle}
            className={cn(
              "group relative flex items-center rounded-md",
              "transition-[background-color,box-shadow,transform] duration-150 ease-[var(--ease-out-expo)]",
              "hover:bg-[var(--glass-bg)] hover:shadow-[var(--glass-shadow)] hover:-translate-y-px",
              "motion-reduce:hover:translate-y-0 motion-reduce:transition-none",
              "has-[[data-state=open]]:bg-accent/50",
              textClasses.rowHeight,
              isDragging && "opacity-50 z-50",
              isSelected && "bg-primary/6",
              isActiveVideo && !isSelected && "bg-primary/10 font-medium ring-1 ring-primary/20 text-foreground"
            )}
            onPointerDown={longPress.onPointerDown}
            onPointerUp={longPress.onPointerUp}
            onPointerLeave={longPress.onPointerLeave}
            onClick={handleClick}
            {...((!selectionMode || isSelected) && attributes)}
            {...((!selectionMode || isSelected) && listeners)}
          >
            {/* Checkbox in selection mode, otherwise spacer */}
            {selectionMode ? (
              <Checkbox
                checked={isSelected}
                onCheckedChange={() => handleVideoSelection(video.id, false, true)}
                className="w-4 h-4 shrink-0"
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span className="w-4 shrink-0" />
            )}

            {/* Video Icon */}
            <Film className={cn(textClasses.iconSize, "shrink-0 text-primary/80 fill-warning/50 ms-1 transition-colors group-hover:text-primary")} />

            {/* Video link */}
            <Link
              ref={titleRef}
              to={`/video/${video.id}`}
              className={cn("ms-2 truncate flex-1 cursor-pointer", textClasses.mainText)}
              onClick={(e) => {
                if (isDragging || selectionMode) {
                  e.preventDefault();
                  return;
                }
                // Clear folder selection when navigating to a video
                setSelectedFolder(null);
              }}
            >
              {video.title || "Processing..."}
            </Link>

            {/* Star button — hover-reveal on idle, persistent when starred.
                Suppressed in selection mode so the checkbox owns the click. */}
            {!selectionMode && (
              <button
                type="button"
                onClick={handleToggleFavorite}
                aria-label={isFavorite ? "Remove star" : "Star"}
                aria-pressed={isFavorite}
                className={cn(
                  "shrink-0 h-6 w-6 rounded-md flex items-center justify-center transition-[opacity,color,background-color] duration-150",
                  "motion-reduce:transition-none",
                  "hover:bg-accent/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isFavorite
                    ? "opacity-100 text-[color:var(--vie-accent)]"
                    : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-muted-foreground hover:text-foreground"
                )}
              >
                <Star
                  className={cn("h-3.5 w-3.5", isFavorite && "fill-[color:var(--vie-accent)]")}
                  aria-hidden="true"
                />
              </button>
            )}

            {/* Status icon and context menu - invisible in selection mode to prevent layout shift */}
            <div className={cn(
              "flex items-center shrink-0",
              selectionMode && "invisible pointer-events-none"
            )}>
              {/* Status icon — always reserve space for consistent alignment */}
              <span className="shrink-0 w-[26px] flex items-center justify-center">
                {video.status !== "completed" && (
                  <TooltipProvider delayDuration={0}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="p-1">
                          <StatusIcon status={video.status} size={16} />
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="top" sideOffset={4}>
                        {video.status === "pending" && "Pending"}
                        {video.status === "processing" && "Processing..."}
                        {video.status === "failed" && "Failed to process"}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )}
              </span>

              {/* Context menu */}
              <VideoContextMenu
                video={video}
                folders={folders}
                textClasses={textClasses}
                onMoveToFolder={handleMoveToFolder}
                onDelete={() => setShowDeleteDialog(true)}
                onResummarize={handleResummarize}
                isRetrying={retryVideo.isPending}
              />
            </div>

            {/* Processing shimmer bar */}
            {video.status === "processing" && (
              <span
                aria-hidden="true"
                className="absolute inset-x-0 bottom-0 h-[2px] rounded-full overflow-hidden"
              >
                <span className="block h-full w-full animate-[shimmer-travel_1.5s_infinite] bg-[length:200%_100%] bg-[linear-gradient(90deg,transparent_0%,var(--primary)_50%,transparent_100%)]" />
              </span>
            )}
            {video.status === "failed" && (
              <span
                aria-hidden="true"
                className="absolute inset-x-0 bottom-0 h-[2px] rounded-full bg-destructive/60"
              />
            )}
          </div>
        </TooltipTrigger>
        <TooltipContent
          side="right"
          align="start"
          sideOffset={8}
          className="max-w-xs z-100"
        >
          {video.title || "Processing..."}
        </TooltipContent>
      </Tooltip>

      {/* Delete confirmation dialog - outside tooltip to prevent interference */}
      <DeleteVideoDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        videoTitle={video.title || "Untitled video"}
        onConfirm={handleDeleteConfirm}
        isPending={deleteVideo.isPending}
      />
    </TooltipProvider>
  );
});
