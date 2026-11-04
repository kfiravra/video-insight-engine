import { describe, it, expect } from 'vitest';
import { getLabels } from '../i18n';
import type { PluralForms } from '../i18n';

const LANGUAGES = ['en', 'he', 'ar'] as const;
// Legitimately identical to English: a bare number template, the plan name.
const SAME_AS_ENGLISH = new Set(['approxTokens', 'planPro', 'proCardTitle', 'duration']);
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
    const { tokens, tokensOblique, minutes, minutesOblique, videos, videosOblique, months } = getLabels(language).billing;

    expect([tokens, tokensOblique, minutes, minutesOblique, videos, videosOblique, months].map(isPluralForms)).toEqual(
      Array(7).fill(true),
    );
  });

  it.each(['he', 'ar'] as const)('should translate every %s billing label away from English', (language) => {
    const labels = getLabels(language).billing as unknown as Record<string, unknown>;
    const sameAsEnglish = Object.entries(en)
      .filter(([key]) => !SAME_AS_ENGLISH.has(key))
      .filter(([key, value]) => JSON.stringify(labels[key]) === JSON.stringify(value))
      .map(([key]) => key);

    expect(sameAsEnglish).toEqual([]);
  });

  it.each(['he', 'ar'] as const)('should keep every %s plural form non-empty', (language) => {
    const labels = getLabels(language).billing as unknown as Record<string, unknown>;
    const empty = Object.entries(labels)
      .filter(([, value]) => isPluralForms(value))
      .flatMap(([key, forms]) =>
        Object.entries(forms as PluralForms)
          .filter(([, text]) => !text || !text.trim())
          .map(([form]) => `${key}.${form}`),
      );

    expect(empty).toEqual([]);
  });

  it('should count Hebrew tokens with one, two and other forms', () => {
    expect(getLabels('he').billing.tokens).toEqual({
      one: 'טוקן אחד',
      two: 'שני טוקנים',
      other: '{count} טוקנים',
    });
  });

  it('should put the Arabic token dual in the oblique case after a preposition', () => {
    const { tokens, tokensOblique } = getLabels('ar').billing;

    expect([tokens.two, tokensOblique.two]).toEqual(['توكنان', 'توكنين']);
  });

  it('should give Arabic tokens a form for every plural category', () => {
    const { tokens, tokensOblique } = getLabels('ar').billing;

    expect([tokens, tokensOblique].map((forms) => Object.keys(forms).sort())).toEqual(
      Array(2).fill(['few', 'many', 'one', 'other', 'two', 'zero']),
    );
  });

  it('should say in Arabic that the video needs tokens', () => {
    expect(getLabels('ar').billing.calloutNeeds).toContain('يحتاج');
  });

  it('should keep the Latin Pro name untouched in he and ar', () => {
    expect([getLabels('he').billing.proCardTitle, getLabels('ar').billing.proCardTitle]).toEqual(['Pro', 'Pro']);
  });

  it('should word the team note exactly as the product asks', () => {
    expect(en.teamNote).toBe('Tokens are added by the team for now.');
  });

  it('should word the payments placeholder exactly as the product asks', () => {
    expect(en.paymentsPlaceholder).toBe("Payments aren't live yet — tokens are added by the team.");
  });

  it('should count English tokens with one and other forms', () => {
    expect(en.tokens).toEqual({ one: '{count} token', other: '{count} tokens' });
  });

  it.each(LANGUAGES)('should leave the minute and free-video era behind in %s', (language) => {
    const keys = Object.keys(getLabels(language).billing);

    expect(keys.filter((key) => /^freeVideo|^planMinutes|ChatLimit|^planFreeNote|^calloutFree/.test(key))).toEqual([]);
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
      const { tokens, minutes, videos, tokensOblique, minutesOblique, videosOblique } = getLabels(language).billing;

      expect([tokensOblique, minutesOblique, videosOblique]).toEqual([tokens, minutes, videos]);
    });
  });
});
