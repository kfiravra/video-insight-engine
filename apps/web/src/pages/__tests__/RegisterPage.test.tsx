import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders, screen } from "../../test/test-utils";
import { server } from "../../test/mocks/server";
import { RegisterPage } from "../RegisterPage";

const API_URL = "http://localhost:3000/api";

describe("RegisterPage", () => {
  it("should show the closed notice instead of the form when registration is closed", async () => {
    server.use(http.get(`${API_URL}/auth/registration`, () => HttpResponse.json({ open: false })));

    renderWithProviders(<RegisterPage />);

    expect(await screen.findByRole("heading", { name: "Registration is closed" })).toBeInTheDocument();
  });

  it("should not render the signup form when registration is closed", async () => {
    server.use(http.get(`${API_URL}/auth/registration`, () => HttpResponse.json({ open: false })));

    renderWithProviders(<RegisterPage />);

    await screen.findByRole("heading", { name: "Registration is closed" });
    expect(screen.queryByRole("button", { name: "Create account" })).not.toBeInTheDocument();
  });

  it("should render the signup form when registration is open", async () => {
    renderWithProviders(<RegisterPage />);

    expect(await screen.findByRole("button", { name: "Create account" })).toBeInTheDocument();
  });
});
