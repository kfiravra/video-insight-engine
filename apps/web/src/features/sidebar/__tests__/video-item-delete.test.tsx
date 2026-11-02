import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { VideoItem } from "../videos/VideoItem";
import { createMockVideo } from "@/test/mocks/handlers";
import type { Video } from "@/types";

// The delete mutation resolves at once and reports success, like the server
// path would after the DELETE round-trip.
const deleteMutate = vi.hoisted(() =>
  vi.fn((_id: string, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()),
);
vi.mock("@/hooks/use-videos", () => ({
  useDeleteVideo: () => ({ mutate: deleteMutate, isPending: false }),
  useMoveVideo: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/features/sidebar/hooks/use-resummarize", () => ({
  useResummarize: () => ({ resummarize: vi.fn(), isPending: false }),
}));
// Dragging needs a DndContext the sidebar provides; the row under test does not.
vi.mock("@dnd-kit/core", async () => ({
  ...(await vi.importActual<typeof import("@dnd-kit/core")>("@dnd-kit/core")),
  useDraggable: () => ({ attributes: {}, listeners: {}, setNodeRef: () => {}, transform: null, isDragging: false }),
}));
// The real menu is a Radix dropdown; a plain button reaches the same onDelete.
vi.mock("@/features/sidebar/videos/VideoContextMenu", () => ({
  VideoContextMenu: ({ onDelete }: { onDelete: () => void }) => (
    <button type="button" onClick={onDelete}>
      Open delete
    </button>
  ),
}));

const video = createMockVideo({ id: "v1", title: "Open video", status: "completed" }) as unknown as Video;

function renderAt(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/video/:id" element={<VideoItem video={video} level={0} />} />
        <Route path="/board" element={<p>Library root</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function confirmDelete() {
  fireEvent.click(screen.getByRole("button", { name: "Open delete" }));
  const confirm = await screen.findByRole("button", { name: "Delete" });
  fireEvent.click(confirm);
}

describe("VideoItem delete navigation", () => {
  beforeEach(() => {
    deleteMutate.mockClear();
  });

  it("should navigate to the library root when the open video is deleted", async () => {
    renderAt("/video/v1");

    await confirmDelete();

    expect(deleteMutate).toHaveBeenCalledWith("v1", expect.anything());
    await waitFor(() => expect(screen.getByText("Library root")).toBeInTheDocument());
  });

  it("should stay on the current page when a different video is deleted", async () => {
    renderAt("/video/another");

    await confirmDelete();

    expect(deleteMutate).toHaveBeenCalledWith("v1", expect.anything());
    expect(screen.queryByText("Library root")).toBeNull();
    expect(screen.getByText("Open video")).toBeInTheDocument();
  });
});
