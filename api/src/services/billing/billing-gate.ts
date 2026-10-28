import { ConcurrencyLimit } from './concurrency-limit.js';
import type { HoldView } from '@vie/types';
import type { AllocationRejection } from './allocation.js';
import type { BillingService, Quote } from './billing.service.js';

/** Where a video's length comes from when the caller doesn't know it (SummarizerClient). */
export interface VideoMetadataSource {
  getVideoMetadata(youtubeId: string): Promise<{ duration: number }>;
}

export interface GateSubmission {
  requestId: string;
  userId: string;
  youtubeId: string;
  /** Known length in seconds (playlist extract); otherwise the metadata pre-flight is asked. */
  durationSec?: number | null;
}

export type Admission =
  | { admitted: true; quote: Quote; hold: HoldView }
  | { admitted: false; quote: Quote; rejection: AllocationRejection };

/**
 * The submit-time half of the ledger: duration → quote → hold, awaited on the
 * request path by LedgerEnforcement (P3.1), which surfaces every refusal.
 */
/** Metadata pre-flights (yt-dlp behind the summarizer) the gate runs at once (I9). */
const METADATA_CONCURRENCY = 3;

export class BillingGate {
  private readonly metadataSlots: ConcurrencyLimit;

  constructor(
    private readonly billing: BillingService,
    private readonly metadata: VideoMetadataSource,
    metadataConcurrency: number = METADATA_CONCURRENCY,
  ) {
    this.metadataSlots = new ConcurrencyLimit(metadataConcurrency);
  }

  /** Seconds; throws MetadataUnavailableError when the pre-flight cannot tell. */
  async durationOf(youtubeId: string, known?: number | null): Promise<number> {
    if (known !== undefined && known !== null && known > 0) return known;
    const { duration } = await this.metadataSlots.run(() => this.metadata.getVideoMetadata(youtubeId));
    return duration;
  }

  async quote(submission: GateSubmission): Promise<Quote> {
    const { userId, youtubeId, durationSec } = submission;
    return this.billing.quote(userId, youtubeId, await this.durationOf(youtubeId, durationSec));
  }

  /**
   * Quote and, when the quote admits, hold for `requestId`. Throws
   * MetadataUnavailableError and the hold's own errors (a refusal raced in
   * after the quote, HoldFinalizedError); a refused quote is returned with
   * its rejection (enforcement: `throw rejectionToError(rejection)`).
   */
  async admit(submission: GateSubmission): Promise<Admission> {
    const quote = await this.quote(submission);
    // A repeated run id keeps its open hold (re-deciding would count the hold
    // against itself) — but only for the same user and video (W3).
    const open = await this.billing.openHoldFor(submission.requestId, submission);
    if (open) return { admitted: true, quote, hold: open };
    const { decision } = quote;
    if (!decision.ok) return { admitted: false, quote, rejection: decision };
    return { admitted: true, quote, hold: await this.billing.hold(submission.requestId, quote) };
  }
}
