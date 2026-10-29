import { Db, Collection } from 'mongodb';

/**
 * The operator alert thresholds. The `llm_alert_config` collection belongs to
 * vie-admin's evaluator (`services/admin/src/services/alert_evaluator.py`), which
 * reads it as `{...DEFAULT_CONFIG, ...doc}`; field names and the `default`
 * document id are its contract, so they stay snake_case.
 */
export interface AlertConfig {
  /** A single call costing more than this (USD) raises an alert. */
  cost_threshold_usd: number;
  /** Today's spend above this multiple of the prorated 7-day baseline raises a spike alert. */
  daily_spike_multiplier: number;
  /** A failure rate over the last hour above this (0–1) raises an alert. */
  failure_rate_threshold: number;
}

/** What vie-admin serves and the evaluator uses when no document exists. */
export const DEFAULT_ALERT_CONFIG: AlertConfig = {
  cost_threshold_usd: 0.5,
  daily_spike_multiplier: 2.0,
  failure_rate_threshold: 0.2,
};

const CONFIG_ID = 'default';

interface AlertConfigDocument extends AlertConfig {
  _id: string;
  updated_at: Date;
}

export class AlertConfigRepository {
  private readonly collection: Collection<AlertConfigDocument>;

  constructor(db: Db) {
    this.collection = db.collection('llm_alert_config');
  }

  /** The stored thresholds over the defaults, like the evaluator reads them. */
  async get(): Promise<AlertConfig> {
    const doc = await this.collection.findOne({ _id: CONFIG_ID });
    return {
      cost_threshold_usd: doc?.cost_threshold_usd ?? DEFAULT_ALERT_CONFIG.cost_threshold_usd,
      daily_spike_multiplier: doc?.daily_spike_multiplier ?? DEFAULT_ALERT_CONFIG.daily_spike_multiplier,
      failure_rate_threshold: doc?.failure_rate_threshold ?? DEFAULT_ALERT_CONFIG.failure_rate_threshold,
    };
  }

  /** Replace the document, creating it on first use. */
  async replace(config: AlertConfig, now: Date): Promise<void> {
    await this.collection.replaceOne({ _id: CONFIG_ID }, { ...config, updated_at: now }, { upsert: true });
  }
}
