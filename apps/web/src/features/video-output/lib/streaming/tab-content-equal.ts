/**
 * Content equality for assembled tabs — what decides whether a re-emitted or
 * DB-served tab differs from the copy already on screen.
 */

import type { TabEntry } from "@vie/types";

/** The TabEntry fields a renderer reads; stream-only fields (position, final,
 *  provisional) are not content. */
const CONTENT_KEYS = [
  "id",
  "label",
  "emoji",
  "component",
  "props",
  "goal",
  "crossTabLinks",
  "attachments",
  "degradedFrom",
] as const satisfies readonly (keyof TabEntry)[];

// Mirrors api/src/utils/refresh-frame-urls.ts: every DB serve re-signs the
// `thumbnailUrl`/`url` of each object carrying a frame `s3Key`, so those two
// values differ between serves while pointing at the same frame.
const SIGNED_URL_KEYS: ReadonlySet<string> = new Set(["thumbnailUrl", "url"]);
const FRAME_KEY_PREFIX = "videos/";

interface TabContentOptions {
  /** Treat two signatures of the same frame URL as equal (stream vs DB copy). */
  ignoreFrameSignatures?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Keys with a defined value — a key set to `undefined` counts as absent. */
function definedKeys(obj: Record<string, unknown>): string[] {
  return Object.keys(obj).filter((key) => obj[key] !== undefined);
}

function isSameSignedFrame(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  return typeof a.s3Key === "string" && a.s3Key.startsWith(FRAME_KEY_PREFIX) && a.s3Key === b.s3Key;
}

function recordsEqual(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
  ignoreFrameSignatures: boolean,
): boolean {
  const keys = definedKeys(a);
  if (keys.length !== definedKeys(b).length) return false;
  const signedFrame = ignoreFrameSignatures && isSameSignedFrame(a, b);
  return keys.every((key) => {
    if (b[key] === undefined) return false;
    if (signedFrame && SIGNED_URL_KEYS.has(key)) return isNonEmptyString(a[key]) && isNonEmptyString(b[key]);
    return valuesEqual(a[key], b[key], ignoreFrameSignatures);
  });
}

function valuesEqual(a: unknown, b: unknown, ignoreFrameSignatures: boolean): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, i) => valuesEqual(item, b[i], ignoreFrameSignatures));
  }
  if (!isRecord(a) || !isRecord(b)) return false;
  return recordsEqual(a, b, ignoreFrameSignatures);
}

/** True when both tabs render the same content. */
export function sameTabContent(a: TabEntry, b: TabEntry, options: TabContentOptions = {}): boolean {
  const { ignoreFrameSignatures = false } = options;
  return CONTENT_KEYS.every((key) => valuesEqual(a[key], b[key], ignoreFrameSignatures));
}
