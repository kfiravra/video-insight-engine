import { describe, it, expect } from 'vitest';
import { buildSynthesisMerge } from '../synthesis-merge.js';

const memoryPartial = { tldr: 'Memory tldr', keyTakeaways: ['Memory point [0:42]'] };
const heroSuperset = {
  tldr: 'Hand-written code no longer pays.',
  keyTakeaways: ['Agents write the code now. [1:00]'],
  masterSummary: 'It opens on a bet. It ends on a promise.',
  seoDescription: 'A keynote on agents writing the code.',
  brief: 'It opens on a bet. It ends on a promise.',
  worthItFor: 'Leads deciding how far to let agents in.',
  skipIf: 'You want a how-to: no code is written on stage.',
  readingMinutes: 5,
};

describe('buildSynthesisMerge', () => {
  it('should set every hero field when the hero superset arrives', () => {
    const merge = buildSynthesisMerge(heroSuperset);

    expect(merge?.set).toEqual({
      'synthesis.tldr': heroSuperset.tldr,
      'synthesis.keyTakeaways': heroSuperset.keyTakeaways,
      'synthesis.masterSummary': heroSuperset.masterSummary,
      'synthesis.seoDescription': heroSuperset.seoDescription,
      'synthesis.brief': heroSuperset.brief,
      'synthesis.worthItFor': heroSuperset.worthItFor,
      'synthesis.skipIf': heroSuperset.skipIf,
      'synthesis.readingMinutes': 5,
    });
  });

  it('should land the hero superset unguarded because it carries a masterSummary', () => {
    expect(buildSynthesisMerge(heroSuperset)?.guard).toEqual({});
  });

  it('should not blank a hero line an earlier emission filled when a later one leaves it empty', () => {
    const merge = buildSynthesisMerge({ ...heroSuperset, worthItFor: '' });

    expect(merge?.set).not.toHaveProperty('synthesis.worthItFor');
  });

  it('should keep guarding the memory-done partial when it arrives', () => {
    expect(buildSynthesisMerge(memoryPartial)?.guard).toEqual({ 'synthesis.masterSummary': { $in: [null, ''] } });
  });
});
