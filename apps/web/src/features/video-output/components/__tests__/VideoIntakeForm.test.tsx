import { StrictMode } from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { act, renderWithProviders, screen, userEvent, waitFor } from "@/test/test-utils";
import { server } from "@/test/mocks/server";
import { createMockVideo } from "@/test/mocks/handlers";
import { useAuthStore } from "@/stores/auth-store";
import { setAccessToken } from "@/api/client";
import { VideoIntakeForm } from "../VideoIntakeForm";

const API_URL = "http://localhost:3000/api";
const VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
const PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf";

function rejectWithDailyLimit(): void {
  server.use(
    http.post(`${API_URL}/videos`, () =>
      HttpResponse.json(
        {
          error: "DAILY_LIMIT_REACHED",
          message: "Daily limit reached",
          statusCode: 429,
          resetAt: "2026-10-06T00:00:00.000Z",
          limitUsd: 1,
        },
        { status: 429 },
      ),
    ),
  );
}

/** Records every submission so tests can assert how many were sent. */
function trackSubmissions(): { urls: string[] } {
  const tracker: { urls: string[] } = { urls: [] };
  server.use(
    http.post(`${API_URL}/videos`, async ({ request }) => {
      const body = (await request.json()) as { url: string };
      tracker.urls.push(body.url);
      return HttpResponse.json({ video: createMockVideo({ id: "video-42" }), cached: false });
    }),
  );
  return tracker;
}

describe("VideoIntakeForm", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: { id: "user-1", email: "test@example.com", name: "Test User" },
      accessToken: "test-access-token",
      isAuthenticated: true,
      isLoading: false,
    });
    setAccessToken("test-access-token");
  });

  describe("with a handed-over URL", () => {
    it("should prefill the input with the URL", () => {
      trackSubmissions();

      renderWithProviders(<VideoIntakeForm initialUrl={VIDEO_URL} />, { route: "/generate" });

      expect(screen.getByLabelText("YouTube URL")).toHaveValue(VIDEO_URL);
    });

    it("should submit the URL once without a click", async () => {
      const submissions = trackSubmissions();

      renderWithProviders(<VideoIntakeForm initialUrl={VIDEO_URL} />, { route: "/generate" });

      await waitFor(() => expect(window.location.pathname).toBe("/video/video-42"));
      expect(submissions.urls).toEqual([VIDEO_URL]);
    });

    it("should open the video page for the created video", async () => {
      trackSubmissions();

      renderWithProviders(<VideoIntakeForm initialUrl={VIDEO_URL} />, { route: "/generate" });

      await waitFor(() => expect(window.location.pathname).toBe("/video/video-42"));
    });

    it("should show the daily limit callout when the account is out of budget", async () => {
      rejectWithDailyLimit();

      renderWithProviders(<VideoIntakeForm initialUrl={VIDEO_URL} />, { route: "/generate" });

      expect(await screen.findByTestId("daily-limit-callout")).toBeInTheDocument();
    });
  });

  // StrictMode mounts effects twice in development; the hand-off must still
  // produce one submission whose result reaches the mounted form.
  describe("with a handed-over URL under StrictMode", () => {
    it("should submit the URL exactly once", async () => {
      const submissions = trackSubmissions();

      renderWithProviders(
        <StrictMode>
          <VideoIntakeForm initialUrl={VIDEO_URL} />
        </StrictMode>,
        { route: "/generate" },
      );

      await waitFor(() => expect(window.location.pathname).toBe("/video/video-42"));
      expect(submissions.urls).toEqual([VIDEO_URL]);
    });

    it("should unlock the input again after an out-of-budget response", async () => {
      rejectWithDailyLimit();

      renderWithProviders(
        <StrictMode>
          <VideoIntakeForm initialUrl={VIDEO_URL} />
        </StrictMode>,
        { route: "/generate" },
      );

      await screen.findByTestId("daily-limit-callout");
      expect(screen.getByLabelText("YouTube URL")).toBeEnabled();
    });
  });

  describe("mode detection", () => {
    it("should start in Video mode", () => {
      renderWithProviders(<VideoIntakeForm />, { route: "/generate" });

      expect(screen.getByRole("radio", { name: "Video" })).toBeChecked();
    });

    it("should switch to Playlist mode when a playlist URL is typed", async () => {
      const user = userEvent.setup();
      renderWithProviders(<VideoIntakeForm />, { route: "/generate" });

      await user.type(screen.getByLabelText("YouTube URL"), PLAYLIST_URL);

      expect(screen.getByRole("radio", { name: "Playlist" })).toBeChecked();
    });

    it("should switch back to Video mode when a video URL replaces it", async () => {
      const user = userEvent.setup();
      renderWithProviders(<VideoIntakeForm />, { route: "/generate" });
      const input = screen.getByLabelText("YouTube URL");
      await user.type(input, PLAYLIST_URL);

      await user.clear(input);
      await user.type(input, VIDEO_URL);

      expect(screen.getByRole("radio", { name: "Video" })).toBeChecked();
    });
  });

  describe("without a handed-over URL", () => {
    it("should wait for the user instead of submitting on mount", async () => {
      const submissions = trackSubmissions();

      renderWithProviders(<VideoIntakeForm />, { route: "/generate" });

      // A mount-time submit is deferred by one tick, so it would have fired by now.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(submissions.urls).toEqual([]);
    });
  });
});
