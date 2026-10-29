import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders, screen, userEvent, waitFor, within } from "@/test/test-utils";
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

/** An import result; `landed` videos made it in. With none, the server deleted the folder it made and sends null. */
function importResult(failedVideos: Array<Record<string, unknown>>, landed = 3) {
  return {
    folder: landed > 0 ? { id: "f1", name: "A playlist" } : null,
    videos: Array.from({ length: landed }, (_, i) => i).map((i) => ({
      id: `v${i}`,
      videoSummaryId: `s${i}`,
      youtubeId: `yt${i}`,
      title: `Video ${i}`,
      status: "processing",
      position: i,
    })),
    totalVideos: 5,
    cachedCount: 0,
    processingCount: landed,
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

    it("should say how long to wait when the burst limit sends retryAfterSec (429)", async () => {
      rejectSubmit(429, {
        error: "RATE_LIMITED",
        message: "Up to 20 videos per hour.",
        statusCode: 429,
        retryAfterSec: 3 * 3600,
      });

      await submitVideo();

      const notice = await screen.findByTestId("billing-notice");
      expect(notice).toHaveTextContent("Try again in 3h.");
      expect(notice).not.toHaveTextContent("Up to 20 videos per hour");
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

    it("should explain an import where every video was refused and no folder was left (folder null)", async () => {
      await previewPlaylist(() =>
        HttpResponse.json({
          ...importResult(
            [
              { youtubeId: "a", title: "First", position: 0, error: "INSUFFICIENT_MINUTES" },
              { youtubeId: "b", title: "Second", position: 1, error: "INSUFFICIENT_MINUTES" },
            ],
            0,
          ),
          billing: { neededMinutes: 90, availableMinutes: 0, freeVideoAvailable: false },
        }),
      );

      const summary = await screen.findByTestId("playlist-import-summary");

      expect(summary).toHaveTextContent("0 of 5 videos added");
      expect(summary).toHaveTextContent("The whole playlist needs 90 minutes; you have 0 minutes.");
      expect(within(summary).getAllByRole("listitem")).toHaveLength(2);
    });

    it("should keep the form usable after an import that left no folder", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(importResult([{ youtubeId: "a", title: "First", position: 0, error: "INSUFFICIENT_MINUTES" }], 0)),
      );
      await screen.findByTestId("playlist-import-summary");

      expect(screen.getByLabelText("YouTube URL")).toBeEnabled();
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

    it("should say how long to wait when the whole import is refused by the burst limit (429)", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(
          {
            error: "RATE_LIMITED",
            message: "Daily playlist import limit reached. Try again tomorrow.",
            statusCode: 429,
            retryAfterSec: 40 * 60,
          },
          { status: 429 },
        ),
      );

      const notice = await screen.findByTestId("billing-notice");
      expect(notice).toHaveTextContent("Try again in 40m.");
      expect(notice).not.toHaveTextContent("tomorrow");
    });

    it("should show the playlist quote and the unreadable reason for items the server could not size", async () => {
      await previewPlaylist(() =>
        HttpResponse.json({
          ...importResult([
            { youtubeId: "a", title: "Too big", position: 3, error: "INSUFFICIENT_MINUTES" },
            { youtubeId: "b", title: "No length", position: 4, error: "METADATA_UNAVAILABLE" },
          ]),
          billing: { neededMinutes: 340, availableMinutes: 120, freeVideoAvailable: false },
        }),
      );

      const summary = await screen.findByTestId("playlist-import-summary");

      expect(summary).toHaveTextContent("The whole playlist needs 340 minutes; you have 120 minutes.");
      expect(within(summary).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        "Too bigNot enough minutes",
        "No lengthCouldn't read this video",
      ]);
    });
  });
  describe("a refusal does not outlive the next attempt", () => {
    const INSUFFICIENT = { error: "INSUFFICIENT_MINUTES", message: "x", statusCode: 402, needed: 9, available: 1 };

    it("should clear the minutes callout when the user edits the URL", async () => {
      rejectSubmit(402, INSUFFICIENT);
      await submitVideo();
      await screen.findByTestId("minutes-callout");

      await userEvent.setup().type(screen.getByLabelText("YouTube URL"), "x");

      expect(screen.queryByTestId("minutes-callout")).toBeNull();
    });

    it("should show a newer plain error instead of the earlier refusal", async () => {
      rejectSubmit(402, INSUFFICIENT);
      await submitVideo();
      await screen.findByTestId("minutes-callout");
      rejectSubmit(500, { error: "INTERNAL_ERROR", message: "Something broke", statusCode: 500 });

      await userEvent.setup().click(screen.getByRole("button", { name: /summarize/i }));

      expect(await screen.findByText("Something broke")).toBeInTheDocument();
      expect(screen.queryByTestId("minutes-callout")).toBeNull();
    });

    it("should clear the refusal when the next submit is a playlist preview", async () => {
      rejectSubmit(402, INSUFFICIENT);
      await submitVideo();
      await screen.findByTestId("minutes-callout");
      server.use(http.post(`${API_URL}/playlists/preview`, () => HttpResponse.json(PREVIEW)));
      const user = userEvent.setup();

      await user.clear(screen.getByLabelText("YouTube URL"));
      await user.type(screen.getByLabelText("YouTube URL"), PLAYLIST_URL);
      await user.click(screen.getByRole("button", { name: /summarize/i }));

      expect(await screen.findByRole("button", { name: "Import Playlist" })).toBeInTheDocument();
      expect(screen.queryByTestId("minutes-callout")).toBeNull();
    });

    it("should clear a refusal of the playlist import when the import is tried again and works", async () => {
      let attempts = 0;
      await previewPlaylist(() => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ error: "RATE_LIMITED", message: "x", statusCode: 429, retryAfterSec: 600 }, { status: 429 })
          : HttpResponse.json(importResult([]));
      });
      await screen.findByTestId("billing-notice");

      await userEvent.setup().click(screen.getByRole("button", { name: "Import Playlist" }));

      await waitFor(() => expect(screen.queryByTestId("billing-notice")).toBeNull());
    });

    it("should show a plain import error instead of an earlier refusal", async () => {
      let attempts = 0;
      await previewPlaylist(() => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ error: "RATE_LIMITED", message: "x", statusCode: 429, retryAfterSec: 600 }, { status: 429 })
          : HttpResponse.json({ error: "INTERNAL_ERROR", message: "Import broke", statusCode: 500 }, { status: 500 });
      });
      await screen.findByTestId("billing-notice");

      await userEvent.setup().click(screen.getByRole("button", { name: "Import Playlist" }));

      expect(await screen.findByText("Import broke")).toBeInTheDocument();
      expect(screen.queryByTestId("billing-notice")).toBeNull();
    });
  });
});
