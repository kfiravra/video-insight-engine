import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders, screen, userEvent, waitFor } from "../../test/test-utils";
import { server } from "../../test/mocks/server";
import { useAuthStore } from "../../stores/auth-store";
import { setAccessToken } from "../../api/client";
import { Navigate, useLocation } from "react-router-dom";
import { LoginPage } from "../LoginPage";

const API_URL = "http://localhost:3000/api";

function mockRegistrationStatus(status: { open: boolean; demoEnabled?: boolean }): void {
  server.use(http.get(`${API_URL}/auth/registration`, () => HttpResponse.json(status)));
}

const HANDED_OVER_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

/** Reaches the login page the way the landing page does: with a URL in router state. */
function LoginPageWithHandedOverUrl() {
  const location = useLocation();
  if (location.state === null) {
    return <Navigate to="/login" replace state={{ submitUrl: HANDED_OVER_URL }} />;
  }
  return <LoginPage />;
}

/** Router state of the current history entry (React Router stores it under `usr`). */
function routerState(): unknown {
  return (window.history.state as { usr?: unknown } | null)?.usr;
}

describe("LoginPage", () => {
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

  describe("Try the demo button", () => {
    it("should be hidden when the API does not report demo mode", async () => {
      mockRegistrationStatus({ open: true });

      renderWithProviders(<LoginPage />);

      // The signup link appears once the shared status request has landed.
      await screen.findByRole("link", { name: "Sign up" });
      expect(screen.queryByRole("button", { name: "Try the demo" })).not.toBeInTheDocument();
    });

    it("should be hidden when demo mode is disabled", async () => {
      mockRegistrationStatus({ open: true, demoEnabled: false });

      renderWithProviders(<LoginPage />);

      await screen.findByRole("link", { name: "Sign up" });
      expect(screen.queryByRole("button", { name: "Try the demo" })).not.toBeInTheDocument();
    });

    it("should be shown when demo mode is enabled", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });

      renderWithProviders(<LoginPage />);

      expect(await screen.findByRole("button", { name: "Try the demo" })).toBeInTheDocument();
    });

    it("should keep the regular sign-in button when demo mode is enabled", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });

      renderWithProviders(<LoginPage />);

      await screen.findByRole("button", { name: "Try the demo" });
      expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    });
  });

  describe("demo login", () => {
    it("should request a demo session without sending credentials", async () => {
      let capturedBody: unknown = null;
      mockRegistrationStatus({ open: false, demoEnabled: true });
      server.use(
        http.post(`${API_URL}/auth/login`, async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json({
            user: { id: "demo-user", email: "demo@example.com", name: "Demo" },
            accessToken: "demo-token",
          });
        }),
      );
      const user = userEvent.setup();
      renderWithProviders(<LoginPage />);

      await user.click(await screen.findByRole("button", { name: "Try the demo" }));

      await waitFor(() => expect(capturedBody).toEqual({ demo: true }));
    });

    it("should sign the visitor in as the demo user", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      const user = userEvent.setup();
      renderWithProviders(<LoginPage />);

      await user.click(await screen.findByRole("button", { name: "Try the demo" }));

      await waitFor(() => expect(useAuthStore.getState().isAuthenticated).toBe(true));
    });

    it("should open the board after a demo login", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      const user = userEvent.setup();
      renderWithProviders(<LoginPage />, { route: "/login" });

      await user.click(await screen.findByRole("button", { name: "Try the demo" }));

      await waitFor(() => expect(window.location.pathname).toBe("/board"));
    });

    it("should explain that the demo is unavailable when the demo login fails", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      server.use(
        http.post(`${API_URL}/auth/login`, () =>
          HttpResponse.json(
            { error: "INVALID_CREDENTIALS", message: "Invalid email or password", statusCode: 401 },
            { status: 401 },
          ),
        ),
      );
      const user = userEvent.setup();
      renderWithProviders(<LoginPage />);

      await user.click(await screen.findByRole("button", { name: "Try the demo" }));

      expect(
        await screen.findByText("The demo isn't available right now. Please try again later."),
      ).toBeInTheDocument();
    });
  });

  describe("URL handed over from the landing page", () => {
    it("should forward the URL to the generate page after a demo login", async () => {
      mockRegistrationStatus({ open: false, demoEnabled: true });
      const user = userEvent.setup();
      renderWithProviders(<LoginPageWithHandedOverUrl />, { route: "/login" });

      await user.click(await screen.findByRole("button", { name: "Try the demo" }));

      await waitFor(() => expect(window.location.pathname).toBe("/generate"));
      expect(routerState()).toEqual({ submitUrl: HANDED_OVER_URL });
    });

    it("should forward the URL to the generate page after a password login", async () => {
      mockRegistrationStatus({ open: false });
      const user = userEvent.setup();
      renderWithProviders(<LoginPageWithHandedOverUrl />, { route: "/login" });

      await user.type(await screen.findByLabelText(/email/i), "user@example.com");
      await user.type(screen.getByLabelText(/^password/i), "password123");
      await user.click(screen.getByRole("button", { name: "Sign in" }));

      await waitFor(() => expect(window.location.pathname).toBe("/generate"));
      expect(routerState()).toEqual({ submitUrl: HANDED_OVER_URL });
    });
  });
});
