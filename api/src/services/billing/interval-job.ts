import type { FastifyBaseLogger } from 'fastify';

/**
 * Runs `task` every `intervalMs`, never overlapping itself: the next run is
 * scheduled only after the previous one settled. A failing run is logged and
 * the schedule carries on. Timers are unref'd, so the job never keeps the
 * process alive; `stop()` waits for a run in flight.
 */
export class IntervalJob {
  private timer: NodeJS.Timeout | null = null;
  private inFlight: Promise<void> | null = null;
  private running = false;

  constructor(
    private readonly name: string,
    private readonly task: () => Promise<void>,
    private readonly intervalMs: number,
    private readonly logger: FastifyBaseLogger,
  ) {}

  start(firstDelayMs: number = this.intervalMs): void {
    if (this.running) return;
    this.running = true;
    this.schedule(firstDelayMs);
  }

  async stop(): Promise<void> {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    await this.inFlight;
  }

  private schedule(delayMs: number): void {
    this.timer = setTimeout(() => {
      this.inFlight = this.runOnce().finally(() => {
        this.inFlight = null;
        if (this.running) this.schedule(this.intervalMs);
      });
    }, delayMs);
    this.timer.unref();
  }

  private async runOnce(): Promise<void> {
    try {
      await this.task();
    } catch (err) {
      this.logger.error({ err, job: this.name }, 'interval_job_failed');
    }
  }
}
