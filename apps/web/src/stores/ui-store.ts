import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Compute range selection between anchor and target in item order.
 * Returns video and folder IDs in the selected range.
 */
function computeRangeSelection(
  itemOrder: string[],
  anchorId: string,
  targetId: string,
  preserveExisting: boolean,
  existingVideoIds: string[],
  existingFolderIds: string[]
): { videoIds: string[]; folderIds: string[] } | null {
  const anchorIndex = itemOrder.indexOf(anchorId);
  const targetIndex = itemOrder.indexOf(targetId);

  if (anchorIndex === -1 || targetIndex === -1) {
    return null;
  }

  const startIndex = Math.min(anchorIndex, targetIndex);
  const endIndex = Math.max(anchorIndex, targetIndex);

  const rangeItems = itemOrder.slice(startIndex, endIndex + 1);

  const rangeVideoIds: string[] = [];
  const rangeFolderIds: string[] = [];

  for (const itemId of rangeItems) {
    if (itemId.startsWith("v_")) {
      rangeVideoIds.push(itemId.slice(2));
    } else if (itemId.startsWith("f_")) {
      rangeFolderIds.push(itemId.slice(2));
    }
  }

  if (preserveExisting) {
    // Merge with existing selection
    const videoSet = new Set([...existingVideoIds, ...rangeVideoIds]);
    const folderSet = new Set([...existingFolderIds, ...rangeFolderIds]);
    return {
      videoIds: Array.from(videoSet),
      folderIds: Array.from(folderSet),
    };
  }

  return {
    videoIds: rangeVideoIds,
    folderIds: rangeFolderIds,
  };
}

export type ActiveSection = "summarized" | "assistant";

export type RightPanelId = "none" | "minimap" | "chapters" | "chat";

export type SidebarTextSize = "small" | "medium" | "large";
export type SortOption = "name-asc" | "name-desc" | "created-asc" | "created-desc";

interface UIState {
  // Sidebar visibility
  sidebarOpen: boolean;
  activeRightPanel: RightPanelId;
  isRightPanelMinimized: boolean;
  sidebarWidth: number;

  // Icon strip (desktop-only nav rail) — labels visible by default;
  // user can opt into icon-only (Arc-style) via a chevron toggle.
  iconStripCollapsed: boolean;

  // Keyboard shortcuts modal — opened from menu, hotkey, or CommandPalette.
  shortcutsModalOpen: boolean;

  // Content context
  activeSection: ActiveSection;
  selectedFolderId: string | null;

  // Folder expansion state (persisted as array, used as Set in memory)
  expandedFolderIds: string[];

  // Sidebar text size preference
  sidebarTextSize: SidebarTextSize;

  // Sort preference (persisted)
  sidebarSortOption: SortOption;

  // Search query (not persisted, cleared on reload)
  sidebarSearchQuery: string;

  // Signal to open the sidebar search panel after sidebar opens (consumed once)
  pendingSidebarSearch: boolean;

  // New folder input visibility (triggered from SidebarTabs, consumed by SidebarSection)
  showNewFolderInput: boolean;

  // Selection mode state (for bulk operations)
  selectionMode: boolean;
  selectedVideoIds: string[];
  selectedFolderIds: string[];

  // Favorites — local-only scaffold (NO backend wiring yet).
  // Persisted via zustand `partialize` so a star survives reloads.
  // Tracked in dev/active/ROADMAP.md (Priority 4 — "Favorites server sync");
  // hoist to server-owned `isFavorite` once the endpoint lands.
  favoriteVideoIds: string[];

  // Range selection state
  lastClickedItemId: string | null;  // Anchor for Shift+Click
  itemOrder: string[];  // Flat list of item IDs in display order (prefix: v_ for videos, f_ for folders)

