import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { SelectionToolbar } from "../core/SelectionToolbar";
import { useUIStore } from "@/stores/ui-store";

const bulkDeleteVideos = vi.hoisted(() => vi.fn<(ids: string[]) => Promise<void>>(async () => {}));
vi.mock("@/hooks/use-videos", () => ({
  useBulkDeleteVideos: () => ({ mutateAsync: bulkDeleteVideos, isPending: false }),
  useBulkMoveVideos: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/use-folders", () => ({
  useBulkDeleteFolders: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBulkMoveFolders: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useFolders: () => ({ data: { folders: [] } }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderAt(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/video/:id" element={<SelectionToolbar />} />
        <Route path="/board" element={<p>Library root</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function confirmBulkDelete() {
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  const confirm = await screen.findByRole("button", { name: /^delete$/i, hidden: false });
  fireEvent.click(confirm);
}

describe("SelectionToolbar bulk delete navigation", () => {
  beforeEach(() => {
    bulkDeleteVideos.mockClear();
    useUIStore.getState().exitSelectionMode();
  });

  it("should navigate to the library root when the open video is among the deleted", async () => {
    useUIStore.getState().enterSelectionMode("v1");
    useUIStore.getState().toggleVideoSelection("v2");
    renderAt("/video/v1");

    await confirmBulkDelete();

    await waitFor(() => expect(bulkDeleteVideos).toHaveBeenCalledWith(["v1", "v2"]));
    await waitFor(() => expect(screen.getByText("Library root")).toBeInTheDocument());
    expect(useUIStore.getState().selectionMode).toBe(false);
  });

  it("should stay put when the open video is not part of the selection", async () => {
    useUIStore.getState().enterSelectionMode("v2");
    renderAt("/video/v1");

    await confirmBulkDelete();

    await waitFor(() => expect(bulkDeleteVideos).toHaveBeenCalledWith(["v2"]));
    expect(screen.queryByText("Library root")).toBeNull();
  });
});
