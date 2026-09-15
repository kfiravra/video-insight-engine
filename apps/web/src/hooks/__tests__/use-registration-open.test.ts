import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createWrapper } from "../../test/test-utils";
import { server } from "../../test/mocks/server";
import { useRegistrationOpen } from "../use-registration-open";

const API_URL = "http://localhost:3000/api";

describe("useRegistrationOpen", () => {
  it("should be undefined while the status is loading", () => {
    const { result } = renderHook(() => useRegistrationOpen(), { wrapper: createWrapper() });

    expect(result.current).toBeUndefined();
  });

  it("should report open when the API allows registration", async () => {
    const { result } = renderHook(() => useRegistrationOpen(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current).toBe(true));
  });

  it("should report closed when the API disables registration", async () => {
    server.use(http.get(`${API_URL}/auth/registration`, () => HttpResponse.json({ open: false })));

    const { result } = renderHook(() => useRegistrationOpen(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current).toBe(false));
  });

  it("should fail open when the status request errors", async () => {
    server.use(
      http.get(`${API_URL}/auth/registration`, () =>
        HttpResponse.json({ error: "INTERNAL_ERROR", message: "boom", statusCode: 500 }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useRegistrationOpen(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current).toBe(true));
  });
});
