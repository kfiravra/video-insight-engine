import { describe, it, expect, beforeEach } from 'vitest';
import { getTestDb } from '../../test/setup.js';
import { LlmUsageRepository } from '../llm-usage.repository.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const CUTOFF = new Date('2026-10-01T00:00:00Z');
const BEFORE = new Date(CUTOFF.getTime() - DAY_MS);
const AFTER = new Date(CUTOFF.getTime() + DAY_MS);

describe('LlmUsageRepository', () => {
  let repo: LlmUsageRepository;
  const rows = () => getTestDb().collection('llm_usage');

  beforeEach(() => {
    repo = new LlmUsageRepository(getTestDb());
  });

  describe('scrubPersonalDataBefore', () => {
    it('should unset user_id, request_id and prompt_preview on rows before the cutoff', async () => {
      await rows().insertOne({ service: 'summarizer', user_id: 'u', request_id: 'r', prompt_preview: 'p', cost_usd: 1, timestamp: BEFORE });

      await repo.scrubPersonalDataBefore(CUTOFF, 100);

      const row = await rows().findOne({});
      expect([row?.user_id, row?.request_id, row?.prompt_preview]).toEqual([undefined, undefined, undefined]);
    });

    it('should keep cost, model, service, timestamp and prompt_hash on a cleared row', async () => {
      await rows().insertOne({ service: 'assistant', model: 'm', user_id: 'u', prompt_preview: 'p', prompt_hash: 'h', cost_usd: 1, timestamp: BEFORE });

      await repo.scrubPersonalDataBefore(CUTOFF, 100);

      expect(await rows().findOne({})).toMatchObject({ service: 'assistant', model: 'm', prompt_hash: 'h', cost_usd: 1, timestamp: BEFORE });
    });

    it('should leave rows from the cutoff on untouched', async () => {
      await rows().insertOne({ user_id: 'u', request_id: 'r', prompt_preview: 'p', timestamp: AFTER });

      const changed = await repo.scrubPersonalDataBefore(CUTOFF, 100);

      expect([changed, (await rows().findOne({}))?.prompt_preview]).toEqual([0, 'p']);
    });

    it('should match an old row that only has a prompt_preview', async () => {
      await rows().insertOne({ service: 'summarizer', prompt_preview: 'p', timestamp: BEFORE });

      expect(await repo.scrubPersonalDataBefore(CUTOFF, 100)).toBe(1);
    });

    it('should clear an empty-string prompt_preview field as well', async () => {
      await rows().insertOne({ service: 'assistant', prompt_preview: '', timestamp: BEFORE });

      await repo.scrubPersonalDataBefore(CUTOFF, 100);

      expect(await rows().findOne({ prompt_preview: { $exists: true } })).toBeNull();
    });

    it('should stop at the limit and finish on the next call', async () => {
      await rows().insertMany([1, 2, 3].map((n) => ({ user_id: `u${n}`, timestamp: BEFORE })));

      const calls = [await repo.scrubPersonalDataBefore(CUTOFF, 2), await repo.scrubPersonalDataBefore(CUTOFF, 2), await repo.scrubPersonalDataBefore(CUTOFF, 2)];

      expect(calls).toEqual([2, 1, 0]);
    });
  });

  describe('countPromptPreviews', () => {
    it('should count only the service rows that hold prompt text', async () => {
      await rows().insertMany([
        { service: 'assistant', prompt_preview: 'a question' },
        { service: 'assistant', prompt_preview: 'another' },
        { service: 'assistant', prompt_preview: '' },
        { service: 'assistant', prompt_preview: null },
        { service: 'assistant' },
        { service: 'summarizer', prompt_preview: 'Summarize this' },
      ]);

      expect(await repo.countPromptPreviews('assistant')).toBe(2);
    });
  });

  describe('unsetPromptPreviews', () => {
    beforeEach(async () => {
      await rows().insertMany([
        { service: 'assistant', prompt_preview: 'a question', prompt_hash: 'h1', cost_usd: 0.1 },
        { service: 'assistant', prompt_preview: '', cost_usd: 0.2 },
        { service: 'summarizer', prompt_preview: 'Summarize this', cost_usd: 0.3 },
      ]);
    });

    it('should remove the text from the service rows that hold it and report how many', async () => {
      const changed = await repo.unsetPromptPreviews('assistant');

      expect([changed, await repo.countPromptPreviews('assistant')]).toEqual([1, 0]);
    });

    it('should keep prompt_hash and cost on the row it clears', async () => {
      await repo.unsetPromptPreviews('assistant');

      expect(await rows().findOne({ prompt_hash: 'h1' })).toMatchObject({ cost_usd: 0.1 });
    });

    it('should leave other services alone', async () => {
      await repo.unsetPromptPreviews('assistant');

      expect((await rows().findOne({ service: 'summarizer' }))?.prompt_preview).toBe('Summarize this');
    });

    it('should change nothing on a second call', async () => {
      await repo.unsetPromptPreviews('assistant');

      expect(await repo.unsetPromptPreviews('assistant')).toBe(0);
    });
  });
});
