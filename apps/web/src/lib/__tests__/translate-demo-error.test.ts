import { describe, it, expect } from "vitest";
import { ApiError } from "@/api/client";
import { translateDemoError } from "../validation";

const UNAVAILABLE = "The demo isn't available right now. Please try again later.";

describe("translateDemoError", () => {
  it("should not blame credentials when the demo account is rejected", () => {
    const err = new ApiError(401, "Invalid email or password", "INVALID_CREDENTIALS");

    expect(translateDemoError(err)).toBe(UNAVAILABLE);
  });

  it("should report the demo as unavailable when it is disabled", () => {
    const err = new ApiError(403, "The demo is not available", "DEMO_DISABLED");

    expect(translateDemoError(err)).toBe(UNAVAILABLE);
  });

  it("should ask the visitor to wait when rate limited", () => {
    const err = new ApiError(429, "Too many requests", "RATE_LIMITED");

    expect(translateDemoError(err)).toBe("Too many attempts. Wait a minute and try again.");
  });

  it("should point at connectivity when the request never reached the server", () => {
    expect(translateDemoError(new TypeError("Failed to fetch"))).toBe(
      "Couldn't reach the server. Check your connection and try again.",
    );
  });
});
