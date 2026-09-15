import { describe, it, expect } from "vitest";
import { buildWebSocketUrl } from "../websocket-url";

describe("buildWebSocketUrl", () => {
  it("should append /ws when the base is a bare origin", () => {
    expect(buildWebSocketUrl("wss://example.com")).toBe("wss://example.com/ws");
  });

  it("should not double the suffix when the base already ends with /ws", () => {
    expect(buildWebSocketUrl("wss://example.com/ws")).toBe("wss://example.com/ws");
  });

  it("should ignore a trailing slash after /ws", () => {
    expect(buildWebSocketUrl("ws://localhost:3000/ws/")).toBe("ws://localhost:3000/ws");
  });

  it("should ignore a trailing slash on a bare origin", () => {
    expect(buildWebSocketUrl("ws://localhost:3000/")).toBe("ws://localhost:3000/ws");
  });

  it("should keep a path segment that only starts with ws", () => {
    expect(buildWebSocketUrl("wss://example.com/wsgw")).toBe("wss://example.com/wsgw/ws");
  });
});
