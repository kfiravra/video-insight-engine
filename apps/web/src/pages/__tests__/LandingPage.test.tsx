import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders, screen, userEvent, waitFor } from "../../test/test-utils";
import { server } from "../../test/mocks/server";
import { useAuthStore } from "../../stores/auth-store";
import { setAccessToken } from "../../api/client";
import { LandingPage } from "../LandingPage";

const API_URL = "http://localhost:3000/api";
const VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

function mockRegistrationStatus(status: { open: boolean; demoEnabled?: boolean }): void {
  server.use(http.get(`${API_URL}/auth/registration`, () => HttpResponse.json(status)));
}

/** Counts login requests so tests can assert whether a demo sign-in happened. */
function trackLoginRequests(): { bodies: unknown[] } {
  const tracker: { bodies: unknown[] } = { bodies: [] };
  server.use(
    http.post(`${API_URL}/auth/login`, async ({ request }) => {
      tracker.bodies.push(await request.json());
      return HttpResponse.json({
        user: { id: "demo-user", email: "demo@example.com", name: "Demo" },
        accessToken: "demo-token",
      });
    }),
  );
  return tracker;
}

async function submitUrl(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText("YouTube video URL"), VIDEO_URL);
  await user.click(screen.getByRole("button", { name: /summarize/i }));
}

/** Router state of the current history entry (React Router stores it under `usr`). */
function routerState(): unknown {
  return (window.history.state as { usr?: unknown } | null)?.usr;
}

describe("LandingPage", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: false,
      logoutReason: null,
    });
    localStorage.clear();
    setAccessToken(null);
  });

  describe("when demo mode is enabled and the visitor is logged out", () => {
    it("should sign in as the demo user when a URL is submitted", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      const logins = trackLoginRequests();
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByText("Free to try. No account needed.");

      await submitUrl(user);

      await waitFor(() => expect(logins.bodies).toEqual([{ demo: true }]));
    });

    it("should hand the URL to the generate page after the demo login", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      trackLoginRequests();
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByText("Free to try. No account needed.");

      await submitUrl(user);

      await waitFor(() => expect(window.location.pathname).toBe("/generate"));
      expect(routerState()).toEqual({ submitUrl: VIDEO_URL });
    });

    it("should not bounce to the board while the hand-off is in flight", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      trackLoginRequests();
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByText("Free to try. No account needed.");

      await submitUrl(user);

      await waitFor(() => expect(useAuthStore.getState().isAuthenticated).toBe(true));
      expect(window.location.pathname).toBe("/generate");
    });

    it("should stay on the landing page with a message when the demo login fails", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      server.use(
        http.post(`${API_URL}/auth/login`, () =>
          HttpResponse.json(
            { error: "DEMO_DISABLED", message: "The demo is not available", statusCode: 403 },
            { status: 403 },
          ),
        ),
      );
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByText("Free to try. No account needed.");

      await submitUrl(user);

      expect(
        await screen.findByText("The demo isn't available right now. Please try again later."),
      ).toBeInTheDocument();
      expect(window.location.pathname).toBe("/");
    });

    it("should run the sample video through the same demo flow", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      const logins = trackLoginRequests();
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByText("Free to try. No account needed.");

      await user.click(screen.getByRole("button", { name: "Try a sample video" }));

      await waitFor(() => expect(window.location.pathname).toBe("/generate"));
      expect(logins.bodies).toEqual([{ demo: true }]);
    });
  });

  describe("when the visitor already has a stored session", () => {
    it("should submit as that user without a demo login", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      const logins = trackLoginRequests();
      // A persisted token whose auth check has not finished yet.
      useAuthStore.setState({ accessToken: "existing-token", isLoading: true });
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByText("Free to try. No account needed.");

      await submitUrl(user);

      await waitFor(() => expect(window.location.pathname).toBe("/generate"));
      expect(logins.bodies).toEqual([]);
    });
  });

  describe("when demo mode is disabled", () => {
    it("should send a logged-out visitor to the login page as before", async () => {
      mockRegistrationStatus({ open: true, demoEnabled: false });
      const logins = trackLoginRequests();
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByRole("link", { name: "Sign up" });

      await submitUrl(user);

      await waitFor(() => expect(window.location.pathname).toBe("/login"));
      expect(logins.bodies).toEqual([]);
    });

    it("should carry the pasted URL to the login page", async () => {
      mockRegistrationStatus({ open: true, demoEnabled: false });
      const user = userEvent.setup();
      renderWithProviders(<LandingPage />);
      await screen.findByRole("link", { name: "Sign up" });

      await submitUrl(user);

      await waitFor(() => expect(window.location.pathname).toBe("/login"));
      expect(routerState()).toEqual({ submitUrl: VIDEO_URL });
    });

    it("should keep the sign-in wording in the closing call to action", async () => {
      mockRegistrationStatus({ open: true, demoEnabled: false });

      renderWithProviders(<LandingPage />);

      await screen.findByRole("link", { name: "Sign up" });
      expect(screen.getByText("Free to try. Sign in takes a keystroke.")).toBeInTheDocument();
    });
  });
});
