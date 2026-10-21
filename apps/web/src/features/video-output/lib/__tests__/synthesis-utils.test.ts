import { describe, it, expect } from 'vitest';
import type { VIEResponseMeta } from '@vie/types';
import { buildSynthesisFromMeta } from '../synthesis-utils';

// A stored doc's meta before the hero rewrite (output-quality 2.2).
const OLD_META: VIEResponseMeta = {
  videoId: 'vDjW_dRyKXY',
  videoTitle: 'Rails World 2026 Opening Keynote',
  creator: 'Ruby on Rails',
  contentTags: ['learning'],
  modifiers: [],
  primaryTag: 'learning',
  userGoal: 'understand the keynote',
  tldr: 'Agents make hand-written code obsolete.',
  keyTakeaways: ['37signals has gone pencils down [20:45]'],
  masterSummary: 'In the Rails World 2026 opening keynote, DHH…',
  seoDescription: 'DHH on agents.',
};

const NEW_META: VIEResponseMeta = {
  ...OLD_META,
  brief: 'DHH opens with his great-great-grandfather.',
  masterSummary: 'DHH opens with his great-great-grandfather.',
  worthItFor: 'Engineering leads deciding how far to let agents in.',
  skipIf: 'You want a how-to: no code is written on stage.',
  readingMinutes: 5,
};

describe('buildSynthesisFromMeta', () => {
  it('should carry the hero rewrite fields when the stored meta has them', () => {
    expect(buildSynthesisFromMeta(NEW_META)).toMatchObject({
      brief: NEW_META.brief,
      worthItFor: NEW_META.worthItFor,
      skipIf: NEW_META.skipIf,
      readingMinutes: 5,
    });
  });

  it('should rebuild an older row exactly as before when it has no hero rewrite fields', () => {
    expect(buildSynthesisFromMeta(OLD_META)).toEqual({
      tldr: OLD_META.tldr,
      keyTakeaways: OLD_META.keyTakeaways,
      masterSummary: OLD_META.masterSummary,
      seoDescription: OLD_META.seoDescription,
    });
  });

  it('should drop empty hero lines when the rewrite wrote none', () => {
    const synthesis = buildSynthesisFromMeta({ ...NEW_META, worthItFor: '', skipIf: '' });

    expect(synthesis).not.toHaveProperty('worthItFor');
    expect(synthesis).not.toHaveProperty('skipIf');
  });

  it('should drop a reading time that is not a positive number', () => {
    expect(buildSynthesisFromMeta({ ...NEW_META, readingMinutes: 0 })).not.toHaveProperty('readingMinutes');
  });

  it('should return null when the meta carries no synthesis at all', () => {
    expect(buildSynthesisFromMeta({ ...OLD_META, tldr: undefined, masterSummary: undefined })).toBeNull();
  });
});
