import { describe, it, expect, beforeEach } from "vitest";
import { useUIStore } from "../ui-store";

describe("uiStore.resetUserScope", () => {
  beforeEach(() => {
    localStorage.clear();
    useUIStore.getState().resetUserScope();
  });

  it("clears every field that names the user's folders and videos", () => {
    const ui = useUIStore.getState();
    ui.expandFolder("f1");
    ui.expandFolder("f2");
    ui.toggleFavorite("v1");
    ui.setSelectedFolder("f2");
    ui.setItemOrder(["f_f1", "v_v1"]);
    ui.handleVideoSelection("v1", false, false);
    ui.setSidebarSearchQuery("rick");
    ui.setShowNewFolderInput(true);
    ui.openSidebarSearch();

    useUIStore.getState().resetUserScope();

    expect(useUIStore.getState()).toMatchObject({
      expandedFolderIds: [],
      favoriteVideoIds: [],
      selectedFolderId: null,
      itemOrder: [],
      selectionMode: false,
      selectedVideoIds: [],
      selectedFolderIds: [],
      lastClickedItemId: null,
      sidebarSearchQuery: "",
      showNewFolderInput: false,
      pendingSidebarSearch: false,
    });
  });

  it("keeps layout preferences", () => {
    const ui = useUIStore.getState();
    ui.setSidebarWidth(500);
    ui.setSidebarSortOption("created-desc");
    ui.setIconStripCollapsed(true);
    ui.setSidebarOpen(false);

    useUIStore.getState().resetUserScope();

    expect(useUIStore.getState()).toMatchObject({
      sidebarWidth: 500,
      sidebarSortOption: "created-desc",
      iconStripCollapsed: true,
      sidebarOpen: false,
    });
  });

  it("removes the folder and favourite ids from the persisted blob", () => {
    useUIStore.getState().expandFolder("f1");
    useUIStore.getState().toggleFavorite("v1");

    useUIStore.getState().resetUserScope();

    const persisted = JSON.parse(localStorage.getItem("vie-ui-store") ?? "{}");
    expect(persisted.state.expandedFolderIds).toEqual([]);
    expect(persisted.state.favoriteVideoIds).toEqual([]);
  });
});
