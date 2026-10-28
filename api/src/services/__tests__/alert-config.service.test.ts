import { describe, it, expect, vi } from 'vitest';

import type { AlertConfig, AlertConfigRepository } from '../../repositories/alert-config.repository.js';
import { AlertConfigService } from '../alert-config.service.js';

const OLD: AlertConfig = { cost_threshold_usd: 0.5, daily_spike_multiplier: 2, failure_rate_threshold: 0.2 };
const NEW: AlertConfig = { cost_threshold_usd: 1.5, daily_spike_multiplier: 3, failure_rate_threshold: 0.1 };
const NOW = new Date('2026-10-28T12:00:00Z');

function setup() {
  const repository = {
    get: vi.fn().mockResolvedValue(OLD),
    replace: vi.fn().mockResolvedValue(undefined),
  };
  const service = new AlertConfigService(repository as unknown as AlertConfigRepository, () => NOW);
  return { repository, service };
}

describe('AlertConfigService', () => {
  describe('replace', () => {
    it('should return the thresholds as they were and as they are now', async () => {
      const { service } = setup();

      expect(await service.replace(NEW)).toEqual({ before: OLD, after: NEW });
    });

    it('should read the old thresholds before writing the new ones', async () => {
      const { repository, service } = setup();
      const order: string[] = [];
      repository.get.mockImplementation(async () => {
        order.push('get');
        return OLD;
      });
      repository.replace.mockImplementation(async () => {
        order.push('replace');
      });

      await service.replace(NEW);

      expect(order).toEqual(['get', 'replace']);
    });

    it('should stamp the write with the clock', async () => {
      const { repository, service } = setup();

      await service.replace(NEW);

      expect(repository.replace).toHaveBeenCalledWith(NEW, NOW);
    });

    it('should not report a change when the write fails', async () => {
      const { repository, service } = setup();
      repository.replace.mockRejectedValue(new Error('mongo down'));

      await expect(service.replace(NEW)).rejects.toThrow('mongo down');
    });
  });
});
