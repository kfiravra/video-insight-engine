import { Db, Collection } from 'mongodb';

/** The fields of the summarizer's `llm_usage` rows (Python schema) that vie-api reads. */
interface LlmUsageDocument {
  request_id?: string | null;
  cost_usd?: number | null;
}

/**
 * Read-only access to `llm_usage`, written by the Python services. Every row
 * carries the originating `request_id`, which is how a run's LLM $ is
 * attributed to its billing hold (margin telemetry, never user-facing).
 * Served by vie-admin's `{request_id: 1, timestamp: -1}` index (mirrored in
 * `createBillingIndexes`).
 */
export class LlmUsageRepository {
  private readonly collection: Collection<LlmUsageDocument>;

  constructor(db: Db) {
    this.collection = db.collection('llm_usage');
  }

  /** Σ `cost_usd` of every LLM call made for `requestId` (0 when none). */
  async sumCostByRequestId(requestId: string): Promise<number> {
    const [row] = await this.collection
      .aggregate<{ total: number }>([
        { $match: { request_id: requestId } },
        { $group: { _id: null, total: { $sum: { $ifNull: ['$cost_usd', 0] } } } },
      ])
      .toArray();
    return row?.total ?? 0;
  }
}
