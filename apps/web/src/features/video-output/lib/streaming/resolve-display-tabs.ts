import { sameTabContent } from "@/features/video-output/lib/streaming/tab-content-equal";
import type { StreamTab } from "@/features/video-output/lib/streaming/stream-tabs";
import type { TabEntry } from "@vie/types";

interface ResolveDisplayTabsInput {
  streamTabs: StreamTab[];
  /** The stream holds the run's final tab set (see isStreamSettled). */
  streamSettled: boolean;
  dbTabs: TabEntry[] | null;
  videoStatus: string | undefined;
}

/** The DB tabs, each swapped for its streamed copy when that copy renders the
 *  same content (frame-URL signatures aside). Returns the stream array itself
 *  when every tab matches in order, so nothing downstream re-renders. */
function preferStreamedCopies(dbTabs: TabEntry[], streamTabs: StreamTab[]): StreamTab[] {
  const streamed = new Map(streamTabs.map((tab) => [tab.id, tab]));
  const resolved = dbTabs.map((dbTab) => {
    const copy = streamed.get(dbTab.id);
    return copy && sameTabContent(copy, dbTab, { ignoreFrameSignatures: true }) ? copy : dbTab;
  });
  const unchanged = resolved.length === streamTabs.length && resolved.every((tab, i) => tab === streamTabs[i]);
  return unchanged ? streamTabs : resolved;
}

/**
 * Decide which tab objects the page renders: the streamed set or the DB doc.
 *
 * While the video is processing, the stream has priority (progressive
 * rendering). Once the video is completed, the DB doc is authoritative — by
 * the stream's `final` state, never by tab count:
 * - stream not settled (a dropped connection, a withdrawn tab still held) →
 *   the DB tabs;
 * - stream settled (every tab final, the final order known) → the DB tabs'
 *   order and content, but a tab whose streamed copy renders the same content
 *   keeps that copy. Every DB serve re-signs frame URLs, so swapping in the DB
 *   objects at `done` would re-download every frame image (a flicker) for no
 *   content change; any tab the DB holds differently (or alone) comes from the
 *   DB.
 */
export function resolveDisplayTabs({
  streamTabs,
  streamSettled,
  dbTabs,
  videoStatus,
}: ResolveDisplayTabsInput): StreamTab[] | null {
  const db = dbTabs && dbTabs.length > 0 ? dbTabs : null;
  if (videoStatus === "completed" && db) {
    return streamSettled ? preferStreamedCopies(db, streamTabs) : db;
  }
  if (streamTabs.length > 0) return streamTabs;
  return db;
}
