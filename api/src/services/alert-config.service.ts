import type { AlertConfig, AlertConfigRepository } from '../repositories/alert-config.repository.js';

export interface AlertConfigChange {
  before: AlertConfig;
  after: AlertConfig;
}

/** Reads and replaces the operator alert thresholds; the route audits the change. */
export class AlertConfigService {
  constructor(
    private readonly repository: AlertConfigRepository,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  /** Replace all three thresholds and return what they were and are now. */
  async replace(next: AlertConfig): Promise<AlertConfigChange> {
    const before = await this.repository.get();
    await this.repository.replace(next, this.clock());
    return { before, after: next };
  }
}
