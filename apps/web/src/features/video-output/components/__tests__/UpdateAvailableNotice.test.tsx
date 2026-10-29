import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { http, HttpResponse, delay } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server } from "@/test/mocks/server";
import { createMockVideo } from "@/test/mocks/handlers";
import { renderWithProviders } from "@/test/test-utils";
import { setAccessToken } from "@/api/client";
import { usageQueryKey } from "@/hooks/use-usage";
import { queryKeys } from "@/lib/query-keys";
import { UpdateAvailableNotice } from "../UpdateAvailableNotice";

const API_URL = "http://localhost:3000/api";

const STALE_VIDEO = {
  id: "video-1",
  youtubeId: "abc123",
  folderId: "folder-9",
  status: "completed",
  updateAvailable: true,
};

type NoticeVideo = Parameters<typeof UpdateAvailableNotice>[0]["video"];

function renderNotice(video: Partial<NoticeVideo> = {}, language = "en", isRTL = false) {
  return renderWithProviders(
    <UpdateAvailableNotice video={{ ...STALE_VIDEO, ...video }} language={language} isRTL={isRTL} />,
  );
}

function captureUpdateRequests(): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.post(`${API_URL}/videos`, async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ video: createMockVideo({ id: "video-1" }), cached: false });
    }),
  );
  return bodies;
}

function refuseWith(status: number, body: Record<string, unknown>): void {
  server.use(http.post(`${API_URL}/videos`, () => HttpResponse.json(body, { status })));
}

describe("UpdateAvailableNotice", () => {
  beforeEach(() => {
    setAccessToken("test-token");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("visibility", () => {
    it("should offer the update on a completed video with a newer version", () => {
      renderNotice();

      expect(screen.getByRole("status")).toHaveTextContent("A newer version of this summary is available.");
      expect(screen.getByRole("button", { name: "Update to latest version" })).toBeEnabled();
    });

    it("should say the update counts as a new video that uses the free video or minutes", () => {
      renderNotice();

      expect(screen.getByRole("status")).toHaveTextContent(
        "Updating counts as a new video and uses your free video or minutes.",
      );
    });

    it("should render nothing when the video has no update available", () => {
      renderNotice({ updateAvailable: undefined });

      expect(screen.queryByTestId("update-available")).toBeNull();
    });

    it.each(["pending", "processing", "failed"])("should render nothing while the video is %s", (status) => {
      renderNotice({ status });

      expect(screen.queryByTestId("update-available")).toBeNull();
    });

    it("should not start an update until the button is clicked", async () => {
      const bodies = captureUpdateRequests();

      renderNotice();
      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(bodies).toEqual([]);
    });
  });

  describe("update", () => {
    it("should post updateToLatest with the watch url and folder when clicked", async () => {
      const bodies = captureUpdateRequests();
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      await waitFor(() => expect(bodies).toHaveLength(1));
      expect(bodies[0]).toEqual({
        url: "https://www.youtube.com/watch?v=abc123",
        folderId: "folder-9",
        updateToLatest: true,
      });
    });

    it("should disable the button while the request is in flight", async () => {
      server.use(
        http.post(`${API_URL}/videos`, async () => {
          await delay(100);
          return HttpResponse.json({ video: createMockVideo({ id: "video-1" }), cached: false });
        }),
      );
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      expect(screen.getByRole("button", { name: "Update to latest version" })).toBeDisabled();
    });

    it("should invalidate the video, the library list and the usage tile when it succeeds", async () => {
      captureUpdateRequests();
      const { queryClient } = renderNotice();
      const invalidate = vi.spyOn(queryClient, "invalidateQueries");

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(3));
      const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
      expect(keys).toEqual(
        expect.arrayContaining([queryKeys.videos.detail("video-1"), queryKeys.videos.lists(), usageQueryKey]),
      );
    });

    it("should keep the button disabled after the request succeeded so a second run cannot start", async () => {
      const bodies = captureUpdateRequests();
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));
      await waitFor(() => expect(bodies).toHaveLength(1));

      expect(screen.getByRole("button", { name: "Update to latest version" })).toBeDisabled();
    });
  });

  describe("refusals", () => {
    it("should show the minutes callout when the update needs more minutes (402)", async () => {
      refuseWith(402, { error: "INSUFFICIENT_MINUTES", message: "x", needed: 31, available: 12 });
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      const callout = await screen.findByTestId("minutes-callout");
      expect(callout).toHaveTextContent("This video needs 31 minutes and you have 12 minutes to spend.");
    });

    it("should leave the button usable after a refusal so the user can try again", async () => {
      refuseWith(402, { error: "INSUFFICIENT_MINUTES", message: "x", needed: 31, available: 12 });
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));
      await screen.findByTestId("minutes-callout");

      expect(screen.getByRole("button", { name: "Update to latest version" })).toBeEnabled();
    });

    it("should not invalidate anything when the update is refused", async () => {
      refuseWith(402, { error: "INSUFFICIENT_MINUTES", message: "x", needed: 31, available: 12 });
      const { queryClient } = renderNotice();
      const invalidate = vi.spyOn(queryClient, "invalidateQueries");

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));
      await screen.findByTestId("minutes-callout");

      expect(invalidate).not.toHaveBeenCalled();
    });

    it("should say how long to wait when the rate limit refuses it (429)", async () => {
      refuseWith(429, { error: "RATE_LIMITED", message: "Up to 20 videos per hour", retryAfterSec: 1200 });
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent("Try again in 20m.");
    });

    it("should say the demo account cannot update when the server refuses it (403)", async () => {
      refuseWith(403, { error: "DEMO_RESTRICTED", message: "This action is not available on the demo account" });
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      expect(await screen.findByTestId("update-error")).toHaveTextContent(
        "Updating isn't available on the demo account.",
      );
    });

    it("should show a general localised line for any other failure", async () => {
      refuseWith(500, { error: "INTERNAL_ERROR", message: "stack trace here" });
      renderNotice();

      await userEvent.click(screen.getByRole("button", { name: "Update to latest version" }));

      const error = await screen.findByTestId("update-error");
      expect(error).toHaveTextContent("Couldn't start the update. Try again in a moment.");
      expect(error).not.toHaveTextContent("stack trace here");
    });
  });

  describe("languages", () => {
    it.each([
      ["he", "קיימת גרסה חדשה יותר של הסיכום הזה.", "עדכון לגרסה האחרונה"],
      ["ar", "تتوفر نسخة أحدث من هذا الملخص.", "التحديث إلى أحدث نسخة"],
    ])("should read in %s", (language, title, action) => {
      renderNotice({}, language, true);

      const notice = screen.getByTestId("update-available");
      expect(notice).toHaveTextContent(title);
      expect(within(notice).getByRole("button", { name: action })).toBeInTheDocument();
    });

    it("should lay out right to left for a right-to-left video", () => {
      const { container } = renderNotice({}, "he", true);

      expect(container.querySelector('[dir="rtl"]')).not.toBeNull();
    });

    it("should show a billing refusal in the video's language", async () => {
      refuseWith(403, { error: "ACCOUNT_BLOCKED", message: "x" });
      renderNotice({}, "ar", true);

      await userEvent.click(screen.getByRole("button", { name: "التحديث إلى أحدث نسخة" }));

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent(
        "لا يمكن لهذا الحساب بدء فيديوهات جديدة الآن.",
      );
    });
  });
});
