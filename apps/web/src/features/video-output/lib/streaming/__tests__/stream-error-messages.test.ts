import { describe, it, expect } from "vitest";
import { getUserFriendlyError } from "../stream-error-messages";

describe("getUserFriendlyError", () => {
  describe("duration limits", () => {
    it("states no hard-coded number when the video is too long", () => {
      const message = getUserFriendlyError("Video too long (130 min)", "VIDEO_TOO_LONG");

      expect(message).toBe("This video is longer than the maximum length we can summarize.");
    });

    it("states no hard-coded number when the video is too short", () => {
      const message = getUserFriendlyError("Video too short (20 sec)", "VIDEO_TOO_SHORT");

      expect(message).toBe("This video is shorter than the minimum length we can summarize.");
    });

    it.each(["VIDEO_TOO_LONG", "VIDEO_TOO_SHORT"])("never embeds a digit in the %s copy", (code) => {
      const message = getUserFriendlyError("raw upstream detail", code);

      expect(message).not.toMatch(/\d/);
    });
  });

  it("maps a known code to its friendly copy", () => {
    expect(getUserFriendlyError("anything", "NO_TRANSCRIPT")).toMatch(/captions/i);
  });

  it("prefers the code over a matching message pattern", () => {
    expect(getUserFriendlyError("request timed out", "LIVE_STREAM")).toMatch(/live streams/i);
  });

  it("falls back to a message pattern when the code is unknown", () => {
    expect(getUserFriendlyError("request timed out", "SOME_NEW_CODE")).toMatch(/took too long/i);
  });

  it("returns the default copy when nothing matches", () => {
    expect(getUserFriendlyError("zzz")).toBe(
      "Something went wrong while processing this video. Please try again.",
    );
  });

  describe("a video whose run already failed (VIDEO_FAILED)", () => {
    it("says the video did not finish and that Retry starts a new run", () => {
      expect(getUserFriendlyError("Pipeline failed", "VIDEO_FAILED")).toBe(
        "This video didn't finish processing. Retry starts it again as a new run.",
      );
    });

    it("never shows the stored raw message or the code", () => {
      const message = getUserFriendlyError("KeyError: 'tabs' in extract_batch (video_summary 6650)", "VIDEO_FAILED");

      expect(message).not.toMatch(/KeyError|extract_batch|VIDEO_FAILED/);
    });

    it("prefers the code over a stored message that matches a pattern", () => {
      expect(getUserFriendlyError("request timed out", "VIDEO_FAILED")).toMatch(/Retry starts it again/);
    });
  });
});
