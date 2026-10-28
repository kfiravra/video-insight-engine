import { Db, Collection } from 'mongodb';

/**
 * A row of `llm_alerts`, the operator alert feed shared by the summarizer,
 * llm-common and vie-admin (rendered on its Alerts page). vie-api writes
 * billing alerts here so they land in the same place.
 */
export interface LlmAlertDocument {
  type: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  service: string;
  timestamp: Date;
  [detail: string]: unknown;
}

export class LlmAlertsRepository {
  private readonly collection: Collection<LlmAlertDocument>;

  constructor(db: Db) {
    this.collection = db.collection('llm_alerts');
  }

  /**
   * Insert `alert` unless one of the same type matching `sameAs` was written
   * at or after `since` (a cooldown, so a persistent problem does not alert
   * on every tick). Returns whether a row was written.
   */
  async recordOnce(alert: LlmAlertDocument, sameAs: Record<string, unknown>, since: Date): Promise<boolean> {
    const recent = await this.collection.findOne({ type: alert.type, ...sameAs, timestamp: { $gte: since } });
    if (recent) return false;
    await this.collection.insertOne(alert);
    return true;
  }
}
