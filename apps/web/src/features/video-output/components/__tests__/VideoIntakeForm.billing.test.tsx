import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders, screen, userEvent, within } from "@/test/test-utils";
import { server } from "@/test/mocks/server";
import { useAuthStore } from "@/stores/auth-store";
import { setAccessToken } from "@/api/client";
import { VideoIntakeForm } from "../VideoIntakeForm";

const API_URL = "http://localhost:3000/api";
const VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
const PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf";

const PREVIEW = {
  playlist: {
    playlistId: "PL1",
    title: "A playlist",
    channel: "A channel",
    thumbnailUrl: null,
    totalVideos: 5,
    cachedCount: 0,
    videos: [],
  },
};

function importResult(failedVideos: Array<Record<string, unknown>>) {
  return {
    folder: { id: "f1", name: "A playlist" },
    videos: [0, 1, 2].map((i) => ({
      id: `v${i}`,
      videoSummaryId: `s${i}`,
      youtubeId: `yt${i}`,
      title: `Video ${i}`,
      status: "processing",
      position: i,
    })),
    totalVideos: 5,
    cachedCount: 0,
    processingCount: 3,
    failedCount: failedVideos.length,
    failedVideos,
  };
}

function rejectSubmit(status: number, body: Record<string, unknown>): void {
  server.use(http.post(`${API_URL}/videos`, () => HttpResponse.json(body, { status })));
}

async function submitVideo(): Promise<void> {
  const user = userEvent.setup();
  renderWithProviders(<VideoIntakeForm />, { route: "/generate" });
  await user.type(screen.getByLabelText("YouTube URL"), VIDEO_URL);
  await user.click(screen.getByRole("button", { name: /summarize/i }));
}

async function previewPlaylist(importBody: () => Response | Promise<Response>) {
  server.use(
    http.post(`${API_URL}/playlists/preview`, () => HttpResponse.json(PREVIEW)),
    http.post(`${API_URL}/playlists/import`, importBody),
  );
  const user = userEvent.setup();
  renderWithProviders(<VideoIntakeForm />, { route: "/generate" });
  await user.type(screen.getByLabelText("YouTube URL"), PLAYLIST_URL);
  await user.click(screen.getByRole("button", { name: /summarize/i }));
  await user.click(await screen.findByRole("button", { name: "Import Playlist" }));
}

describe("VideoIntakeForm billing refusals", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: { id: "user-1", email: "test@example.com", name: "Test User" },
      accessToken: "test-access-token",
      isAuthenticated: true,
      isLoading: false,
    });
    setAccessToken("test-access-token");
  });

  describe("single video", () => {
    it("should show how many minutes the video needs and the account has (402)", async () => {
      rejectSubmit(402, {
        error: "INSUFFICIENT_MINUTES",
        message: "x",
        statusCode: 402,
        needed: 31,
        available: 12,
      });

      await submitVideo();

      expect(await screen.findByTestId("minutes-callout")).toHaveTextContent(
        "This video needs 31 minutes and you have 12 minutes to spend.",
      );
    });

    it("should say when the next free video opens when the free video is used (402)", async () => {
      rejectSubmit(402, {
        error: "INSUFFICIENT_MINUTES",
        message: "x",
        statusCode: 402,
        needed: 20,
        available: 0,
        nextFreeAt: new Date(Date.now() + 7 * 3_600_000 + 30_000).toISOString(),
      });

      await submitVideo();

      const callout = await screen.findByTestId("minutes-callout");
      expect(callout).toHaveTextContent("You've used your free video.");
      expect(callout).toHaveTextContent("The next one opens in 7h");
    });

    it("should show the video length limit when the video is too long (422)", async () => {
      rejectSubmit(422, { error: "VIDEO_TOO_LONG_FOR_PLAN", message: "x", statusCode: 422, maxMinutes: 180 });

      await submitVideo();

      expect(await screen.findByTestId("minutes-callout")).toHaveTextContent("up to 180 minutes long");
    });

    it("should link the callout to the billing page", async () => {
      rejectSubmit(402, { error: "INSUFFICIENT_MINUTES", message: "x", statusCode: 402, needed: 3, available: 0 });

      await submitVideo();

      expect(await screen.findByRole("link", { name: "See your minutes" })).toHaveAttribute(
        "href",
        "/settings/billing",
      );
    });

    it("should say the account is blocked (403) without the minutes callout", async () => {
      rejectSubmit(403, { error: "ACCOUNT_BLOCKED", message: "x", statusCode: 403 });

      await submitVideo();

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent("can't start new videos");
      expect(screen.queryByTestId("minutes-callout")).toBeNull();
    });

    it("should say too many videos are running (429)", async () => {
      rejectSubmit(429, { error: "TOO_MANY_RUNS", message: "x", statusCode: 429, maxConcurrent: 1 });

      await submitVideo();

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent("You already have 1 video running.");
    });

    it("should show the server message for the burst limit (429)", async () => {
      rejectSubmit(429, { error: "RATE_LIMITED", message: "Up to 20 videos per hour.", statusCode: 429 });

      await submitVideo();

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent("Up to 20 videos per hour.");
    });

    it("should show the plain error for a failure that is not a billing refusal", async () => {
      rejectSubmit(500, { error: "INTERNAL_ERROR", message: "Something broke", statusCode: 500 });

      await submitVideo();

      expect(await screen.findByText("Something broke")).toBeInTheDocument();
      expect(screen.queryByTestId("minutes-callout")).toBeNull();
    });
  });

  describe("playlist import", () => {
    it("should list the videos an import left out and why", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(
          importResult([
            { youtubeId: "a", title: "Too big", position: 3, error: "INSUFFICIENT_MINUTES" },
            { youtubeId: "b", title: "Gone", position: 4, error: "Video not found" },
          ]),
        ),
      );

      const summary = await screen.findByTestId("playlist-import-summary");

      expect(summary).toHaveTextContent("3 of 5 videos added");
      expect(within(summary).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        "Too bigNot enough minutes",
        "GoneVideo not found",
      ]);
    });

    it("should link to the billing page when minutes stopped part of the import", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(
          importResult([{ youtubeId: "a", title: "Too big", position: 3, error: "INSUFFICIENT_MINUTES" }]),
        ),
      );

      expect(await screen.findByTestId("playlist-import-minutes-link")).toHaveAttribute(
        "href",
        "/settings/billing",
      );
    });

    it("should show no summary when every video was added", async () => {
      await previewPlaylist(() => HttpResponse.json(importResult([])));

      await screen.findByLabelText("YouTube URL");

      expect(screen.queryByTestId("playlist-import-summary")).toBeNull();
    });

    it("should dismiss the summary", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(importResult([{ youtubeId: "b", title: "Gone", position: 4, error: "Video not found" }])),
      );
      await screen.findByTestId("playlist-import-summary");

      await userEvent.setup().click(screen.getByRole("button", { name: "Dismiss" }));

      expect(screen.queryByTestId("playlist-import-summary")).toBeNull();
    });

    it("should show the burst limit message when the whole import is refused (429)", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(
          { error: "RATE_LIMITED", message: "Up to 20 videos per hour.", statusCode: 429 },
          { status: 429 },
        ),
      );

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent("Up to 20 videos per hour.");
    });
  });
});
