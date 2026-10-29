import { ObjectId } from 'mongodb';
import type { Collection, Db } from 'mongodb';
import type { UserVideoDocument, VideoSummaryCacheDocument } from './video.repository.js';

// D-LIB: an update to the latest version re-points a library entry at the
// current version's row and remembers the completed copy it left
// (`previousVideoSummaryId`) while that row's run can still fail. These two
// settle such entries when the run ends, from the summarizer's status relay.

/** What a failed run's settle did with the entries an update moved onto it. */
export interface RestoredUpdates {
  /** Entries handed back to the copy they left. */
  restored: number;
  /** Entries whose previous copy is gone: they keep the failed state. */
  stranded: number;
}

type PreviousCopy = Pick<VideoSummaryCacheDocument, '_id' | 'status' | 'title' | 'channel' | 'duration' | 'thumbnailUrl'>;

const PREVIOUS_COPY_PROJECTION = { status: 1, title: 1, channel: 1, duration: 1, thumbnailUrl: 1 } as const;

export class LibraryUpdateRepository {
  private readonly userVideos: Collection<UserVideoDocument>;
  private readonly cache: Collection<VideoSummaryCacheDocument>;

  constructor(db: Db) {
    this.userVideos = db.collection('userVideos');
    this.cache = db.collection('videoSummaryCache');
  }

  /**
   * The run an update moved entries onto failed: each goes back to the copy it
   * left, with that row's status and denormalised fields, so the update is on
   * offer again and can be retried. Idempotent: a restored entry no longer
   * points at the failed row, and one whose previous copy is gone is left as it is.
   */
  async restoreFailedUpdates(failedId: string): Promise<RestoredUpdates> {
    const failed = new ObjectId(failedId);
    const left = await this.userVideos.distinct('previousVideoSummaryId', { videoSummaryId: failed, previousVideoSummaryId: { $exists: true } });
    const outcome: RestoredUpdates = { restored: 0, stranded: 0 };
    for (const previousId of left.filter((id): id is ObjectId => id instanceof ObjectId)) {
      const { restored, stranded } = await this.restoreTo(failed, previousId);
      outcome.restored += restored;
      outcome.stranded += stranded;
    }
    return outcome;
  }

  /** The run completed: the entries an update moved onto it stay there and forget the copy they left. */
  async clearSettledUpdates(completedId: string): Promise<number> {
    const result = await this.userVideos.updateMany(
      { videoSummaryId: new ObjectId(completedId), previousVideoSummaryId: { $exists: true } },
      { $unset: { previousVideoSummaryId: '' } },
    );
    return result.modifiedCount;
  }

  private async restoreTo(failed: ObjectId, previousId: ObjectId): Promise<RestoredUpdates> {
    const filter = { videoSummaryId: failed, previousVideoSummaryId: previousId };
    const copy = await this.cache.findOne<PreviousCopy>({ _id: previousId }, { projection: PREVIOUS_COPY_PROJECTION });
    if (!copy) return { restored: 0, stranded: await this.userVideos.countDocuments(filter) };
    const { status, title, channel, duration, thumbnailUrl } = copy;
    const result = await this.userVideos.updateMany(
      filter,
      {
        $set: { videoSummaryId: previousId, status, title, channel, duration, thumbnailUrl, updatedAt: new Date() },
        $unset: { previousVideoSummaryId: '' },
      },
      { ignoreUndefined: true },
    );
    return { restored: result.modifiedCount, stranded: 0 };
  }
}
