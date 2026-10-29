import { describe, it, expect } from 'vitest';
import { getLabels } from '../i18n';
import type { PluralForms } from '../i18n';

const LANGUAGES = ['en', 'he', 'ar'] as const;
const PLACEHOLDER = /\{(\w+)\}/g;

function placeholders(text: string): string[] {
  return [...text.matchAll(PLACEHOLDER)].map((m) => m[1]).sort();
}

function isPluralForms(value: unknown): value is PluralForms {
  return typeof value === 'object' && value !== null && 'other' in value;
}

describe('billing labels', () => {
  const en = getLabels('en').billing;

  it.each(['he', 'ar'] as const)('should fill every English billing key in %s', (language) => {
    expect(Object.keys(getLabels(language).billing).sort()).toEqual(Object.keys(en).sort());
  });

  it.each(LANGUAGES)('should keep a non-empty string in every %s billing label', (language) => {
    const labels = getLabels(language).billing;
    const empty = Object.entries(labels).filter(
      ([, value]) => typeof value === 'string' && value.trim() === '',
    );

    expect(empty).toEqual([]);
  });

  it.each(['he', 'ar'] as const)(
    'should interpolate the same placeholders in %s as in English',
    (language) => {
      const labels = getLabels(language).billing;
      const mismatched = Object.entries(en)
        .filter(([, value]) => typeof value === 'string')
        .filter(([key, value]) => {
          const translated = (labels as unknown as Record<string, string>)[key];
          return placeholders(translated).join() !== placeholders(value as string).join();
        })
        .map(([key]) => key);

      expect(mismatched).toEqual([]);
    },
  );

  it.each(LANGUAGES)('should give %s plural forms an other fallback', (language) => {
    const labels = getLabels(language).billing;

    expect([isPluralForms(labels.minutes), isPluralForms(labels.videos)]).toEqual([true, true]);
  });

  it('should word the team note exactly as the product asks', () => {
    expect(en.teamNote).toBe('Minutes are added by the team for now.');
  });

  describe('wording fixes from the review', () => {
    it('should say a Hebrew credit lot is valid until its date, not that it expired', () => {
      expect(getLabels('he').billing.lotExpires).toBe('בתוקף עד {date}');
    });

    it('should not say "expired" in the Hebrew lot label in the past tense', () => {
      expect(getLabels('he').billing.lotExpires).not.toContain('פג');
    });

    it('should put the Arabic dual in the oblique case after a preposition', () => {
      const { minutes, minutesOblique } = getLabels('ar').billing;

      expect([minutes.two, minutesOblique.two]).toEqual(['دقيقتان', 'دقيقتين']);
    });

    it('should put the Arabic video dual in the oblique case after a preposition', () => {
      const { videos, videosOblique } = getLabels('ar').billing;

      expect([videos.two, videosOblique.two]).toEqual(['فيديوهان', 'فيديوهين']);
    });

    it.each(['en', 'he'] as const)('should keep the oblique plural forms the same as the plain ones in %s', (language) => {
      const { minutes, videos, minutesOblique, videosOblique } = getLabels(language).billing;

      expect([minutesOblique, videosOblique]).toEqual([minutes, videos]);
    });

    it('should not use an adjective that has to agree with the Arabic number in "you have … to spend"', () => {
      expect(getLabels('ar').billing.calloutNeeds).toContain('للاستخدام');
    });

    it('should give the plural free-video note an other fallback in every language', () => {
      expect(['en', 'he', 'ar'].map((language) => typeof getLabels(language).billing.planFreeNoteMany.other)).toEqual([
        'string',
        'string',
        'string',
      ]);
    });
  });
});
