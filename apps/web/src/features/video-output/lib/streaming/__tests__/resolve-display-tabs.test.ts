import { describe, expect, it } from "vitest";

import { resolveDisplayTabs } from "@/features/video-output/lib/streaming/resolve-display-tabs";
import type { StreamTab } from "@/features/video-output/lib/streaming/stream-tabs";

import type { TabEntry } from "@vie/types";

function tabs(n: number, prefix = "t"): TabEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `${prefix}${i}`,
    label: `Tab ${i}`,
    emoji: "",
    component: "display_section",
    props: {},
  })) as TabEntry[];
}

/** The streamed copy of `entries`: final at their index. */
function streamed(entries: TabEntry[]): StreamTab[] {
  return entries.map((tab, position) => ({ ...tab, position, final: true }));
}

function momentTab(thumbnailUrl: string): TabEntry {
  return {
    id: "moments",
    label: "Moments",
    emoji: "🎬",
    component: "moment_track",
    props: { items: [{ seconds: 5, label: "Intro", s3Key: "videos/abc/frame_1.jpg", thumbnailUrl }] },
  };
}

describe("resolveDisplayTabs", () => {
  describe("while the video is processing", () => {
    it("should prefer stream tabs when the stream holds some", () => {
      const stream = streamed(tabs(2, "s"));

      const result = resolveDisplayTabs({
        streamTabs: stream,
        streamSettled: false,
        dbTabs: tabs(5, "d"),
        videoStatus: "processing",
      });

      expect(result).toBe(stream);
    });

    it("should fall back to DB tabs when nothing streamed", () => {
      const db = tabs(3, "d");

      const result = resolveDisplayTabs({ streamTabs: [], streamSettled: false, dbTabs: db, videoStatus: "processing" });

      expect(result).toBe(db);
    });
  });

  describe("once the video is completed", () => {
    it("should prefer the DB doc when the stream is not settled", () => {
      const db = tabs(5, "d");

      const result = resolveDisplayTabs({
        streamTabs: streamed(tabs(2, "s")),
        streamSettled: false,
        dbTabs: db,
        videoStatus: "completed",
      });

      expect(result).toBe(db);
    });

    it("should prefer the DB doc over an unsettled stream that holds more tabs", () => {
      const db = tabs(3, "d");

      const result = resolveDisplayTabs({
        streamTabs: streamed(tabs(6, "s")),
        streamSettled: false,
        dbTabs: db,
        videoStatus: "completed",
      });

      expect(result).toBe(db);
    });

    it("should keep the settled stream's tab objects when the DB copy renders the same content", () => {
      const stream = streamed(tabs(3, "t"));

      const result = resolveDisplayTabs({
        streamTabs: stream,
        streamSettled: true,
        dbTabs: tabs(3, "t"),
        videoStatus: "completed",
      });

      expect(result).toBe(stream);
    });

    it("should keep a streamed tab whose DB copy differs only by re-signed frame URLs", () => {
      const stream = streamed([momentTab("https://s3/frame_1.jpg?X-Amz-Signature=run")]);

      const result = resolveDisplayTabs({
        streamTabs: stream,
        streamSettled: true,
        dbTabs: [momentTab("https://s3/frame_1.jpg?X-Amz-Signature=serve")],
        videoStatus: "completed",
      });

      expect(result?.[0]).toBe(stream[0]);
    });

    it("should take the DB tab when its content differs from the settled stream's copy", () => {
      const db = tabs(2, "t");
      const changed: TabEntry = { ...db[1], label: "Renamed by the DB" };

      const result = resolveDisplayTabs({
        streamTabs: streamed(db),
        streamSettled: true,
        dbTabs: [db[0], changed],
        videoStatus: "completed",
      });

      expect(result?.[1]).toBe(changed);
    });

    it("should take the DB tab when the DB adds a frame URL the stream lacked", () => {
      const bare: TabEntry = {
        ...momentTab("x"),
        props: { items: [{ seconds: 5, label: "Intro", s3Key: "videos/abc/frame_1.jpg" }] },
      };
      const db = momentTab("https://s3/frame_1.jpg?X-Amz-Signature=serve");

      const result = resolveDisplayTabs({
        streamTabs: streamed([bare]),
        streamSettled: true,
        dbTabs: [db],
        videoStatus: "completed",
      });

      expect(result?.[0]).toBe(db);
    });

    it("should follow the DB doc's order and membership even when the stream is settled", () => {
      const db = tabs(3, "t");

      const result = resolveDisplayTabs({
        streamTabs: streamed([db[0], db[1]]),
        streamSettled: true,
        dbTabs: db,
        videoStatus: "completed",
      });

      expect(result?.map((t) => t.id)).toEqual(["t0", "t1", "t2"]);
    });

    it("should keep stream tabs when the completed doc has none", () => {
      const stream = streamed(tabs(2, "s"));

      const result = resolveDisplayTabs({ streamTabs: stream, streamSettled: true, dbTabs: [], videoStatus: "completed" });

      expect(result).toBe(stream);
    });
  });

  it("should return null when neither source has tabs", () => {
    const result = resolveDisplayTabs({
      streamTabs: [],
      streamSettled: false,
      dbTabs: null,
      videoStatus: undefined,
    });

    expect(result).toBeNull();
  });
});