  // Actions
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setActiveRightPanel: (panel: RightPanelId) => void;
  toggleRightPanel: (panel: RightPanelId) => void;
  toggleRightPanelMinimized: () => void;
  expandRightPanelToTab: (panel: RightPanelId) => void;
  setSidebarWidth: (width: number) => void;
  setIconStripCollapsed: (collapsed: boolean) => void;
  toggleIconStripCollapsed: () => void;
  openShortcutsModal: () => void;
  closeShortcutsModal: () => void;
  toggleShortcutsModal: () => void;
  setActiveSection: (section: ActiveSection) => void;
  setSelectedFolder: (id: string | null) => void;
  toggleFolderExpansion: (folderId: string) => void;
  expandFolder: (folderId: string) => void;
  collapseFolder: (folderId: string) => void;
  collapseAllFolders: () => void;
  isFolderExpanded: (folderId: string) => boolean;
  setShowNewFolderInput: (show: boolean) => void;
  setSidebarTextSize: (size: SidebarTextSize) => void;
  setSidebarSortOption: (option: SortOption) => void;
  setSidebarSearchQuery: (query: string) => void;
  clearSidebarSearch: () => void;
  openSidebarSearch: () => void;
  consumePendingSidebarSearch: () => void;

  // Selection mode actions
  enterSelectionMode: (initialVideoId?: string, initialFolderId?: string) => void;
  exitSelectionMode: () => void;
  toggleVideoSelection: (videoId: string) => void;
  toggleFolderSelection: (folderId: string) => void;
  clearSelection: () => void;
  isVideoSelected: (videoId: string) => boolean;
  isFolderSelected: (folderId: string) => boolean;

  // Favorite actions (local-only — see TODO above)
  toggleFavorite: (videoId: string) => void;
  isFavorite: (videoId: string) => boolean;

  // Range selection actions
  setItemOrder: (items: string[]) => void;
  handleVideoSelection: (videoId: string, shiftKey: boolean, ctrlKey: boolean) => void;
  handleFolderSelection: (folderId: string, shiftKey: boolean, ctrlKey: boolean) => void;

  /** Forget everything that names the signed-in user's folders and videos
   *  (sign-out). Layout preferences stay. */
  resetUserScope: () => void;
}

/** The slice of the UI store that refers to one user's library. */
const USER_SCOPE_INITIAL = {
  selectedFolderId: null,
  expandedFolderIds: [],
  favoriteVideoIds: [],
  selectionMode: false,
  selectedVideoIds: [],
  selectedFolderIds: [],
  lastClickedItemId: null,
  itemOrder: [],
  sidebarSearchQuery: "",
  pendingSidebarSearch: false,
  showNewFolderInput: false,
} satisfies Partial<UIState>;

/**
 * Build selection updates for both videos and folders.
 * Returns a partial UIState for shift+click (range), ctrl+click (toggle), and normal click (single).
 */
