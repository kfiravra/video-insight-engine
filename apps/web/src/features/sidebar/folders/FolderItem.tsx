import { useState, useCallback, useMemo, useRef, memo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ChevronRight, Folder, Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { DeleteFolderDialog } from "@/features/sidebar/dialogs/DeleteFolderDialog";
import { cn } from "@/lib/utils";
import { getFolderColorStyle } from "@/features/sidebar/lib/style-utils";
import { SIDEBAR_LAYOUT } from "@/features/sidebar/lib/layout-constants";
import { useUIStore, useSelectionMode } from "@/stores/ui-store";
import { useDeleteFolder } from "@/hooks/use-folders";
import { useSidebarTextClasses } from "@/features/sidebar/hooks/use-sidebar-text-size";
import { useLongPress } from "@/features/sidebar/hooks/use-long-press";
import { useIsTruncated } from "@/hooks/use-is-truncated";
import { useFolderDragDrop } from "@/features/sidebar/hooks/use-folder-drag-drop";
import { FolderRenameInput } from "./FolderRenameInput";
import { CreateSubfolderInput } from "./CreateSubfolderInput";
import { FolderContextMenu } from "./FolderContextMenu";
import { VideoItem } from "../videos/VideoItem";
import type { FolderNode } from "@/features/sidebar/lib/folder-utils";
import type { Video, Folder as FolderData } from "@/types";

interface FolderItemProps {
  folder: FolderNode;
  level: number;
  videos: Video[];
  allFolders: FolderData[];
}

// Recursion safety net — deeper than this is almost certainly a data cycle,
// not a real folder tree. Bail out so a bad API payload can't stack-overflow.
const MAX_FOLDER_DEPTH = 20;

// Memoized to prevent cascading re-renders in sidebar tree
export const FolderItem = memo(function FolderItem({ folder, level, videos, allFolders }: FolderItemProps) {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // Zustand state
  const expandedFolderIds = useUIStore((s) => s.expandedFolderIds);
  const toggleFolderExpansion = useUIStore((s) => s.toggleFolderExpansion);
  const selectedFolderId = useUIStore((s) => s.selectedFolderId);
  const setSelectedFolder = useUIStore((s) => s.setSelectedFolder);
  const setActiveSection = useUIStore((s) => s.setActiveSection);

  // Selection mode state
  const selectionMode = useSelectionMode();
  const enterSelectionMode = useUIStore((s) => s.enterSelectionMode);
  const handleFolderSelection = useUIStore((s) => s.handleFolderSelection);
  const isFolderSelectedFn = useUIStore((s) => s.isFolderSelected);
  const isFolderSelectionSelected = isFolderSelectedFn(folder.id);
  const selectedVideoIds = useUIStore((s) => s.selectedVideoIds);
  const selectedFolderIds = useUIStore((s) => s.selectedFolderIds);

  // Long press for entering selection mode
  const longPress = useLongPress({
    onLongPress: () => enterSelectionMode(undefined, folder.id),
    disabled: selectionMode,
  });

  // Truncation detection for conditional tooltip
  const nameRef = useRef<HTMLSpanElement>(null);
  const isTruncated = useIsTruncated(nameRef);
  const [tooltipOpen, setTooltipOpen] = useState(false);

  // Local UI state
  const [isRenaming, setIsRenaming] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showSubfolderInput, setShowSubfolderInput] = useState(false);

  // Mutations
  const deleteFolder = useDeleteFolder();

  // Styling
  const textClasses = useSidebarTextClasses();
  const paddingLeft = SIDEBAR_LAYOUT.BASE_PADDING + level * SIDEBAR_LAYOUT.INDENT_PER_LEVEL;

  // Drag & Drop
  const isMultiDrag = selectionMode && isFolderSelectionSelected;
  const { setNodeRef, isOver, isDragging, attributes, listeners, dragStyle } =
    useFolderDragDrop({
      folderId: folder.id,
      folderName: folder.name,
      level: folder.level,
      disabled: selectionMode && !isFolderSelectionSelected,
      isMultiDrag,
      selectedVideoIds,
      selectedFolderIds,
    });

  // Computed values
  const isExpanded = expandedFolderIds.includes(folder.id);
  const isSelected = selectedFolderId === folder.id;
  const folderVideos = useMemo(() => videos.filter((v) => v.folderId === folder.id), [videos, folder.id]);
  const hasChildren = folder.children.length > 0 || folderVideos.length > 0;

  // Track expand transitions so stagger animation only plays on open, not on re-renders
  const wasExpandedRef = useRef(isExpanded);
  const justExpanded = isExpanded && !wasExpandedRef.current;
  if (wasExpandedRef.current !== isExpanded) wasExpandedRef.current = isExpanded;

  const originalFolder = useMemo(
    () => allFolders.find((f) => f.id === folder.id),
    [allFolders, folder.id]
  );

  // Supports Shift+Click for range selection, Ctrl/Cmd+Click for toggle selection
  const handleClick = (e: React.MouseEvent) => {
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      e.preventDefault();
      e.stopPropagation();
      handleFolderSelection(folder.id, e.shiftKey, e.ctrlKey || e.metaKey);
      return;
    }

    if (selectionMode) {
      e.preventDefault();
      e.stopPropagation();
      handleFolderSelection(folder.id, false, true);
      return;
    }

    setSelectedFolder(folder.id);
    setActiveSection("summarized");

    if (pathname !== "/board") {
      navigate("/board");
      if (!isExpanded && hasChildren) {
        toggleFolderExpansion(folder.id);
      }
    } else if (hasChildren) {
      toggleFolderExpansion(folder.id);
    }
  };

  const handleChevronClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    toggleFolderExpansion(folder.id);
  };

  const handleAddSubfolderClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowSubfolderInput((prev) => !prev);
    if (!showSubfolderInput && !isExpanded) {
      toggleFolderExpansion(folder.id);
    }
  };

  const handleDeleteConfirm = async (deleteContent: boolean) => {
    try {
      await deleteFolder.mutateAsync({ id: folder.id, deleteContent });
      if (selectedFolderId === folder.id) {
        setSelectedFolder(null);
      }
      setShowDeleteDialog(false);
    } catch {
      toast.error("Failed to delete folder");
    }
  };

  const ensureExpanded = useCallback(() => {
    if (!isExpanded) {
      toggleFolderExpansion(folder.id);
    }
  }, [isExpanded, toggleFolderExpansion, folder.id]);

  // Selected folder bg tint derived from folder color
  const selectedBgStyle: React.CSSProperties | undefined =
    (isSelected || isFolderSelectionSelected) && folder.color
      ? { backgroundColor: `oklch(from ${folder.color} l c h / 0.12)` }
      : undefined;

  const rowClassName = cn(
    "group flex items-center cursor-pointer rounded-sm",
    "transition-[background-color,box-shadow,transform] duration-150 ease-[var(--ease-out-expo)]",
    "hover:bg-[var(--glass-bg)] hover:shadow-[var(--glass-shadow)] hover:-translate-y-px",
    "motion-reduce:hover:translate-y-0 motion-reduce:transition-none",
    "has-[[data-state=open]]:bg-accent/50",
    textClasses.rowHeight,
    isSelected && !isFolderSelectionSelected && !folder.color && "bg-primary/8 font-medium ring-1 ring-primary/20",
    isSelected && !isFolderSelectionSelected && folder.color && "font-medium ring-1 ring-primary/20",
    isOver && !isDragging && "ring-1 ring-primary/50",
    isDragging && "opacity-50",
    isFolderSelectionSelected && !folder.color && "bg-primary/8 ring-1 ring-primary/20",
    isFolderSelectionSelected && folder.color && "ring-1 ring-primary/20",
  );

  const rowStyle = useMemo<React.CSSProperties>(
    () => ({ paddingInlineStart: `${paddingLeft}px`, paddingInlineEnd: "8px", ...selectedBgStyle }),
    [paddingLeft, selectedBgStyle],
  );

  const rowProps = {
    "data-sidebar-item": "folder" as const,
    className: rowClassName,
    style: rowStyle,
    onClick: handleClick,
    onPointerDown: longPress.onPointerDown,
    onPointerUp: longPress.onPointerUp,
    onPointerLeave: longPress.onPointerLeave,
    ...((!selectionMode || isFolderSelectionSelected) && attributes),
    ...((!selectionMode || isFolderSelectionSelected) && listeners),
  };

  // Shared left side content (checkbox/chevron + folder icon)
  const leftContent = (
    <>
      {selectionMode ? (
        <Checkbox
          checked={isFolderSelectionSelected}
          onCheckedChange={() => handleFolderSelection(folder.id, false, true)}
          className="w-4 h-4 shrink-0"
          onClick={(e) => e.stopPropagation()}
        />
      ) : hasChildren ? (
        <Button
          variant="ghost"
          size="icon-bare"
          onClick={handleChevronClick}
          className="w-4 h-4 shrink-0 rounded-sm"
        >
          <ChevronRight
            className={cn(
              textClasses.smallIconSize,
              "text-muted-foreground transition-transform",
              isExpanded && "rotate-90"
            )}
          />
        </Button>
      ) : (
        <span className="w-4 shrink-0" />
      )}
      <Folder
        className={cn(
          textClasses.iconSize,
          "shrink-0 text-primary ms-1 transition-[filter]",
          folder.color && (isSelected || isFolderSelectionSelected) && "drop-shadow-[0_0_6px_currentColor]"
        )}
        style={getFolderColorStyle(folder.color)}
      />
    </>
  );

  // Action buttons (add subfolder + context menu)
  const actionButtons = (
    <div className={cn(
      "flex items-center shrink-0",
      selectionMode && "invisible pointer-events-none"
    )}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon-bare"
            className="p-1.5 rounded-sm opacity-0 group-hover:opacity-100 group-has-[[data-state=open]]:opacity-100 hover:bg-accent transition-opacity shrink-0 focus-visible:opacity-100"
            onClick={handleAddSubfolderClick}
            aria-label="Create subfolder"
          >
            <Plus className={cn(
              textClasses.smallIconSize,
              "text-muted-foreground transition-transform duration-200",
              showSubfolderInput && "rotate-45"
            )} />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="text-xs">
          Create subfolder
        </TooltipContent>
      </Tooltip>
      {originalFolder && (
        <FolderContextMenu
          folder={originalFolder}
          allFolders={allFolders}
          onRename={() => setIsRenaming(true)}
          onDelete={() => setShowDeleteDialog(true)}
        />
      )}
    </div>
  );

  // Video count badge
  const countBadge = folderVideos.length > 0 ? (
    <span className={cn(
      "shrink-0 ms-1 px-1.5 py-0.5 rounded-full text-muted-foreground bg-muted/70 tabular-nums leading-none",
      textClasses.badgeText
    )}>
      {folderVideos.length}
    </span>
  ) : null;

  return (
    <div
      ref={setNodeRef}
      style={dragStyle}
      className={cn(isOver && !isDragging && "bg-primary/10 rounded-md")}
    >
      {/* Main folder row */}
      {isRenaming ? (
        <div {...rowProps}>
          {leftContent}
          <FolderRenameInput
            folderId={folder.id}
            currentName={folder.name}
            onComplete={() => setIsRenaming(false)}
          />
        </div>
      ) : (
        <TooltipProvider delayDuration={400}>
          <Tooltip open={isTruncated && tooltipOpen} onOpenChange={setTooltipOpen}>
            <TooltipTrigger asChild>
              <div {...rowProps}>
                {leftContent}
                <span ref={nameRef} className={cn("ms-2 truncate flex-1", textClasses.mainText)}>
                  {folder.name}
                </span>
                {actionButtons}
                {countBadge}
              </div>
            </TooltipTrigger>
            <TooltipContent side="right" align="start" sideOffset={8} className="max-w-xs z-tooltip">
              {folder.name}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      {/* Subfolder creation input — only mounted when active */}
      {showSubfolderInput && (
        <CreateSubfolderInput
          parentFolder={folder}
          paddingLeft={paddingLeft}
          indentPerLevel={SIDEBAR_LAYOUT.INDENT_PER_LEVEL}
          open={showSubfolderInput}
          onComplete={() => setShowSubfolderInput(false)}
          onExpand={ensureExpanded}
        />
      )}

      {/* Expanded children — stagger only on fresh expand, not re-renders.
          Depth-capped at MAX_FOLDER_DEPTH so a cyclic parent/child payload
          can't stack-overflow; a real tree should never approach that depth. */}
      {isExpanded && (hasChildren || showSubfolderInput) && level < MAX_FOLDER_DEPTH && (
        <div {...(justExpanded ? { 'data-sidebar-stagger': '' } : {})}>
          {folder.children.map((child, i) => (
            <div key={child.id} style={{ animationDelay: `${i * 40}ms` }}>
              <FolderItem
                folder={child}
                level={level + 1}
                videos={videos}
                allFolders={allFolders}
              />
            </div>
          ))}
          {folderVideos.map((video, i) => (
            <div key={video.id} style={{ animationDelay: `${(folder.children.length + i) * 40}ms` }}>
              <VideoItem
                video={video}
                level={level + 1}
                folders={allFolders}
              />
            </div>
          ))}
        </div>
      )}

      {/* Delete confirmation dialog */}
      <DeleteFolderDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        folderName={folder.name}
        hasContent={folderVideos.length > 0 || folder.children.length > 0}
        onConfirm={handleDeleteConfirm}
        isPending={deleteFolder.isPending}
      />
    </div>
  );
});
