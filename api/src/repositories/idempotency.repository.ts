import { Db, ObjectId, Collection } from 'mongodb';

export type IdempotencyStatus = 'pending' | 'completed';

/**
 * Persisted shape in MongoDB `idempotencyKeys`. A row is inserted in the
 * `pending` state when the route reserves the hash (BEFORE the expensive
 * pipeline work) and promoted to `completed` once the work returns. The
 * unique index on `hash` is the load-bearing serialization point: concurrent
 * submissions race here, and only one inserts; the rest read the existing
 * row and decide what to do based on its status.
 *
 * The TTL index on `expiresAt` evicts stale rows so the collection stays
 * bounded.
 */
export interface IdempotencyKeyDocument {
  _id: ObjectId;
  /** SHA-256 of dedup-relevant inputs (see IdempotencyService.computeKey). */
  hash: string;
  userId: ObjectId;
  status: IdempotencyStatus;
  /** Set when status flips to `completed`. */
  videoSummaryId?: ObjectId;
  /** Set when status flips to `completed`. */
  userVideoId?: ObjectId;
  youtubeId: string;
  pipelineVersion: string;
  createdAt: Date;
  expiresAt: Date;
}

export interface ReserveHashInput {
  hash: string;
  userId: string;
  youtubeId: string;
  pipelineVersion: string;
  ttlSeconds: number;
}

export interface ReserveHashResult {
  /** True when we won the race and inserted the placeholder row. */
  created: boolean;
  /** Either the newly-inserted row (created=true) or the winner's row (created=false). */
  doc: IdempotencyKeyDocument;
}

export interface CompleteHashInput {
  hash: string;
  videoSummaryId: string;
  userVideoId: string;
}

export class IdempotencyRepository {
  private readonly collection: Collection<IdempotencyKeyDocument>;

  constructor(db: Db) {
    this.collection = db.collection('idempotencyKeys');
  }

  /** Strong-consistency read of the current row for a hash, or null. */
  async findByHash(hash: string): Promise<IdempotencyKeyDocument | null> {
    return this.collection.findOne({ hash });
  }

  /**
   * Atomic reserve-or-yield. The unique index on `hash` serializes concurrent
   * callers — exactly one insert wins; the rest catch the duplicate-key
   * error, read the winner's row, and return it with `created: false`.
   *
   * The placeholder carries no videoSummaryId / userVideoId yet — those are
   * patched in by `completeHash` once the pipeline finishes. The status field
   * tells dup-callers whether the original is still in-flight (`pending`) or
   * has resolved (`completed`).
   */
  async reserveHash(input: ReserveHashInput): Promise<ReserveHashResult> {
    const now = new Date();
    const doc: IdempotencyKeyDocument = {
      _id: new ObjectId(),
      hash: input.hash,
      userId: new ObjectId(input.userId),
      status: 'pending',
      youtubeId: input.youtubeId,
      pipelineVersion: input.pipelineVersion,
      createdAt: now,
      expiresAt: new Date(now.getTime() + input.ttlSeconds * 1000),
    };

    try {
      await this.collection.insertOne(doc);
      return { created: true, doc };
    } catch (err) {
      if ((err as { code?: number }).code === 11000) {
        const existing = await this.collection.findOne({ hash: input.hash });
        if (existing) return { created: false, doc: existing };
      }
      throw err;
    }
  }

  /**
   * Promote a `pending` row to `completed`, recording the work's output IDs.
   * Status-scoped — only promotes rows currently in `pending`. If a request
   * races with a stale-hit cleanup that re-reserved the row in a fresh pending
   * cycle, this update no-ops on the original (now gone) row instead of
   * stomping the new owner's placeholder.
   */
  async completeHash(input: CompleteHashInput): Promise<void> {
    await this.collection.updateOne(
      { hash: input.hash, status: 'pending' },
      {
        $set: {
          status: 'completed',
          videoSummaryId: new ObjectId(input.videoSummaryId),
          userVideoId: new ObjectId(input.userVideoId),
        },
      },
    );
  }

  /**
   * Drop a single key by hash. Used on the failure-unwind path so the user
   * can immediately re-submit without waiting out the TTL.
   */
  async invalidateByHash(hash: string): Promise<void> {
    await this.collection.deleteOne({ hash });
  }

  /**
   * Race-safe stale-hit cleanup. Deletes only when the row is still the
   * specific completed row the route observed — if a concurrent request
   * already invalidated and re-reserved a fresh pending row, this no-ops
   * instead of deleting the new owner's placeholder.
   */
  async invalidateStaleCompleted(hash: string, videoSummaryId: string): Promise<void> {
    await this.collection.deleteOne({
      hash,
      status: 'completed',
      videoSummaryId: new ObjectId(videoSummaryId),
    });
  }

  /**
   * Drop every key pointing at a given videoSummaryId. Failure events arrive
   * from the summarizer keyed by videoSummaryId, not hash — this cascade lets
   * the failure path invalidate without recomputing the hash.
   */
  async invalidateByVideoSummaryId(videoSummaryId: string): Promise<void> {
    await this.collection.deleteMany({ videoSummaryId: new ObjectId(videoSummaryId) });
  }

  /**
   * Drop the keys that completed against one library row. Without this a
   * deleted row could be re-served from its completed key until the TTL.
   */
  async invalidateByUserVideoId(userVideoId: string): Promise<number> {
    const result = await this.collection.deleteMany({ userVideoId: new ObjectId(userVideoId) });
    return result.deletedCount;
  }

  /** Drop every key for a video, all users and versions (global purge). */
  async invalidateByYoutubeId(youtubeId: string): Promise<number> {
    const result = await this.collection.deleteMany({ youtubeId });
    return result.deletedCount;
  }
}