function buildSelectionUpdate(
  state: UIState,
  id: string,
  type: "video" | "folder",
  shiftKey: boolean,
  ctrlKey: boolean,
): Partial<UIState> {
  const isVideo = type === "video";
  const itemId = `${isVideo ? "v_" : "f_"}${id}`;
  const ownIds = isVideo ? state.selectedVideoIds : state.selectedFolderIds;

  // If not in selection mode, enter it
  if (!state.selectionMode) {
    return {
      selectionMode: true,
      selectedVideoIds: isVideo ? [id] : [],
      selectedFolderIds: isVideo ? [] : [id],
      lastClickedItemId: itemId,
    };
  }

  // Shift+Click: range selection
  if (shiftKey && state.lastClickedItemId && state.itemOrder.length > 0) {
    const result = computeRangeSelection(
      state.itemOrder,
      state.lastClickedItemId,
      itemId,
      ctrlKey,
      state.selectedVideoIds,
      state.selectedFolderIds,
    );
    if (result) {
      return {
        selectedVideoIds: result.videoIds,
        selectedFolderIds: result.folderIds,
      };
    }
  }

  // Ctrl+Click: toggle selection
  if (ctrlKey) {
    const toggled = ownIds.includes(id)
      ? ownIds.filter((x) => x !== id)
      : [...ownIds, id];
    return {
      ...(isVideo
        ? { selectedVideoIds: toggled }
        : { selectedFolderIds: toggled }),
      lastClickedItemId: itemId,
    };
  }

  // Normal click: single selection
  return {
    selectedVideoIds: isVideo ? [id] : [],
    selectedFolderIds: isVideo ? [] : [id],
    lastClickedItemId: itemId,
  };
}

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      // Initial state
      sidebarOpen: true,
      activeRightPanel: "chapters" as RightPanelId,
      isRightPanelMinimized: false,
      sidebarWidth: 360,
      iconStripCollapsed: false,
      shortcutsModalOpen: false,
      activeSection: "summarized",
      selectedFolderId: null,
      expandedFolderIds: [],
      sidebarTextSize: "medium",
      sidebarSortOption: "name-asc",
      sidebarSearchQuery: "",
      pendingSidebarSearch: false,
      showNewFolderInput: false,

      // Selection mode state
      selectionMode: false,
      selectedVideoIds: [],
      selectedFolderIds: [],

      // Favorites (local-only)
      favoriteVideoIds: [],

      // Range selection state
      lastClickedItemId: null,
      itemOrder: [],

      // Actions
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      setActiveRightPanel: (panel) => set({ activeRightPanel: panel }),
      toggleRightPanel: (panel) => set((s) => ({
        activeRightPanel: s.activeRightPanel === panel ? "none" : panel,
      })),
      toggleRightPanelMinimized: () => set((s) => ({
        isRightPanelMinimized: !s.isRightPanelMinimized,
      })),
      expandRightPanelToTab: (panel) => set({
        activeRightPanel: panel,
        isRightPanelMinimized: false,
      }),
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      setIconStripCollapsed: (collapsed) => set({ iconStripCollapsed: collapsed }),
      toggleIconStripCollapsed: () =>
        set((s) => ({ iconStripCollapsed: !s.iconStripCollapsed })),
      openShortcutsModal: () => set({ shortcutsModalOpen: true }),
      closeShortcutsModal: () => set({ shortcutsModalOpen: false }),
      toggleShortcutsModal: () =>
        set((s) => ({ shortcutsModalOpen: !s.shortcutsModalOpen })),
      setActiveSection: (section) => set({ activeSection: section, showNewFolderInput: false }),
      setSelectedFolder: (id) => set({ selectedFolderId: id }),
      toggleFolderExpansion: (folderId) =>
        set((s) => {
          const ids = s.expandedFolderIds;
          if (ids.includes(folderId)) {
            return { expandedFolderIds: ids.filter((id) => id !== folderId) };
          } else {
            return { expandedFolderIds: [...ids, folderId] };
          }
        }),
      expandFolder: (folderId) =>
        set((s) => {
          if (s.expandedFolderIds.includes(folderId)) {
            return s;
          }
          return { expandedFolderIds: [...s.expandedFolderIds, folderId] };
        }),
      collapseFolder: (folderId) =>
        set((s) => ({
          expandedFolderIds: s.expandedFolderIds.filter((id) => id !== folderId),
        })),
      collapseAllFolders: () => set({ expandedFolderIds: [] }),
      isFolderExpanded: (folderId) => get().expandedFolderIds.includes(folderId),
      setShowNewFolderInput: (show) => set({ showNewFolderInput: show }),
      setSidebarTextSize: (size) => set({ sidebarTextSize: size }),
      setSidebarSortOption: (option) => set({ sidebarSortOption: option }),
      setSidebarSearchQuery: (query) => set({ sidebarSearchQuery: query }),
      clearSidebarSearch: () => set({ sidebarSearchQuery: "" }),
      openSidebarSearch: () => set({ sidebarOpen: true, activeSection: "summarized", pendingSidebarSearch: true }),
      consumePendingSidebarSearch: () => set({ pendingSidebarSearch: false }),

      // Selection mode actions
      enterSelectionMode: (initialVideoId, initialFolderId) =>
        set({
          selectionMode: true,
          selectedVideoIds: initialVideoId ? [initialVideoId] : [],
          selectedFolderIds: initialFolderId ? [initialFolderId] : [],
        }),
      exitSelectionMode: () =>
        set({
          selectionMode: false,
          selectedVideoIds: [],
          selectedFolderIds: [],
        }),
      toggleVideoSelection: (videoId) =>
        set((s) => {
          const ids = s.selectedVideoIds;
          if (ids.includes(videoId)) {
            return { selectedVideoIds: ids.filter((id) => id !== videoId) };
          } else {
            return { selectedVideoIds: [...ids, videoId] };
          }
        }),
      toggleFolderSelection: (folderId) =>
        set((s) => {
          const ids = s.selectedFolderIds;
          if (ids.includes(folderId)) {
            return { selectedFolderIds: ids.filter((id) => id !== folderId) };
          } else {
            return { selectedFolderIds: [...ids, folderId] };
          }
        }),
      clearSelection: () =>
        set({ selectedVideoIds: [], selectedFolderIds: [] }),
      isVideoSelected: (videoId) => get().selectedVideoIds.includes(videoId),
      isFolderSelected: (folderId) => get().selectedFolderIds.includes(folderId),

      // Favorite actions — optimistic local toggle. No network call;
      // backend wiring tracked in dev/active/ROADMAP.md (Priority 4).
      toggleFavorite: (videoId) =>
        set((s) => {
          const ids = s.favoriteVideoIds;
          if (ids.includes(videoId)) {
            return { favoriteVideoIds: ids.filter((id) => id !== videoId) };
          }
          return { favoriteVideoIds: [...ids, videoId] };
        }),
      isFavorite: (videoId) => get().favoriteVideoIds.includes(videoId),

      // Range selection actions
      setItemOrder: (items) => set({ itemOrder: items }),

      handleVideoSelection: (videoId, shiftKey, ctrlKey) => {
        set(buildSelectionUpdate(get(), videoId, "video", shiftKey, ctrlKey));
      },

      handleFolderSelection: (folderId, shiftKey, ctrlKey) => {
        set(buildSelectionUpdate(get(), folderId, "folder", shiftKey, ctrlKey));
      },

      resetUserScope: () => set({ ...USER_SCOPE_INITIAL }),
    }),
    {
      name: "vie-ui-store",
      partialize: (state) => ({
        sidebarOpen: state.sidebarOpen,
        activeRightPanel: state.activeRightPanel,
        isRightPanelMinimized: state.isRightPanelMinimized,
        sidebarWidth: state.sidebarWidth,
        iconStripCollapsed: state.iconStripCollapsed,
        activeSection: state.activeSection,
        expandedFolderIds: state.expandedFolderIds,
        sidebarTextSize: state.sidebarTextSize,
        sidebarSortOption: state.sidebarSortOption,
        favoriteVideoIds: state.favoriteVideoIds,
      }),
    }
  )
);

