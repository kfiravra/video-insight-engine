import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders, screen, waitFor } from "../../../test/test-utils";
import { queryKeys } from "@/lib/query-keys";
import { server } from "../../../test/mocks/server";
import { LandingHeader } from "../LandingHeader";

const API_URL = "http://localhost:3000/api";

describe("LandingHeader", () => {
  it("should show the Sign up link when registration is open", async () => {
    renderWithProviders(<LandingHeader />);

    expect(await screen.findByRole("link", { name: "Sign up" })).toBeInTheDocument();
  });

  it("should hide the Sign up link when registration is closed", async () => {
    server.use(http.get(`${API_URL}/auth/registration`, () => HttpResponse.json({ open: false })));

    const { queryClient } = renderWithProviders(<LandingHeader />);

    await waitFor(() =>
      expect(queryClient.getQueryData(queryKeys.auth.registration())).toEqual({ open: false }),
    );
    expect(screen.queryByRole("link", { name: "Sign up" })).not.toBeInTheDocument();
  });
});
