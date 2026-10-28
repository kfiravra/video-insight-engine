import { describe, it, expect, vi } from 'vitest';
import type { FastifyBaseLogger } from 'fastify';

import { AdminReplayGuard } from '../admin-replay-guard.service.js';
import { ServiceUnavailableError } from '../../utils/errors.js';

const SIGNATURE = 'a'.repeat(64);

function createLogger() {
  return { error: vi.fn(), warn: vi.fn(), info: vi.fn() };
}

function guardOver(set: ReturnType<typeof vi.fn>, logger = createLogger()) {
  return { guard: new AdminReplayGuard({ set } as never, logger as unknown as FastifyBaseLogger), logger };
}

describe('AdminReplayGuard', () => {
  it('should record the signature with SET NX and the requested lifetime', async () => {
    const set = vi.fn().mockResolvedValue('OK');
    const { guard } = guardOver(set);

    await guard.claim(SIGNATURE, 301);

    expect(set).toHaveBeenCalledWith(`vie:api:admin-actor-sig:${SIGNATURE}`, '1', 'EX', 301, 'NX');
  });

  it('should report the first use of a signature', async () => {
    const { guard } = guardOver(vi.fn().mockResolvedValue('OK'));

    expect(await guard.claim(SIGNATURE, 300)).toBe(true);
  });

  it('should report a signature seen before as a replay', async () => {
    const { guard } = guardOver(vi.fn().mockResolvedValue(null));

    expect(await guard.claim(SIGNATURE, 300)).toBe(false);
  });

  it('should round a fractional lifetime up and never record for less than a second', async () => {
    const set = vi.fn().mockResolvedValue('OK');
    const { guard } = guardOver(set);

    await guard.claim(SIGNATURE, 0.2);

    expect(set.mock.calls[0][3]).toBe(1);
  });

  it('should fail closed with a 503 when Redis cannot be reached', async () => {
    const { guard } = guardOver(vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));

    await expect(guard.claim(SIGNATURE, 300)).rejects.toBeInstanceOf(ServiceUnavailableError);
  });

  it('should log why it failed closed', async () => {
    const { guard, logger } = guardOver(vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));

    await guard.claim(SIGNATURE, 300).catch(() => undefined);

    expect(logger.error).toHaveBeenCalledWith(expect.objectContaining({ err: expect.any(Error) }), 'admin_replay_guard_unavailable');
  });

  it('should fail closed with a 503 when there is no Redis at all', async () => {
    const guard = new AdminReplayGuard(undefined, createLogger() as unknown as FastifyBaseLogger);

    await expect(guard.claim(SIGNATURE, 300)).rejects.toMatchObject({ status: 503 });
  });
});