// Selectors
export const useSidebarOpen = () => useUIStore((s) => s.sidebarOpen);
export const useActiveRightPanel = () => useUIStore((s) => s.activeRightPanel);
export const useIsRightPanelOpen = () => useUIStore((s) => s.activeRightPanel !== "none");
export const useIsRightPanelMinimized = () => useUIStore((s) => s.isRightPanelMinimized);
export const useSelectedFolder = () => useUIStore((s) => s.selectedFolderId);
export const useActiveSection = () => useUIStore((s) => s.activeSection);
export const useIconStripCollapsed = () => useUIStore((s) => s.iconStripCollapsed);
export const useShortcutsModalOpen = () => useUIStore((s) => s.shortcutsModalOpen);
export const useSidebarTextSize = () => useUIStore((s) => s.sidebarTextSize);
export const useSidebarSortOption = () => useUIStore((s) => s.sidebarSortOption);
export const useSidebarSearchQuery = () => useUIStore((s) => s.sidebarSearchQuery);
// Selection mode selectors
export const useSelectionMode = () => useUIStore((s) => s.selectionMode);
export const useSelectedVideoIds = () => useUIStore((s) => s.selectedVideoIds);
export const useSelectedFolderIds = () => useUIStore((s) => s.selectedFolderIds);
export const useSelectionCount = () =>
  useUIStore((s) => s.selectedVideoIds.length + s.selectedFolderIds.length);
// Favorite selectors
export const useFavoriteVideoIds = () => useUIStore((s) => s.favoriteVideoIds);
export const useIsFavorite = (videoId: string) =>
  useUIStore((s) => s.favoriteVideoIds.includes(videoId));
