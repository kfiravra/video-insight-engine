import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { ApiError, getAccessToken, refreshToken } from "@/api/client";
import { runAssistantAction, sendAssistantMessage, sendLibraryMessage } from "../assistant";

// Mock the API client + auth store so runAssistantAction stays a pure unit test.
vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    getAccessToken: vi.fn(() => "token-abc"),
    refreshToken: vi.fn(() => Promise.resolve(false)),
  };
});

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: { getState: vi.fn(() => ({ forceLogout: vi.fn() })) },
}));

describe("runAssistantAction", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("should POST to the gateway action endpoint with the bearer token and envelope body", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ success: true, action: "create_folder", data: { id: "f1" } }),
    });

    const result = await runAssistantAction(
      "create_folder",
      { name: "Recipes" },
      "vid-123",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:3000/api/assistant/action");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer token-abc");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({
      action: "create_folder",
      params: { name: "Recipes" },
      video_id: "vid-123",
    });
    expect(result).toEqual({
      success: true,
      action: "create_folder",
      data: { id: "f1" },
    });
  });

  it("should omit video_id from the body when no videoSummaryId is given", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ success: true, action: "organize_library" }),
    });

    await runAssistantAction("organize_library", {});

    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(init.body);
    expect(body).not.toHaveProperty("video_id");
  });

  it("should throw a transport error on a non-ok response", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: () => Promise.resolve({}) });

    await expect(runAssistantAction("organize_library", {})).rejects.toThrow(
      "Assistant action failed: 500",
    );
  });
});

describe.each([
  ["sendAssistantMessage", () => sendAssistantMessage("vid-1", "hi", [], vi.fn())],
  ["sendLibraryMessage", () => sendLibraryMessage("hi", [], vi.fn())],
])("%s — failed request", (_name, send) => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("should reject with a RATE_LIMITED ApiError on a 429 response", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 429 });

    const failure = await send().catch((err: unknown) => err);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 429, code: "RATE_LIMITED" });
  });

  it("should reject with a CHAT_LIMIT_REACHED ApiError carrying nextAt when a free account is out of messages", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ error: "CHAT_LIMIT_REACHED", nextAt: "2026-11-02T09:15:00.000Z" }),
    });

    const failure = await send().catch((err: unknown) => err);

    expect(failure).toMatchObject({
      status: 429,
      code: "CHAT_LIMIT_REACHED",
      details: { nextAt: "2026-11-02T09:15:00.000Z" },
    });
  });

  it("should stay RATE_LIMITED for a 429 whose body is not a chat-limit error", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ error: "RATE_LIMITED", message: "Too many requests" }),
    });

    const failure = await send().catch((err: unknown) => err);

    expect(failure).toMatchObject({ status: 429, code: "RATE_LIMITED" });
  });

  it("should keep the plain transport error for a 502 response", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 502 });

    const failure = await send().catch((err: unknown) => err);

    expect(failure).toEqual(new Error("Assistant chat failed: 502"));
    expect(failure).not.toBeInstanceOf(ApiError);
  });
});

describe.each([
  ["sendAssistantMessage", () => sendAssistantMessage("vid-1", "hi", [], vi.fn())],
  ["sendLibraryMessage", () => sendLibraryMessage("hi", [], vi.fn())],
])("%s — 401, token refresh, then rate limited", (_name, send) => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    vi.mocked(getAccessToken).mockReturnValueOnce("token-expired").mockReturnValueOnce("token-fresh");
    vi.mocked(refreshToken).mockResolvedValueOnce(true);
    fetchMock
      .mockResolvedValueOnce({ ok: false, status: 401 })
      .mockResolvedValueOnce({ ok: false, status: 429 });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.mocked(getAccessToken).mockReset().mockReturnValue("token-abc");
    vi.mocked(refreshToken).mockReset().mockResolvedValue(false);
  });

  it("should surface the retry's 429 as a RATE_LIMITED ApiError", async () => {
    const failure = await send().catch((err: unknown) => err);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 429, code: "RATE_LIMITED" });
  });

  it("should retry once with the refreshed token", async () => {
    await send().catch(() => undefined);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer token-fresh");
  });
});
