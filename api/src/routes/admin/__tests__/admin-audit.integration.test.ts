import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import type { ConfirmChannel } from 'amqplib';
import { ObjectId } from 'mongodb';

import { buildApp } from '../../../app.js';
import type { Container } from '../../../container.js';
import { config } from '../../../config.js';
import { createMockContainer, type MockContainer } from '../../../test/helpers.js';
import { getTestDb } from '../../../test/setup.js';
import { AdminAuditRepository } from '../../../repositories/admin-audit.repository.js';
import { AuditService } from '../../../services/audit.service.js';

/**
 * Each existing admin mutation, driven through the real route with the real
 * AuditService and repository writing to the in-memory database: the
 * `adminAudit` row exists and names the operator, the action and the target.
 */
describe('admin mutations write an adminAudit row', () => {
  const YT = 'dQw4w9WgXcQ';
  const targetUserId = new ObjectId().toString();
  const adminHeaders = { 'x-admin-key': config.ADMIN_API_KEY, 'x-admin-id': 'op-1' };
  let app: FastifyInstance;
  let mocks: MockContainer;

  async function auditRows() {
    return getTestDb().collection('adminAudit').find({}).toArray();
  }

  beforeAll(async () => {
    mocks = createMockContainer();
    const auditService = new AuditService(
      new AdminAuditRepository(getTestDb()),
      { info: vi.fn(), warn: vi.fn(), error: vi.fn() } as unknown as FastifyBaseLogger,
    );
    app = await buildApp({
      logger: false,
      container: { ...mocks, auditService } as unknown as Partial<Container>,
    });
    app.decorate('rabbitmq', {
      getChannel: async () =>
        ({
          get: vi.fn().mockResolvedValue(false),
          publish: vi.fn(),
          waitForConfirms: vi.fn(),
          ack: vi.fn(),
          nack: vi.fn(),
        }) as unknown as ConfirmChannel,
      isReady: () => true,
    });
    await app.ready();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    mocks.videoCascadeService.deleteVideo.mockResolvedValue({
      scope: 'global',
      youtubeId: YT,
      summaryIds: ['6a7b2f6abacdb32871507996'],
      counts: { videoSummaryCache: 1 },
      warnings: [],
      partial: false,
    });
    mocks.userDeletionService.requestDeletion.mockResolvedValue({
      userId: targetUserId,
      scheduledHardDeleteAt: new Date('2026-06-19T12:00:00Z'),
      graceDays: 30,
    });
    mocks.userDeletionService.executeHardDelete.mockResolvedValue({
      _id: new ObjectId(),
      originalUserId: targetUserId,
      counts: {},
      completedAt: new Date('2026-05-20T12:30:00Z'),
      durationMs: 10,
      warnings: [],
    });
  });

  it('should audit a scheduled user deletion under the operator', async () => {
    await app.inject({ method: 'DELETE', url: `/api/admin/users/${targetUserId}`, headers: adminHeaders });

    expect(await auditRows()).toMatchObject([
      { actorId: 'op-1', action: 'users.delete', targetType: 'user', targetId: targetUserId, after: { mode: 'scheduled' } },
    ]);
  });

  it('should audit an immediate user deletion under the operator', async () => {
    await app.inject({
      method: 'DELETE',
      url: `/api/admin/users/${targetUserId}?immediate=true`,
      headers: adminHeaders,
    });

    expect(await auditRows()).toMatchObject([
      { actorId: 'op-1', action: 'users.delete', targetType: 'user', targetId: targetUserId, after: { mode: 'immediate' } },
    ]);
  });

  it('should audit a global video purge under the operator', async () => {
    await app.inject({
      method: 'DELETE',
      url: `/api/admin/videos/${YT}`,
      headers: { ...adminHeaders, 'content-type': 'application/json' },
      payload: { reason: 'duplicate upload' },
    });

    expect(await auditRows()).toMatchObject([
      { actorId: 'op-1', action: 'videos.purge', targetType: 'video', targetId: YT, reason: 'duplicate upload' },
    ]);
  });

  it('should audit a DLQ replay under the operator', async () => {
    await app.inject({ method: 'POST', url: '/api/admin/queue/replay', headers: adminHeaders });

    expect(await auditRows()).toMatchObject([
      { actorId: 'op-1', action: 'queue.replay', targetType: 'queue', targetId: 'vie.pipeline.dlq', after: { replayed: 0 } },
    ]);
  });

  it('should carry the request id and client ip on the row', async () => {
    await app.inject({
      method: 'DELETE',
      url: `/api/admin/videos/${YT}`,
      headers: { ...adminHeaders, 'x-request-id': 'req-audit-0001' },
    });

    expect(await auditRows()).toMatchObject([{ requestId: 'req-audit-0001', ip: '127.0.0.1' }]);
  });
});
