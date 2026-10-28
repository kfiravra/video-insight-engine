import { config } from '../config.js';
import type { VideoSummaryCacheDocument } from '../repositories/video.repository.js';

// D-LIB: opening a copy the user already has is always free. A copy an older
// pipeline wrote is served as it is, flagged `updateAvailable`; the update is
// the user's explicit choice (`updateToLatest`), never a side effect of
// submitting the video again. It goes the new-video way at the current
// version (library-entries.ts), so it costs what a new video would.

type VersionStamp = Pick<VideoSummaryCacheDocument, 'pipelineVersion'>;
type ServedRow = Pick<VideoSummaryCacheDocument, 'status' | 'pipelineVersion'>;

/** The response flag for an update on offer: present only as `true`, never `false`. */
export interface UpdateOffer {
  updateAvailable?: true;
}

/** Whether the user already has the video on a shared row, in any folder. */
export interface LibraryReader {
  userHasAccessToSummary(userId: string, videoSummaryId: string): Promise<boolean>;
}

/**
 * A doc is stale when it carries a `pipelineVersion` stamp that differs from
 * the canonical version (packages/shared/src/config/pipeline-version.json).
 * Docs WITHOUT the field predate stamping and are current-legacy, so a version
 * bump can never mass-flag the library. Stamping happens summarizer-side at
 * pipeline write time.
 */
export function isStaleVersion(doc: VersionStamp): boolean {
  return typeof doc.pipelineVersion === 'string' && doc.pipelineVersion !== config.PIPELINE_VERSION;
}

/** A completed summary an older pipeline wrote: the one kind of copy an update is offered for. */
export function isOutdated<T extends ServedRow>(doc: T | null | undefined): doc is T {
  return !!doc && doc.status === 'completed' && isStaleVersion(doc);
}

export function updateOffer(doc: ServedRow | null | undefined): UpdateOffer {
  return isOutdated(doc) ? { updateAvailable: true } : {};
}

/** The response without its update offer: the shared demo account may not pay for one. */
export function withoutUpdateOffer<T extends UpdateOffer>(response: T): Omit<T, 'updateAvailable'> {
  const { updateAvailable: _hidden, ...rest } = response;
  return rest;
}

/**
 * Attaching to a completed shared row an older pipeline wrote, at the current
 * dedupKey: a user who never had the video, or one updating, gets it
 * regenerated (a new video to them, charged); one who already has it on this
 * row is served it as it is.
 */
export async function regeneratesOnAttach(
  library: LibraryReader,
  userId: string,
  row: VersionStamp & { _id: { toString(): string } },
  updateToLatest: boolean,
): Promise<boolean> {
  if (!isStaleVersion(row)) return false;
  if (updateToLatest) return true;
  return !(await library.userHasAccessToSummary(userId, row._id.toString()));
}
