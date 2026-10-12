/**
 * The streamed tab set — the client side of the tab_ready emission contract
 * (docs/API-REFERENCE.md#tab_ready, summarizer pipeline/tab_ledger.py).
 *
 * Tabs are keyed by id: a later emission replaces the earlier one (content,
 * position, flags). `position` is a sort key, never a splice index — exact on
 * a `final` emission, an estimate before it — so the set is kept sorted by
 * (position, final first, arrival). `complete.tabIds` withdraws what the final
 * response no longer holds.
 */

import { sameTabContent } from "@/features/video-output/lib/streaming/tab-content-equal";
import type { TabEntry } from "@vie/types";

/** A streamed tab: the TabEntry plus the stream fields of its last emission. */
export interface StreamTab extends TabEntry {
  /** Sort key — exact once `final`. Absent (legacy emitter) sorts last. */
  position?: number;
  /** The tab's last emission this run: content and position are settled. */
  final?: boolean;
  /** The plan-done Moments placeholder, replaced later by the same id. */
  provisional?: boolean;
}

export interface StreamTabSet {
  tabs: StreamTab[];
  /** Streamed tab id → the id that superseded it via `replaces`. */
  replacedTabIds: Record<string, string>;
}

/** (position, final before non-final). Equal keys return 0 so the stable sort
 *  keeps arrival order — two tabs can share an estimated position. */
export function compareStreamTabs(a: StreamTab, b: StreamTab): number {
  const pa = a.position ?? Number.POSITIVE_INFINITY;
  const pb = b.position ?? Number.POSITIVE_INFINITY;
  if (pa !== pb) return pa < pb ? -1 : 1;
  return Number(b.final === true) - Number(a.final === true);
}

/** A replay can neither undo a final emission nor put a placeholder back over
 *  real content (the contract never sends either in order). */
function isStale(existing: StreamTab, incoming: StreamTab): boolean {
  if (existing.final === true && incoming.final !== true) return true;
  return incoming.provisional === true && existing.provisional !== true;
}

function sameFlags(a: StreamTab, b: StreamTab): boolean {
  return a.position === b.position && a.final === b.final && a.provisional === b.provisional;
}

/** The incoming emission; when its content is unchanged the existing content
 *  objects are kept, so a re-emit re-renders nothing (memoized renderers hold). */
function mergeEmission(existing: StreamTab | undefined, incoming: StreamTab): StreamTab {
  if (!existing || !sameTabContent(existing, incoming)) return incoming;
  return {
    ...existing,
    position: incoming.position,
    final: incoming.final,
    provisional: incoming.provisional,
  };
}

/** The non-final tab `replaces` names, unless it is the incoming tab itself. */
function supersededTab(tabs: StreamTab[], incomingId: string, replaces?: string): StreamTab | undefined {
  if (!replaces || replaces === incomingId) return undefined;
  return tabs.find((t) => t.id === replaces && t.final !== true);
}

/**
 * Apply one tab_ready. With `replaces`, the incoming tab takes the superseded
 * tab's slot (its arrival rank among equal positions) and the superseded id is
 * recorded so focus can follow it. Returns `set` itself when nothing changed.
 */
export function upsertStreamTab(set: StreamTabSet, incoming: StreamTab, replaces?: string): StreamTabSet {
  const existing = set.tabs.find((t) => t.id === incoming.id);
  if (existing && isStale(existing, incoming)) return set;
  if (incoming.provisional === true && incoming.id in set.replacedTabIds) return set;
  const superseded = supersededTab(set.tabs, incoming.id, replaces);
  if (!superseded && existing && sameFlags(existing, incoming) && sameTabContent(existing, incoming)) {
    return set;
  }

  const tab = mergeEmission(existing, incoming);
  const slot = existing ?? superseded;
  let tabs = slot ? set.tabs.map((t) => (t === slot ? tab : t)) : [...set.tabs, tab];
  if (superseded && existing) tabs = tabs.filter((t) => t !== superseded);
  tabs.sort(compareStreamTabs);

  const replacedTabIds = superseded
    ? { ...set.replacedTabIds, [superseded.id]: incoming.id }
    : set.replacedTabIds;
  return { tabs, replacedTabIds };
}

/** Drop tabs the final response withdrew (`complete.tabIds`). */
export function withdrawUnlisted(tabs: StreamTab[], tabIds: string[]): StreamTab[] {
  const kept = new Set(tabIds);
  const remaining = tabs.filter((t) => kept.has(t.id));
  return remaining.length === tabs.length ? tabs : remaining;
}

interface StreamSettleInput {
  tabs: StreamTab[];
  /** `complete.tabIds`, once received. */
  finalTabIds: string[] | null;
  isCached: boolean;
  phase: string;
}

/**
 * True when the stream holds the run's final tab set: `complete` listed the
 * final order and every tab of it arrived final — or, for a cached replay
 * (which sends no `complete`), `done` came with every tab final.
 */
export function isStreamSettled({ tabs, finalTabIds, isCached, phase }: StreamSettleInput): boolean {
  if (tabs.length === 0 || !tabs.every((t) => t.final === true)) return false;
  if (finalTabIds) return finalTabIds.length === tabs.length;
  return isCached && phase === "done";
}
