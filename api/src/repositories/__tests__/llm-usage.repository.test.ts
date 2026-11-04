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

    it('should unset the run, trace and session ids on rows before the cutoff', async () => {
      await rows().insertOne({ service: 'assistant', run_id: 'run', trace_id: 't'.repeat(32), session_id: 's', cost_usd: 1, timestamp: BEFORE });

      expect(await repo.scrubPersonalDataBefore(CUTOFF, 100)).toBe(1);

      const row = await rows().findOne({});
      expect([row?.run_id, row?.trace_id, row?.session_id]).toEqual([undefined, undefined, undefined]);
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

  describe('listAssistantRequestCosts', () => {
    const NOW = new Date('2026-11-01T10:00:00Z');
    const ago = (minutes: number) => new Date(NOW.getTime() - minutes * 60 * 1000);
    const SINCE = ago(24 * 60);
    const QUIET_BEFORE = ago(10);
    const row = (fields: Record<string, unknown>) => ({
      service: 'assistant', request_id: 'req-a', user_id: 'u-1', cost_usd: 0.01, feature: 'assistant:rag_chat', timestamp: ago(30), ...fields,
    });

    it('should sum each finished request\'s rows, oldest request first', async () => {
      await rows().insertMany([
        row({ cost_usd: 0.01, timestamp: ago(40) }),
        row({ cost_usd: 0.02, timestamp: ago(35), feature: 'assistant:tool:search' }),
        row({ request_id: 'req-b', user_id: 'u-2', cost_usd: 0.05, feature: 'assistant:library_chat', timestamp: ago(50) }),
      ]);

      const requests = await repo.listAssistantRequestCosts(SINCE, QUIET_BEFORE, 10);

      expect(requests.map((r) => [r.requestId, r.userId, r.calls, r.lastAt])).toEqual([
        ['req-b', 'u-2', 1, ago(50)],
        ['req-a', 'u-1', 2, ago(35)],
      ]);
      expect(requests[1].costUsd).toBeCloseTo(0.03, 10);
      expect(requests[0].feature).toBe('assistant:library_chat');
    });

    it('should leave out a request with a row inside the quiet window, the whole request', async () => {
      await rows().insertMany([row({ timestamp: ago(30) }), row({ timestamp: ago(5) })]);

      expect(await repo.listAssistantRequestCosts(SINCE, QUIET_BEFORE, 10)).toEqual([]);
    });

    it.each([
      ['another service', { service: 'summarizer' }],
      ['no cost', { cost_usd: 0 }],
      ['a null cost', { cost_usd: null }],
      ['no request id', { request_id: null }],
      ['no user (scrubbed)', { user_id: null }],
      ['rows older than the lookback', { timestamp: ago(25 * 60) }],
    ])('should leave out %s', async (_label, fields) => {
      await rows().insertOne(row(fields));

      expect(await repo.listAssistantRequestCosts(SINCE, QUIET_BEFORE, 10)).toEqual([]);
    });

    it('should stop at the limit', async () => {
      await rows().insertMany(['r1', 'r2', 'r3'].map((id, i) => row({ request_id: id, timestamp: ago(60 - i) })));

      expect((await repo.listAssistantRequestCosts(SINCE, QUIET_BEFORE, 2)).map((r) => r.requestId)).toEqual(['r1', 'r2']);
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
