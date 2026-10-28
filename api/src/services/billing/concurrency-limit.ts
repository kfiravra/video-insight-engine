/**
 * At most `limit` tasks at once; the rest wait their turn in arrival order.
 * The gate runs metadata pre-flights through one, so a playlist of videos
 * with no extract duration does not fire a yt-dlp call per item at once (I9).
 */
export class ConcurrencyLimit {
  private active = 0;
  private readonly waiting: Array<() => void> = [];

  constructor(private readonly limit: number) {
    if (!Number.isInteger(limit) || limit < 1) throw new Error('ConcurrencyLimit needs a positive integer limit');
  }

  async run<T>(task: () => Promise<T>): Promise<T> {
    if (this.active >= this.limit) await new Promise<void>((resolve) => this.waiting.push(resolve));
    this.active++;
    try {
      return await task();
    } finally {
      this.active--;
      this.waiting.shift()?.();
    }
  }
}
