import { describe, it, expect, vi } from 'vitest';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import { createContainer } from './container.js';
import { QueuePublisher } from './services/queue-publisher.service.js';
import { IdempotencyService } from './services/idempotency.service.js';
import { IdempotencyRepository } from './repositories/idempotency.repository.js';
import { VideoService } from './services/video.service.js';
import { BillingService } from './services/billing/billing.service.js';
import { BillingRepository } from './repositories/billing.repository.js';

/**
 * `Db.collection()` is called in every repository constructor. We hand back
 * mock collections so the container builds without a live Mongo connection.
 */
function makeMockDb(): Db {
  const mockCollection = {
    findOne: vi.fn(),
    find: vi.fn(),
    insertOne: vi.fn(),
    updateOne: vi.fn(),
    deleteOne: vi.fn(),
    createIndex: vi.fn(),
    createIndexes: vi.fn(),
    aggregate: vi.fn(),
    countDocuments: vi.fn(),
  };
  return { collection: vi.fn(() => mockCollection) } as unknown as Db;
}

const mockLogger = {
  info: () => {}, error: () => {}, warn: () => {},
  debug: () => {}, trace: () => {}, fatal: () => {},
  child: () => mockLogger, level: 'silent', silent: () => {},
} as unknown as FastifyBaseLogger;

describe('createContainer', () => {
  it('exposes a QueuePublisher in the container', () => {
    const container = createContainer(makeMockDb(), mockLogger, {
      queueChannelSupplier: async () => {
        throw new Error('not used in this test');
      },
    });

    expect(container.queuePublisher).toBeInstanceOf(QueuePublisher);
  });

  it('routes publisher calls through the supplied channel supplier', async () => {
    let called = 0;
    const container = createContainer(makeMockDb(), mockLogger, {
      queueChannelSupplier: async () => {
        called++;
        throw new Error('marker');
      },
    });

    await expect(container.queuePublisher.publishVideoJob({
      videoSummaryId: 'a'.repeat(24),
      youtubeId: 'dQw4w9WgXcQ',
      url: 'https://youtu.be/dQw4w9WgXcQ',
      userId: 'u1',
      tier: 'free',
    })).rejects.toThrow('marker');
    expect(called).toBe(1);
  });

  it('exposes the IdempotencyService and IdempotencyRepository', () => {
    const container = createContainer(makeMockDb(), mockLogger, {
      queueChannelSupplier: async () => {
        throw new Error('not used in this test');
      },
    });

    expect(container.idempotencyService).toBeInstanceOf(IdempotencyService);
    expect(container.idempotencyRepository).toBeInstanceOf(IdempotencyRepository);
  });

  it('wires the IdempotencyService into the VideoService for content-addressed dedup', () => {
    // VideoService needs IdempotencyService to compute the dedupKey for
    // upsertCacheByDedupKey. If the container wiring regresses, cross-user
    // dedup silently breaks (the upsert would receive an undefined key).
    const container = createContainer(makeMockDb(), mockLogger, {
      queueChannelSupplier: async () => {
        throw new Error('not used in this test');
      },
    });

    expect(container.videoService).toBeInstanceOf(VideoService);
    // Probe the private field via index access — sturdier than fragile mocks
    // and proves the constructor injection happened.
    expect((container.videoService as unknown as { idempotencyService: IdempotencyService }).idempotencyService)
      .toBe(container.idempotencyService);
  });

  it('exposes the BillingService and BillingRepository', () => {
    const container = createContainer(makeMockDb(), mockLogger, {
      queueChannelSupplier: async () => {
        throw new Error('not used in this test');
      },
    });

    expect(container.billingService).toBeInstanceOf(BillingService);
    expect(container.billingRepository).toBeInstanceOf(BillingRepository);
  });
});
