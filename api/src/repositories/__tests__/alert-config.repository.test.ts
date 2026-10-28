import { describe, it, expect, beforeEach } from 'vitest';

import { getTestDb } from '../../test/setup.js';
import { AlertConfigRepository, DEFAULT_ALERT_CONFIG } from '../alert-config.repository.js';

const NOW = new Date('2026-10-28T12:00:00Z');
const NEW = { cost_threshold_usd: 1.5, daily_spike_multiplier: 3, failure_rate_threshold: 0.1 };

describe('AlertConfigRepository', () => {
  let repository: AlertConfigRepository;

  const stored = () => getTestDb().collection('llm_alert_config').findOne({ _id: 'default' as never });

  beforeEach(async () => {
    await getTestDb().collection('llm_alert_config').deleteMany({});
    repository = new AlertConfigRepository(getTestDb());
  });

  describe('get', () => {
    it('should return the defaults when nothing has been saved', async () => {
      expect(await repository.get()).toEqual(DEFAULT_ALERT_CONFIG);
    });

    it('should return a saved document over the defaults', async () => {
      await repository.replace(NEW, NOW);

      expect(await repository.get()).toEqual(NEW);
    });

    it('should fill a field a legacy document lacks from the defaults', async () => {
      await getTestDb().collection('llm_alert_config').insertOne({ _id: 'default' as never, cost_threshold_usd: 9 });

      expect(await repository.get()).toEqual({ ...DEFAULT_ALERT_CONFIG, cost_threshold_usd: 9 });
    });

    it('should return the same thresholds the vie-admin evaluator defaults to', () => {
      // services/admin/src/services/alert_evaluator.py DEFAULT_CONFIG
      expect(DEFAULT_ALERT_CONFIG).toEqual({ cost_threshold_usd: 0.5, daily_spike_multiplier: 2.0, failure_rate_threshold: 0.2 });
    });
  });

  describe('replace', () => {
    it('should store the document vie-admin reads: id "default" and snake_case fields', async () => {
      await repository.replace(NEW, NOW);

      expect(await stored()).toEqual({ _id: 'default', ...NEW, updated_at: NOW });
    });

    it('should overwrite the previous thresholds without leaving a second document', async () => {
      await repository.replace(NEW, NOW);
      await repository.replace({ ...NEW, cost_threshold_usd: 2 }, new Date(NOW.getTime() + 1000));

      expect(await getTestDb().collection('llm_alert_config').countDocuments({})).toBe(1);
      expect((await repository.get()).cost_threshold_usd).toBe(2);
    });

    it('should drop a stray field from a previous document', async () => {
      await getTestDb().collection('llm_alert_config').insertOne({ _id: 'default' as never, stray: true, ...NEW });

      await repository.replace(NEW, NOW);

      expect(await stored()).not.toHaveProperty('stray');
    });
  });
});
