import { Db, ObjectId, Collection } from 'mongodb';

/** The fields of the summarizer's `llm_usage` rows (Python schema) that vie-api reads. */
interface LlmUsageDocument {
  _id: ObjectId;
  request_id?: string | null;
  video_summary_id?: string | null;
  cost_usd?: number | null;
  user_id?: string | null;
  service?: string;
  /** The first 200 characters of the prompt; "" where the service does not store it. */
  prompt_preview?: string | null;
  timestamp?: Date;
}

/** A preview that holds text; the Python services write "" when they keep none. */
const HOLDS_PROMPT_TEXT = { $exists: true, $nin: ['', null] };

/**
 * Access to `llm_usage`, written by the Python services. Every row carries the
 * originating `request_id`, which is how a run's LLM $ is attributed to its
 * billing hold (margin telemetry, never user-facing). Served by vie-admin's
 * `{request_id: 1, timestamp: -1}` index (mirrored in `createBillingIndexes`).
 * vie-api only reads cost; its writes are the privacy scrubs below.
 */
export class LlmUsageRepository {
  private readonly collection: Collection<LlmUsageDocument>;

  constructor(db: Db) {
    this.collection = db.collection('llm_usage');
  }

  /**
   * Σ `cost_usd` of every LLM call a run made (0 when none): its `requestId`
   * and, when known, its cache row. The request id is the client's
   * X-Request-ID, which a client may reuse across videos (I4), so a run with
   * a known row counts only that row's calls; the summarizer stamps
   * `video_summary_id` on every pipeline row.
   */
  async sumRunCost(requestId: string, videoSummaryId: string | null): Promise<number> {
    const match = videoSummaryId ? { request_id: requestId, video_summary_id: videoSummaryId } : { request_id: requestId };
    const [row] = await this.collection
      .aggregate<{ total: number }>([
        { $match: match },
        { $group: { _id: null, total: { $sum: { $ifNull: ['$cost_usd', 0] } } } },
      ])
      .toArray();
    return row?.total ?? 0;
  }

  /**
   * Strip the personal data from rows written before `cutoff` (docs/PRIVACY.md:
   * usage analytics older than 90 days carry no personal data): `user_id` and
   * `prompt_preview`, from every service. `request_id` goes too: it joins to
   * the ledger's `source.ref`, whose rows name the user (I6) — a settle reads
   * it at completion, never after 90 days. Cost, model and timestamp stay. At
   * most `limit` rows per call; a re-run matches nothing it already cleared.
   * Returns the rows changed.
   */
  async scrubPersonalDataBefore(cutoff: Date, limit: number): Promise<number> {
    const linked = {
      $or: [{ user_id: { $exists: true } }, { request_id: { $exists: true } }, { prompt_preview: { $exists: true } }],
    };
    const rows = await this.collection
      .find({ timestamp: { $lt: cutoff }, ...linked }, { projection: { _id: 1 } })
      .limit(limit)
      .toArray();
    if (rows.length === 0) return 0;
    const result = await this.collection.updateMany(
      { _id: { $in: rows.map((row) => row._id) } },
      { $unset: { user_id: '', request_id: '', prompt_preview: '' } },
    );
    return result.modifiedCount;
  }

  /** Rows of `service` that still hold prompt text (an empty preview holds none). */
  async countPromptPreviews(service: string): Promise<number> {
    return this.collection.countDocuments({ service, prompt_preview: HOLDS_PROMPT_TEXT });
  }

  /**
   * Remove the stored prompt text from every row of `service` (the assistant's
   * chat prompts are never kept: docs/PRIVACY.md). A re-run matches nothing it
   * already cleared. Returns the rows changed.
   */
  async unsetPromptPreviews(service: string): Promise<number> {
    const result = await this.collection.updateMany(
      { service, prompt_preview: HOLDS_PROMPT_TEXT },
      { $unset: { prompt_preview: '' } },
    );
    return result.modifiedCount;
  }
}
