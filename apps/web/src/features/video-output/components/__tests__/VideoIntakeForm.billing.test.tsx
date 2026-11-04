import { describe, it, expect, beforeEach } from "vitest";
import { delay, http, HttpResponse } from "msw";
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
    it("should show the video's estimate and the balance (402)", async () => {
      rejectSubmit(402, {
        error: "INSUFFICIENT_TOKENS",
        message: "x",
        statusCode: 402,
        needed: 840,
        available: 300,
      });

      await submitVideo();

      expect(await screen.findByTestId("tokens-callout")).toHaveTextContent(
        "This video needs ≈ 840 tokens and you have 300 tokens.",
      );
    });

    it("should lead with the shortfall when the balance is below zero (402)", async () => {
      rejectSubmit(402, {
        error: "INSUFFICIENT_TOKENS",
        message: "x",
        statusCode: 402,
        needed: 840,
        available: -120,
      });

      await submitVideo();

      const callout = await screen.findByTestId("tokens-callout");
      expect(callout).toHaveTextContent("You're 120 tokens short — add tokens to continue.");
      expect(callout).toHaveTextContent("This video needs ≈ 840 tokens on top of that.");
    });

    it("should show the video length limit when the video is too long (422)", async () => {
      rejectSubmit(422, { error: "VIDEO_TOO_LONG", message: "x", statusCode: 422, maxMinutes: 180 });

      await submitVideo();

      expect(await screen.findByTestId("tokens-callout")).toHaveTextContent("up to 180 minutes long");
    });

    it("should link the callout to the billing page", async () => {
      rejectSubmit(402, { error: "INSUFFICIENT_TOKENS", message: "x", statusCode: 402, needed: 750, available: 0 });

      await submitVideo();

      expect(await screen.findByRole("link", { name: "Add tokens" })).toHaveAttribute(
        "href",
        "/settings/billing",
      );
    });

    it("should say the account is blocked (403) without the tokens callout", async () => {
      rejectSubmit(403, { error: "ACCOUNT_BLOCKED", message: "x", statusCode: 403 });

      await submitVideo();

      expect(await screen.findByTestId("billing-notice")).toHaveTextContent("can't start new videos");
      expect(screen.queryByTestId("tokens-callout")).toBeNull();
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
      expect(screen.queryByTestId("tokens-callout")).toBeNull();
    });
  });

  describe("playlist import", () => {
    it("should list the videos an import left out and why", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(
          importResult([
            { youtubeId: "a", title: "Too big", position: 3, error: "INSUFFICIENT_TOKENS" },
            { youtubeId: "b", title: "Gone", position: 4, error: "Video not found" },
          ]),
        ),
      );

      const summary = await screen.findByTestId("playlist-import-summary");

      expect(summary).toHaveTextContent("3 of 5 videos added");
      expect(within(summary).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        "Too bigNot enough tokens",
        "GoneVideo not found",
      ]);
    });

    it("should explain an import where every video was refused and no folder was left (folder null)", async () => {
      await previewPlaylist(() =>
        HttpResponse.json({
          ...importResult(
            [
              { youtubeId: "a", title: "First", position: 0, error: "INSUFFICIENT_TOKENS" },
              { youtubeId: "b", title: "Second", position: 1, error: "INSUFFICIENT_TOKENS" },
            ],
            0,
          ),
          billing: { neededTokens: 2_400, availableTokens: 0 },
        }),
      );

      const summary = await screen.findByTestId("playlist-import-summary");

      expect(summary).toHaveTextContent("0 of 5 videos added");
      expect(summary).toHaveTextContent("The whole playlist needs ≈ 2,400 tokens; you have 0 tokens.");
      expect(within(summary).getAllByRole("listitem")).toHaveLength(2);
    });

    it("should keep the form usable after an import that left no folder", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(importResult([{ youtubeId: "a", title: "First", position: 0, error: "INSUFFICIENT_TOKENS" }], 0)),
      );
      await screen.findByTestId("playlist-import-summary");

      expect(screen.getByLabelText("YouTube URL")).toBeEnabled();
    });

    it("should link to the billing page when tokens stopped part of the import", async () => {
      await previewPlaylist(() =>
        HttpResponse.json(
          importResult([{ youtubeId: "a", title: "Too big", position: 3, error: "INSUFFICIENT_TOKENS" }]),
        ),
      );

      expect(await screen.findByTestId("playlist-import-tokens-link")).toHaveAttribute(
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
            { youtubeId: "a", title: "Too big", position: 3, error: "INSUFFICIENT_TOKENS" },
            { youtubeId: "b", title: "No length", position: 4, error: "METADATA_UNAVAILABLE" },
          ]),
          billing: { neededTokens: 3_400, availableTokens: 1_200 },
        }),
      );

      const summary = await screen.findByTestId("playlist-import-summary");

      expect(summary).toHaveTextContent("The whole playlist needs ≈ 3,400 tokens; you have 1,200 tokens.");
      expect(within(summary).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        "Too bigNot enough tokens",
        "No lengthCouldn't read this video",
      ]);
    });
  });
  describe("estimate before submit", () => {
    function estimateReturns(body: () => Response | Promise<Response>): { urls: string[] } {
      const seen: { urls: string[] } = { urls: [] };
      server.use(
        http.get(`${API_URL}/videos/estimate`, ({ request }) => {
          seen.urls.push(new URL(request.url).searchParams.get("url") ?? "");
          return body();
        }),
      );
      return seen;
    }

    async function pasteVideoUrl(url = VIDEO_URL): Promise<void> {
      renderWithProviders(<VideoIntakeForm />, { route: "/generate" });
      await userEvent.setup().type(screen.getByLabelText("YouTube URL"), url);
    }

    it("should show what the video would hold once the pasted URL settles", async () => {
      const seen = estimateReturns(() =>
        HttpResponse.json({ youtubeId: "dQw4w9WgXcQ", durationSec: 600, tokens: 840, free: false }),
      );

      await pasteVideoUrl();

      expect(await screen.findByTestId("estimate-hint")).toHaveTextContent("≈ 840 tokens");
      expect(seen.urls).toEqual([VIDEO_URL]);
    });

    it("should say a video that is served free costs nothing", async () => {
      estimateReturns(() => HttpResponse.json({ youtubeId: "dQw4w9WgXcQ", durationSec: 600, tokens: 0, free: true }));

      await pasteVideoUrl();

      expect(await screen.findByTestId("estimate-hint")).toHaveTextContent("Already summarized — free");
    });

    it("should stay silent when the estimate is rate limited (429)", async () => {
      const seen = estimateReturns(() =>
        HttpResponse.json({ error: "RATE_LIMITED", message: "x", statusCode: 429, retryAfterSec: 60 }, { status: 429 }),
      );

      await pasteVideoUrl();
      await waitFor(() => expect(seen.urls).toHaveLength(1));
      await new Promise((resolve) => setTimeout(resolve, 100));

      expect([screen.queryByTestId("estimate-hint"), screen.queryByRole("alert")]).toEqual([null, null]);
    });

    it("should not estimate a playlist link", async () => {
      const seen = estimateReturns(() =>
        HttpResponse.json({ youtubeId: "x", durationSec: 60, tokens: 750, free: false }),
      );

      await pasteVideoUrl(PLAYLIST_URL);
      await new Promise((resolve) => setTimeout(resolve, 600));

      expect([seen.urls, screen.queryByTestId("estimate-hint")]).toEqual([[], null]);
    });

    it("should never hold up a submit while the estimate is still loading", async () => {
      const seen = estimateReturns(async () => {
        await delay("infinite");
        return HttpResponse.json({});
      });
      rejectSubmit(402, { error: "INSUFFICIENT_TOKENS", message: "x", statusCode: 402, needed: 840, available: 0 });
      await pasteVideoUrl();
      await waitFor(() => expect(seen.urls).toHaveLength(1));

      await userEvent.setup().click(screen.getByRole("button", { name: /summarize/i }));

      expect(await screen.findByTestId("tokens-callout")).toBeInTheDocument();
    });

    it("should step aside while a refusal explains the cost", async () => {
      estimateReturns(() => HttpResponse.json({ youtubeId: "dQw4w9WgXcQ", durationSec: 600, tokens: 840, free: false }));
      rejectSubmit(402, { error: "INSUFFICIENT_TOKENS", message: "x", statusCode: 402, needed: 840, available: 0 });
      await pasteVideoUrl();
      await screen.findByTestId("estimate-hint");

      await userEvent.setup().click(screen.getByRole("button", { name: /summarize/i }));

      await screen.findByTestId("tokens-callout");
      expect(screen.queryByTestId("estimate-hint")).toBeNull();
    });
  });

  describe("a refusal does not outlive the next attempt", () => {
    const INSUFFICIENT = { error: "INSUFFICIENT_TOKENS", message: "x", statusCode: 402, needed: 840, available: 10 };

    it("should clear the tokens callout when the user edits the URL", async () => {
      rejectSubmit(402, INSUFFICIENT);
      await submitVideo();
      await screen.findByTestId("tokens-callout");

      await userEvent.setup().type(screen.getByLabelText("YouTube URL"), "x");

      expect(screen.queryByTestId("tokens-callout")).toBeNull();
    });

    it("should show a newer plain error instead of the earlier refusal", async () => {
      rejectSubmit(402, INSUFFICIENT);
      await submitVideo();
      await screen.findByTestId("tokens-callout");
      rejectSubmit(500, { error: "INTERNAL_ERROR", message: "Something broke", statusCode: 500 });

      await userEvent.setup().click(screen.getByRole("button", { name: /summarize/i }));

      expect(await screen.findByText("Something broke")).toBeInTheDocument();
      expect(screen.queryByTestId("tokens-callout")).toBeNull();
    });

    it("should clear the refusal when the next submit is a playlist preview", async () => {
      rejectSubmit(402, INSUFFICIENT);
      await submitVideo();
      await screen.findByTestId("tokens-callout");
      server.use(http.post(`${API_URL}/playlists/preview`, () => HttpResponse.json(PREVIEW)));
      const user = userEvent.setup();

      await user.clear(screen.getByLabelText("YouTube URL"));
      await user.type(screen.getByLabelText("YouTube URL"), PLAYLIST_URL);
      await user.click(screen.getByRole("button", { name: /summarize/i }));

      expect(await screen.findByRole("button", { name: "Import Playlist" })).toBeInTheDocument();
      expect(screen.queryByTestId("tokens-callout")).toBeNull();
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
