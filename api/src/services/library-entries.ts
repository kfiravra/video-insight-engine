import { isStaleVersion, updateOffer, type UpdateOffer } from './library-update.js';
import type {
  CreateUserVideoData,
  UserVideoDocument,
  VideoRepository,
  VideoSummaryCacheDocument,
} from '../repositories/video.repository.js';

// D-LIB library entries. An update to the latest version re-points the
// user's entry at the current version's row instead of adding a second one,
// and a video the user already has, filed into another folder, never forces a
// run.

type EntryStore = Pick<VideoRepository, 'createUserVideo' | 'repointUserVideo'>;
type CopyStore = Pick<VideoRepository, 'findUserVideoByYoutubeId' | 'findCacheByDedupKey' | 'findCacheById'>;
type LiveRow = Pick<VideoSummaryCacheDocument, 'status' | 'pipelineVersion' | 'updatedAt'>;

/** A video filed into another folder on the user's own copy: free, flagged when an older pipeline wrote it. */
export interface FiledCopy extends UpdateOffer {
  video: {
    id: string;
    videoSummaryId: string;
    youtubeId: string;
    status: 'completed';
    title?: string;
    channel?: string;
    duration?: number;
    thumbnailUrl?: string;
    version: number;
    isLatest: boolean;
  };
  cached: true;
}

/**
 * The submit's library entry: `replaced` (always an entry on a completed,
 * outdated copy) re-pointed at `data`'s row for an update, else a new entry.
 * A row that is not completed yet can still fail, so the entry remembers the
 * copy it left until the run ends (library-update.repository.ts).
 */
export async function placeLibraryEntry(
  store: EntryStore,
  replaced: UserVideoDocument | null,
  data: CreateUserVideoData,
): Promise<UserVideoDocument> {
  if (!replaced) return store.createUserVideo(data);
  const left = replaced.videoSummaryId.toString();
  const canFail = data.status !== 'completed' && left !== data.videoSummaryId;
  const repointed = await store.repointUserVideo(replaced._id.toString(), data, canFail ? left : undefined);
  // An entry deleted meanwhile is simply made again.
  return repointed ?? store.createUserVideo(data);
}

/** The current version's shared row serves a submit without a run: completed by the current pipeline, or a live run to attach to. */
export function servesWithoutRun(row: LiveRow, stallMs: number, now: number = Date.now()): boolean {
  if (row.status === 'completed') return !isStaleVersion(row);
  return (row.status === 'pending' || row.status === 'processing') && now - row.updatedAt.getTime() <= stallMs;
}

/**
 * The row to file a video the user already has into another folder: their own
 * completed copy, whatever its version, unless the current version's shared
 * row serves the submit free anyway (the normal flow is preferred then). Null
 * when the user has no completed copy.
 */
export async function ownCopyToFile(
  store: CopyStore,
  submit: { userId: string; youtubeId: string; dedupKey: string },
  stallMs: number,
): Promise<VideoSummaryCacheDocument | null> {
  const copy = await store.findUserVideoByYoutubeId(submit.userId, submit.youtubeId);
  if (!copy) return null;
  const current = await store.findCacheByDedupKey(submit.dedupKey);
  if (current && servesWithoutRun(current, stallMs)) return null;
  const row = await store.findCacheById(copy.videoSummaryId.toString());
  return row?.status === 'completed' ? row : null;
}

/** A new entry in `folderId` on the user's own copy's row. */
export async function fileOwnCopy(
  store: EntryStore,
  target: { userId: string; youtubeId: string; folderId: string },
  row: VideoSummaryCacheDocument,
): Promise<FiledCopy> {
  const { title, channel, duration, thumbnailUrl, version, isLatest } = row;
  const videoSummaryId = row._id.toString();
  const entry = await store.createUserVideo({ ...target, videoSummaryId, title, channel, duration, thumbnailUrl, status: 'completed' });
  return {
    video: { id: entry._id.toString(), videoSummaryId, youtubeId: target.youtubeId, status: 'completed', title, channel, duration, thumbnailUrl, version, isLatest },
    cached: true,
    ...updateOffer(row),
  };
}
