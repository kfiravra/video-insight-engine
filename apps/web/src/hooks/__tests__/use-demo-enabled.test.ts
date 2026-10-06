import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createWrapper } from "../../test/test-utils";
import { server } from "../../test/mocks/server";
import { useDemoEnabled } from "../use-demo-enabled";
import { useRegistrationOpen } from "../use-registration-open";

const API_URL = "http://localhost:3000/api";

describe("useDemoEnabled", () => {
  it("should be false while the status is loading", () => {
    const { result } = renderHook(() => useDemoEnabled(), { wrapper: createWrapper() });

    expect(result.current).toBe(false);
  });

  it("should report enabled when the API offers the demo", async () => {
    server.use(
      http.get(`${API_URL}/auth/registration`, () => HttpResponse.json({ open: false, demoEnabled: true })),
    );

    const { result } = renderHook(() => useDemoEnabled(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current).toBe(true));
  });

  it("should stay disabled when the API omits the flag", async () => {
    const { result } = renderHook(
      () => ({ demo: useDemoEnabled(), registrationOpen: useRegistrationOpen() }),
      { wrapper: createWrapper() },
    );

    // registrationOpen resolving proves the shared status request has landed.
    await waitFor(() => expect(result.current.registrationOpen).toBe(true));
    expect(result.current.demo).toBe(false);
  });

  it("should fail closed when the status request errors", async () => {
    server.use(
      http.get(`${API_URL}/auth/registration`, () =>
        HttpResponse.json({ error: "INTERNAL_ERROR", message: "boom", statusCode: 500 }, { status: 500 }),
      ),
    );

    const { result } = renderHook(
      () => ({ demo: useDemoEnabled(), registrationOpen: useRegistrationOpen() }),
      { wrapper: createWrapper() },
    );

    // useRegistrationOpen fails open, so `true` here means the error has landed.
    await waitFor(() => expect(result.current.registrationOpen).toBe(true));
    expect(result.current.demo).toBe(false);
  });
});
