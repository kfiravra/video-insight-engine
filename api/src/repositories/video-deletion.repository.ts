import { Db, ObjectId, Collection } from 'mongodb';

/**
 * Counts captured by a global video purge. Each stage of the cascade fills
 * its own field; zero is a legitimate value (a video nobody shared has no
 * share likes).
 */
export interface VideoDeletionCounts {
  videoSummaryCache: number;
  userVideos: number;
  shareLikes: number;
  shareViews: number;
  agentNotes: number;
  idempotencyKeys: number;
  dispatchGuards: number;
  qdrantPoints: number;
  s3Objects: number;
  redisKeys: number;
}

export type VideoDeletionInitiator = 'admin' | 'script';

/** Audit row for a completed global video purge. Retained indefinitely; no PII. */
export interface VideoDeletionDocument {
  _id: ObjectId;
  youtubeId: string;
  /** Every `videoSummaryCache` version that existed when the purge ran. */
  summaryIds: string[];
  scope: 'global';
  initiatedBy: VideoDeletionInitiator;
  adminId?: string | null;
  reason?: string | null;
  counts: VideoDeletionCounts;
  startedAt: Date;
  completedAt: Date;
  durationMs: number;
  /** Per-step failures that did not abort the cascade. */
  warnings?: string[];
}

export interface CreateVideoDeletionAuditData {
  youtubeId: string;
  summaryIds: string[];
  scope: 'global';
  initiatedBy: VideoDeletionInitiator;
  adminId?: string | null;
  reason?: string | null;
  counts: VideoDeletionCounts;
  startedAt: Date;
  completedAt: Date;
  warnings?: string[];
}

export class VideoDeletionRepository {
  private readonly collection: Collection<VideoDeletionDocument>;

  constructor(db: Db) {
    this.collection = db.collection('videoDeletions');
  }

  async insert(data: CreateVideoDeletionAuditData): Promise<VideoDeletionDocument> {
    const doc: Omit<VideoDeletionDocument, '_id'> = {
      youtubeId: data.youtubeId,
      summaryIds: data.summaryIds,
      scope: data.scope,
      initiatedBy: data.initiatedBy,
      adminId: data.adminId ?? null,
      reason: data.reason ?? null,
      counts: data.counts,
      startedAt: data.startedAt,
      completedAt: data.completedAt,
      durationMs: data.completedAt.getTime() - data.startedAt.getTime(),
      warnings: data.warnings && data.warnings.length > 0 ? data.warnings : undefined,
    };
    const result = await this.collection.insertOne(doc as VideoDeletionDocument);
    return { ...doc, _id: result.insertedId };
  }

  async findByYoutubeId(youtubeId: string): Promise<VideoDeletionDocument[]> {
    return this.collection.find({ youtubeId }).sort({ completedAt: -1 }).toArray();
  }
}
