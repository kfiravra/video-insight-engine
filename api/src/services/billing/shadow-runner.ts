import type { FastifyBaseLogger } from 'fastify';

/**
 * Runs shadow-billing work off the request path, one promise chain per key
 * (a requestId): a step starts only after every earlier step for the same
 * key settled, so a fast `cached` release can never overtake its hold. A
 * failing step is logged at warn and the chain carries on; nothing is ever
 * thrown back to the caller, and the caller never awaits.
 */
export class ShadowRunner {
  private readonly chains = new Map<string, Promise<void>>();

  constructor(private readonly logger: FastifyBaseLogger) {}

  enqueue(key: string, label: string, step: () => Promise<void>): void {
    const previous = this.chains.get(key) ?? Promise.resolve();
    const next = previous.then(step).catch((err: unknown) => {
      this.logger.warn({ err, key, step: label }, 'billing shadow step failed');
    });
    this.chains.set(key, next);
    void next.then(() => {
      if (this.chains.get(key) === next) this.chains.delete(key);
    });
  }

  /** Steps still queued or running (tests, shutdown). */
  get pending(): number {
    return this.chains.size;
  }

  /** Resolve once every queued step — including ones queued while draining — has settled. */
  async drain(): Promise<void> {
    while (this.chains.size > 0) {
      await Promise.all([...this.chains.values()]);
    }
  }
}
