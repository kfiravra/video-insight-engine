/**
 * In-process mutual exclusion per key: `run(key, fn)` starts `fn` only after
 * every earlier `run` for the same key has settled, and returns `fn`'s own
 * result or error. Billing serialises writes per account with it, so a
 * playlist's worth of shadow holds on one account queue up instead of
 * exhausting the transaction retries on write conflicts. Other processes
 * still race; the transaction + version check stays the real guarantee.
 * Not re-entrant: never call `run` for a key from inside its own `fn`.
 */
export class KeyedMutex {
  private readonly tails = new Map<string, Promise<void>>();

  async run<T>(key: string, fn: () => Promise<T>): Promise<T> {
    const previous = this.tails.get(key) ?? Promise.resolve();
    const result = previous.then(fn);
    // The caller gets fn's error from `result`; the tail only orders the next run.
    const tail = result.then(
      () => undefined,
      () => undefined,
    );
    this.tails.set(key, tail);
    void tail.then(() => {
      if (this.tails.get(key) === tail) this.tails.delete(key);
    });
    return result;
  }
}
