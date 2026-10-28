import type { FastifyBaseLogger } from 'fastify';
import { randomUUID } from 'crypto';
import { ConfirmChannel } from 'amqplib';
import { QueuePublishError } from '../utils/errors.js';
import { config } from '../config.js';
import {
  QUEUE_TOPOLOGY,
  type VideoJobPayload,
  videoJobPayloadSchema,
  priorityForPlan,
  type QueuePlan,
} from './queue-topology.js';
import type { ProviderConfig } from './summarizer-client.js';

/** Lazy supplier so the publisher works during onReady before any channel exists. */
export type ChannelSupplier = () => Promise<ConfirmChannel>;

export interface PublishVideoJobInput {
  videoSummaryId: string;
  youtubeId: string;
  url: string;
  userId: string | null;
  /** What pays for the run (queue-topology.ts `queuePlanOf`); sets the message priority. */
  plan: QueuePlan;
  providers?: ProviderConfig;
  bypassCache?: boolean;
  requestId?: string;
  /** Override for retry / replay paths. Defaults to 1. */
  attempt?: number;
}

export class QueuePublisher {
  constructor(
    private readonly channelSupplier: ChannelSupplier,
    private readonly logger: FastifyBaseLogger,
  ) {}

  /**
   * Publish a video processing job. Resolves after publisher confirms acknowledge
   * the message — so callers know the broker has durably accepted the payload.
   *
   * Throws QueuePublishError on validation failure, channel failure, or confirm
   * timeout. The route handler maps this to 503 so the client retries cleanly
   * (the DB row already exists so a retry is idempotent).
   */
  async publishVideoJob(input: PublishVideoJobInput): Promise<VideoJobPayload> {
    const parseResult = videoJobPayloadSchema.safeParse({
      videoSummaryId: input.videoSummaryId,
      youtubeId: input.youtubeId,
      url: input.url,
      userId: input.userId ?? null,
      plan: input.plan,
      priority: priorityForPlan(input.plan),
      providers: input.providers ?? null,
      bypassCache: input.bypassCache ?? false,
      requestId: input.requestId ?? randomUUID(),
      attempt: input.attempt ?? 1,
      createdAt: new Date().toISOString(),
    });
    if (!parseResult.success) {
      throw new QueuePublishError(
        `Invalid payload: ${parseResult.error.issues
          .map(i => `${i.path.join('.') || '<root>'}: ${i.message}`)
          .join('; ')}`,
      );
    }
    const payload: VideoJobPayload = parseResult.data;

    const channel = await this.channelSupplier();
    const body = Buffer.from(JSON.stringify(payload));

    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new QueuePublishError('Publisher confirm timed out'));
      }, config.RABBITMQ_PUBLISH_TIMEOUT_MS);

      // publish() returns false when the internal buffer is full; we still wait
      // for the confirm callback in either case. The callback fires with an
      // error if the broker rejects (e.g. queue gone).
      try {
        channel.publish(
          QUEUE_TOPOLOGY.exchange,
          QUEUE_TOPOLOGY.routingKey,
          body,
          {
            persistent: true,
            priority: payload.priority,
            contentType: 'application/json',
            messageId: payload.requestId,
            timestamp: Date.now(),
            headers: {
              'x-attempt': payload.attempt,
              'x-plan': payload.plan,
            },
          },
          (err) => {
            clearTimeout(timeout);
            if (err) {
              reject(new QueuePublishError(`Broker rejected publish: ${err.message}`));
            } else {
              resolve();
            }
          },
        );
      } catch (err) {
        clearTimeout(timeout);
        const message = err instanceof Error ? err.message : String(err);
        reject(new QueuePublishError(`publish() threw: ${message}`));
      }
    });

    this.logger.info(
      {
        videoSummaryId: payload.videoSummaryId,
        youtubeId: payload.youtubeId,
        plan: payload.plan,
        priority: payload.priority,
        attempt: payload.attempt,
        requestId: payload.requestId,
      },
      'Published video job to queue',
    );

    return payload;
  }
}
